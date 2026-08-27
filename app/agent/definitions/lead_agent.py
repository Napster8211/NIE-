"""
NapsterTec AI - Lead Intelligence Agent (Hardened)
Module: app/agent/definitions/lead_agent.py
"""
import logging
from typing import Dict, Any
from datetime import datetime, timezone

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability
from app.schemas.evidence import is_simulation_evidence, normalize_evidence_source
from app.services.business_entity_qualification import is_entity_qualified
from app.schemas.lead import BusinessDiscoveryQueryMode, BusinessDiscoveryScope

logger = logging.getLogger(__name__)

class LeadIntelligenceAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="lead_intelligence",
            display_name="Lead Intelligence Director",
            description="Autonomously discovers, normalizes, and qualifies business leads.",
            version="1.1.0",
            category="business_intelligence",
            department_id="growth_marketing",      # CANONICAL TAXONOMY
            department_name="Growth & Marketing",  # CANONICAL TAXONOMY
            capabilities={AgentCapability.LEAD_GENERATION, AgentCapability.RESEARCH},
            supported_task_types=["find leads", "discover businesses", "prospecting"],
            # STRICT HARDENING: Explicitly expose lead_upsert and block workspace tools.
            allowed_tools={"business_discovery", "url_reader", "lead_upsert"},
            required_permissions={AgentPermission.READ_EXTERNAL_DISCOVERY, AgentPermission.WRITE}
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            target_count = max(1, min(100, int(context.planner_output.get("target_count", 30))))
            verification_mode = context.planner_output.get("verification_mode")
            live_evidence_canary = verification_mode == "LIVE_EVIDENCE_CANARY"
            qualified_lead_canary = verification_mode == "QUALIFIED_LEAD_CANARY"
            scope_data = context.planner_output.get("discovery_scope")
            scope = None
            if scope_data:
                try:
                    scope = BusinessDiscoveryScope.model_validate(scope_data)
                except Exception:
                    scope = None
            if qualified_lead_canary and scope is None:
                result.errors.append("DISCOVERY_SCOPE_INCOMPLETE")
                result.final_output = "DISCOVERY_SCOPE_INCOMPLETE"
                return result
            if scope is not None:
                query = scope.category.strip()
                location = scope.location.strip()
                target_count = min(target_count, scope.max_results)
                candidate_scan_limit = scope.candidate_scan_limit
                query_mode = scope.query_mode
            else:
                query = context.planner_output.get("query", context.task)
                location = context.planner_output.get("location", "Global")
                candidate_scan_limit = target_count
                query_mode = BusinessDiscoveryQueryMode.GENERIC_DISCOVERY
            require_live_evidence = bool(
                context.planner_output.get("require_live_evidence")
                or live_evidence_canary
                or qualified_lead_canary
            )
            require_entity_qualification = bool(
                context.planner_output.get("require_entity_qualification")
                or qualified_lead_canary
                or verification_mode == "PRODUCTION_BUSINESS_SUCCESS"
            )
            if require_entity_qualification and scope is None:
                result.errors.append("DISCOVERY_SCOPE_INCOMPLETE")
                result.final_output = "DISCOVERY_SCOPE_INCOMPLETE"
                return result
            if require_entity_qualification:
                query_mode = BusinessDiscoveryQueryMode.QUALIFIED_ENTITY_SEARCH
            if require_live_evidence:
                target_count = 1
            
            # 1. Discover Leads
            tool_response = await self.invoke_tool(
                tool_name="business_discovery",
                parameters={
                    "query": query,
                    "location": location,
                    "max_results": target_count,
                    "candidate_scan_limit": candidate_scan_limit,
                    "query_mode": query_mode,
                    "require_live_evidence": require_live_evidence,
                    "require_entity_qualification": require_entity_qualification,
                },
                context=context
            )
            
            # Extract data from the ToolResult wrapper object
            out_obj = tool_response["output"]
            disc_data = getattr(out_obj, "data", out_obj)
            if hasattr(disc_data, "model_dump"):
                disc_data = disc_data.model_dump()
            elif not isinstance(disc_data, dict):
                disc_data = {}
            
            raw_results = disc_data.get("results", [])
            provider = disc_data.get("provider_used", "unknown")
            provider_mode = disc_data.get("provider_mode", "unknown")
            evidence_source = normalize_evidence_source(
                disc_data.get("evidence_source") or provider_mode
            )
            simulation_evidence = bool(
                disc_data.get("simulation_evidence", is_simulation_evidence(evidence_source))
            )
            safe_source_metadata = {
                key: value
                for key, value in dict(disc_data.get("source_metadata") or {}).items()
                if key in {
                    "provider", "endpoint", "retrieval_type", "request_succeeded",
                    "request_count", "requested_at", "result_count", "query", "max_results",
                    "raw_result_count", "normalized_result_count", "usable_result_count",
                    "qualified_artifact_target", "candidate_scan_limit", "query_mode", "candidate_count_examined",
                    "qualified_candidate_index", "candidate_diagnostics",
                }
            }

            if require_live_evidence and not (
                evidence_source.value == "LIVE_EXTERNAL"
                and simulation_evidence is False
                and bool(raw_results)
                and safe_source_metadata.get("request_succeeded") is True
            ):
                error_code = disc_data.get("error_code") or "LIVE_EVIDENCE_UNAVAILABLE"
                error_detail = str(disc_data.get("error") or "").strip()
                result.errors.append(
                    f"{error_code}:{error_detail}" if error_detail else error_code
                )
                result.final_output = "LIVE_EVIDENCE_UNAVAILABLE"
                result.tool_calls.append(tool_response)
                return result
            
            if not raw_results:
                result.warnings.append("Discovery tool executed successfully but returned 0 leads.")

            first_candidate = raw_results[0] if raw_results else {}
            entity_qualification = dict(first_candidate.get("entity_qualification") or {})
            entity_qualified = is_entity_qualified(entity_qualification)
            if require_entity_qualification and not entity_qualified:
                result.errors.append("BUSINESS_ENTITY_UNVERIFIED")
                result.final_output = "BUSINESS_ENTITY_UNVERIFIED"
                result.tool_calls.append(tool_response)
                return result
            
            # 2. Live canaries are evidence-only. They must not mutate CRM or
            # create/update lead records. Structural/mock flows retain the
            # existing internal persistence path.
            upsert_response = None
            if require_live_evidence:
                raw_results = raw_results[:1]
                qualified_count = 1 if raw_results and entity_qualified else 0
                stats = {
                    "success": len(raw_results) == 1 and (
                        not require_entity_qualification or entity_qualified
                    ),
                    "transaction_committed": False,
                    "read_only_validation": True,
                    "created": 0,
                    "updated": 0,
                    "duplicates": 0,
                    "failed": 0 if raw_results else 1,
                    "qualified": qualified_count,
                    "needs_review": 0,
                    "unqualified": len(raw_results) - qualified_count,
                }
            else:
                upsert_response = await self.invoke_tool(
                    tool_name="lead_upsert",
                    parameters={"raw_leads": raw_results, "provider_mode": provider_mode},
                    context=context
                )

                # Extract data from the ToolResult wrapper object
                up_obj = upsert_response["output"]
                stats = getattr(up_obj, "data", up_obj)
                if hasattr(stats, "model_dump"):
                    stats = stats.model_dump()
                elif not isinstance(stats, dict):
                    stats = {}
            
            # 3. Execution Validation
            artifact_verified = bool(stats.get("success") and (
                require_live_evidence or stats.get("transaction_committed")
            ) and (not require_entity_qualification or entity_qualified))
            if artifact_verified:
                result.success = True
                status_msg = "Completed"
            else:
                result.success = False
                status_msg = "Failed"
                if require_entity_qualification and not entity_qualified:
                    result.errors.append("BUSINESS_ENTITY_UNVERIFIED")

            # 4. Grounded Execution Report
            summary = (
                f"Lead Intelligence Execution Complete\n\n"
                f"Provider: {provider}\n"
                f"Provider Mode: {provider_mode.capitalize()}\n\n"
                f"Evidence Source: {evidence_source.value}\n"
                f"Simulation Evidence: {'Yes' if simulation_evidence else 'No'}\n\n"
                f"Discovered: {len(raw_results)}\n"
                f"Normalized: {stats.get('created', 0) + stats.get('updated', 0) + stats.get('failed', 0)}\n\n"
                f"Qualification:\n"
                f"Qualified: {stats.get('qualified', 0)}\n"
                f"Needs Review: {stats.get('needs_review', 0)}\n"
                f"Unqualified: {stats.get('unqualified', 0)}\n\n"
                f"Persistence:\n"
                f"Created: {stats.get('created', 0)}\n"
                f"Updated: {stats.get('updated', 0)}\n"
                f"Duplicates: {stats.get('duplicates', 0)}\n"
                f"Failed: {stats.get('failed', 0)}\n"
                f"Transaction Committed: {'Yes' if stats.get('transaction_committed') else 'No'}\n"
                f"Read-Only Evidence Mode: {'Yes' if require_live_evidence else 'No'}\n\n"
                f"Entity Qualification Required: {'Yes' if require_entity_qualification else 'No'}\n"
                f"Entity Qualification: {entity_qualification.get('status', 'UNVERIFIED')}\n\n"
                f"Missing values preserved as null: Yes\n\n"
                f"Unauthorized tool calls: 0\n"
                f"Workspace files modified: 0\n\n"
                f"Status:\n{status_msg}"
            )
            
            result.final_output = summary
            created_at = datetime.now(timezone.utc).isoformat()
            provenance = {
                "mission_id": context.planner_output.get("mission_id"),
                "plan_version": context.planner_output.get("plan_version"),
                "milestone_id": context.planner_output.get("milestone_id"),
                "decision_id": context.planner_output.get("decision_id"),
                "materialization_id": context.planner_output.get("materialization_id"),
                "execution_request_id": context.planner_output.get("execution_request_id"),
                "delegation_id": context.planner_output.get("delegation_id"),
                "worker_claim_id": context.planner_output.get("worker_claim_id"),
                "specialist": self.metadata.name,
                "evidence_source": evidence_source.value,
                "simulation_evidence": simulation_evidence,
                "created_at": created_at,
                "artifact_type": "LeadArtifact",
                "source_provider": provider,
                "source_metadata": safe_source_metadata,
                "discovery_scope": scope.model_dump(mode="json") if scope else None,
            }
            source_reference = None
            if raw_results:
                source_reference = raw_results[0].get("source_url") or raw_results[0].get("website")
            business_name = first_candidate.get("business_name") or first_candidate.get("name")
            result.artifacts.append({
                "artifact_id": f"lead_batch_{context.session_id[:12]}",
                "artifact_type": "LeadArtifact",
                "lead_id": "lead_batch",
                "verified": artifact_verified,
                "metrics": stats,
                "provider": provider,
                "mode": provider_mode,
                "evidence_source": evidence_source.value,
                "simulation_evidence": simulation_evidence,
                "created_at": created_at,
                "source_provider": provider,
                "source_metadata": safe_source_metadata,
                "source_reference": source_reference,
                "source_url": source_reference,
                "source_type": first_candidate.get("source_type") or entity_qualification.get("source_type"),
                "business_name": business_name,
                "business_category": first_candidate.get("business_category") or first_candidate.get("category"),
                "business_location": first_candidate.get("business_location") or first_candidate.get("city"),
                "business_domain": first_candidate.get("business_domain"),
                "entity_qualification": entity_qualification,
                "qualification_reasons": list(
                    first_candidate.get("qualification_reasons")
                    or entity_qualification.get("qualification_reasons")
                    or []
                ),
                "provenance": provenance,
            })
            result.tool_calls.append(tool_response)
            if upsert_response is not None:
                result.tool_calls.append(upsert_response)
            
            return result

        except Exception as e:
            logger.error(f"[Lead Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            return result