"""
NapsterTec AI - Opportunity Intelligence Engine
Module: app/services/opportunity_engine.py
"""
import uuid
from typing import Dict, Any
from app.schemas.shared_artifacts import OpportunityAgentContext, OpportunityArtifact, RecommendedService

class OpportunityEngine:
    def evaluate(self, context: OpportunityAgentContext, session_id: str) -> OpportunityArtifact:
        issues = []
        drivers = []
        services = []
        
        # 1. Deterministic Evaluation Rules
        if context.website_status in ["missing", "dns_failure", "timeout"]:
            issues.append("No functional digital presence.")
            drivers.append("Website is missing or unreachable.")
            services.append(RecommendedService(
                service_name="Full Website Build",
                evidence_chain=["Website Status is 'missing'"],
                confidence="High"
            ))
            level = "Very High"
            next_step = "Create Demo Website"
            
        else:
            level_score = 0
            
            if not context.seo_findings.get("description"):
                issues.append("Poor SEO Foundation (Missing Meta Description)")
                drivers.append("Lacks basic search engine visibility.")
                level_score += 1
                
            has_booking = any(sig.get("name") == "Online Booking Present" and sig.get("present") for sig in context.business_signals)
            if context.business_identity.get("category") == "Restaurant" and not has_booking:
                issues.append("No Reservation/Ordering System")
                drivers.append("Restaurant losing potential online revenue.")
                services.append(RecommendedService(
                    service_name="Reservation System Integration",
                    evidence_chain=["Category is Restaurant", "No Online Booking Present detected"],
                    confidence="High"
                ))
                level_score += 2

            tech_names = [t.get("name") for t in context.technology]
            if "Cloudflare" not in tech_names:
                services.append(RecommendedService(
                    service_name="Performance & Security Optimization",
                    evidence_chain=["No CDN detected in Tech Stack"],
                    confidence="Medium"
                ))
                level_score += 1

            if level_score >= 3:
                level = "High"
                next_step = "Generate Proposal"
            elif level_score > 0:
                level = "Medium"
                next_step = "Generate Website Audit Document"
            else:
                level = "Low"
                next_step = "No Action Required"

        if not services and level != "Very High":
            services.append(RecommendedService(
                service_name="SEO Optimization",
                evidence_chain=["General Web Audit"],
                confidence="Low"
            ))

        artifact_id = f"opp_{uuid.uuid4().hex[:8]}"

        return OpportunityArtifact(
            artifact_id=artifact_id,
            agent_run_id=session_id,
            lead_id=context.lead_id,
            opportunity_level=level,
            verified_issues=issues,
            business_signals=context.business_signals,
            opportunity_drivers=drivers,
            recommended_services=services,
            recommended_next_step=next_step,
            execution_metadata={"evaluation_method": "Deterministic Engine"}
        )