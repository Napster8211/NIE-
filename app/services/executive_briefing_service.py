"""
NapsterTec AI - Canonical Executive Briefing Engine
Module: app/services/executive_briefing_service.py
"""
import logging
from typing import List, Optional
from datetime import datetime, timezone

from app.schemas.executive_briefing import (
    ExecutiveBriefing, ExecutiveBriefingType, ExecutiveBriefingSection, ExecutiveBriefingFact
)
from app.services.executive_state_service import ExecutiveStateService

logger = logging.getLogger(__name__)

class ExecutiveBriefingService:
    def __init__(self):
        # The Briefing Engine relies strictly on the verified Executive State projection layer
        self.state_service = ExecutiveStateService()

    def generate_company_status_briefing(self) -> ExecutiveBriefing:
        """Generates the primary 'Give me an update' executive briefing with deduplication."""
        state = self.state_service.get_bootstrap_state()
        overview = state.overview
        
        # SPRINT 6F: Deduplicate Owner Actions for Spoken Narrative & Section Presentation
        unique_actions = {}
        for act in state.owner_actions:
            unique_actions[act.summary] = unique_actions.get(act.summary, 0) + 1
                
        unique_action_count = len(unique_actions)
        total_actions = overview.owner_actions_required

        facts = [
            ExecutiveBriefingFact(fact_type="ACTIVE_OBJECTIVES", label="Active Objectives", value=overview.active_objectives, source_type="EXECUTIVE_STATE"),
            ExecutiveBriefingFact(fact_type="ACTIVE_MISSIONS", label="Active Missions", value=overview.active_missions, source_type="EXECUTIVE_STATE"),
            ExecutiveBriefingFact(fact_type="PENDING_APPROVALS", label="Pending Approvals", value=overview.pending_approvals, source_type="EXECUTIVE_STATE"),
            ExecutiveBriefingFact(fact_type="OWNER_ACTIONS", label="Owner Actions Required", value=total_actions, source_type="EXECUTIVE_STATE"),
            ExecutiveBriefingFact(fact_type="BLOCKED_MISSIONS", label="Blocked Missions", value=overview.blocked_missions, source_type="EXECUTIVE_STATE"),
            ExecutiveBriefingFact(fact_type="FINANCIAL_STATUS", label="Financial Health", value=overview.financial_status, source_type="EXECUTIVE_STATE")
        ]

        sections = []
        requires_attention = total_actions > 0
        
        # Section 1: Executive Summary
        summary_content = (
            f"Director Intelligence is {state.director.status.lower()}. "
            f"There are currently {overview.active_objectives} active objectives and {overview.active_missions} running missions. "
        )
        if requires_attention:
            summary_content += f"{total_actions} actions require executive authorization."
            
        sections.append(ExecutiveBriefingSection(
            heading="Executive Summary",
            content=summary_content,
            facts=facts
        ))

        # Section 2: Owner Action Details (Aggregated UI Presentation)
        if requires_attention:
            action_list = "\n".join([f"- {summary} ({count} pending records)" for summary, count in unique_actions.items()])
            sections.append(ExecutiveBriefingSection(
                heading="Required Authorizations",
                content=f"The following unique threads are blocked pending your decision:\n{action_list}",
                priority="HIGH",
                severity="HIGH"
            ))

        # Build TTS-safe Speech Text (Deduplicated)
        speech_text = f"Good {self._get_time_greeting()}. The Intelligence Engine is currently {state.director.status.lower()}. "
        if requires_attention:
            speech_text += f"There are {total_actions} pending authorization records involving {unique_action_count} unique actions requiring your attention. "
        else:
            speech_text += "All systems are operational with no pending blocks. "
            
        if overview.active_objectives > 0:
            speech_text += f"We are tracking {overview.active_objectives} active objectives across {overview.active_missions} running missions."
        else:
            speech_text += "No strategic objectives are currently active."

        return ExecutiveBriefing(
            briefing_type=ExecutiveBriefingType.COMPANY_STATUS,
            title="Company Operating Status",
            summary=summary_content,
            speech_text=speech_text,
            sections=sections,
            priority="HIGH" if requires_attention else "NORMAL",
            severity="HIGH" if requires_attention else "NORMAL",
            requires_owner_attention=requires_attention
        )

    def generate_daily_briefing(self) -> ExecutiveBriefing:
        """Alias for standard daily rollup (currently proxies to COMPANY_STATUS logic)."""
        briefing = self.generate_company_status_briefing()
        briefing.briefing_type = ExecutiveBriefingType.DAILY
        briefing.title = "Daily Executive Briefing"
        return briefing

    def generate_objective_briefing(self, objective_id: str) -> ExecutiveBriefing:
        obj = self.state_service.get_objective_detail(objective_id)
        if not obj:
            raise ValueError(f"OBJECTIVE_NOT_FOUND: {objective_id}")
            
        facts = [
            ExecutiveBriefingFact(fact_type="STATUS", label="Status", value=obj.status, source_type="OBJECTIVE_REPOSITORY", source_id=obj.objective_id),
            ExecutiveBriefingFact(fact_type="PROGRESS", label="Progress", value=obj.progress_percentage, source_type="OBJECTIVE_REPOSITORY", source_id=obj.objective_id),
            ExecutiveBriefingFact(fact_type="MISSIONS", label="Linked Missions", value=obj.mission_count, source_type="OBJECTIVE_REPOSITORY", source_id=obj.objective_id)
        ]
        
        sections = [
            ExecutiveBriefingSection(
                heading="Objective Status",
                content=f"Objective '{obj.title}' is {obj.status} with {obj.progress_percentage:.0f}% verified progress.",
                facts=facts
            )
        ]

        speech_text = f"Objective {self._clean_for_speech(obj.title)} is currently {obj.status}. We have verified {obj.progress_percentage:.0f} percent completion across {obj.mission_count} active missions."
        if obj.owner_action_required:
            speech_text += " This objective currently requires executive intervention."

        return ExecutiveBriefing(
            briefing_type=ExecutiveBriefingType.OBJECTIVE,
            title=f"Objective Briefing: {obj.title}",
            summary=obj.description,
            speech_text=speech_text,
            sections=sections,
            objective_id=obj.objective_id,
            requires_owner_attention=obj.owner_action_required
        )

    def generate_department_briefing(self, department_id: str) -> ExecutiveBriefing:
        departments = self.state_service.list_departments()
        dept = next((d for d in departments if d.department_id == department_id), None)
        if not dept:
            raise ValueError(f"DEPARTMENT_NOT_FOUND: {department_id}")
            
        sections = [
            ExecutiveBriefingSection(
                heading="Department Overview",
                content=f"{dept.department_name} has {dept.active_agent_count} of {dept.agent_count} specialist agents currently active.",
                facts=[
                    ExecutiveBriefingFact(fact_type="AGENT_COUNT", label="Total Agents", value=dept.agent_count, source_type="ORGANIZATION_MODEL"),
                    ExecutiveBriefingFact(fact_type="ACTIVE_AGENTS", label="Active Agents", value=dept.active_agent_count, source_type="ORGANIZATION_MODEL")
                ]
            )
        ]

        speech_text = f"The {self._clean_for_speech(dept.department_name)} department is {dept.status.lower()}. {dept.active_agent_count} out of {dept.agent_count} specialist agents are actively executing missions."

        return ExecutiveBriefing(
            briefing_type=ExecutiveBriefingType.DEPARTMENT,
            title=f"Department Report: {dept.department_name}",
            summary=f"Overview of {dept.department_name} activity.",
            speech_text=speech_text,
            sections=sections,
            department_id=dept.department_id
        )

    def generate_finance_briefing(self) -> ExecutiveBriefing:
        summary = self.state_service._get_finance_summary()
        
        facts = [
            ExecutiveBriefingFact(fact_type="BUDGET", label="Total Budget", value=summary.budget_limit, source_type="FINANCE_ENGINE"),
            ExecutiveBriefingFact(fact_type="SPENT", label="Spent", value=summary.spent, source_type="FINANCE_ENGINE"),
            ExecutiveBriefingFact(fact_type="AVAILABLE", label="Available", value=summary.available, source_type="FINANCE_ENGINE")
        ]
        
        sections = [
            ExecutiveBriefingSection(
                heading="Financial Overview",
                content=f"Financial health is {summary.financial_status}. We have utilized {summary.spent} {summary.currency} of the {summary.budget_limit} {summary.currency} budget.",
                facts=facts
            )
        ]

        speech_text = f"The financial status is {summary.financial_status.lower()}. We have utilized {summary.spent} {summary.currency} with {summary.available} {summary.currency} remaining in available allocation."
        if summary.blocked or summary.requires_owner_action:
            speech_text += " There is a blocked financial commitment awaiting your authorization."

        return ExecutiveBriefing(
            briefing_type=ExecutiveBriefingType.FINANCE,
            title="Financial Health Briefing",
            summary=f"Financial overview. Status: {summary.financial_status}",
            speech_text=speech_text,
            sections=sections,
            requires_owner_attention=summary.requires_owner_action
        )

    # --- Helpers ---
    def _get_time_greeting(self) -> str:
        hour = datetime.now().hour
        if hour < 12: return "morning"
        if hour < 17: return "afternoon"
        return "evening"
        
    def _clean_for_speech(self, text: str) -> str:
        return text.replace("&", "and").replace("_", " ")

executive_briefing_service = ExecutiveBriefingService()