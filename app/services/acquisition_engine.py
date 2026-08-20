"""
NapsterTec AI - Client Acquisition Engine
Module: app/services/acquisition_engine.py
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, Any
from app.schemas.shared_artifacts import (
    AcquisitionAgentContext, ClientAcquisitionArtifact, ContactValidation,
    ChannelStrategy, PersonalizationSummary, CRMStatus
)

class AcquisitionEngine:
    def prepare_acquisition(self, context: AcquisitionAgentContext, session_id: str) -> ClientAcquisitionArtifact:
        
        # 1. Contact Validation
        missing = []
        if not context.email: missing.append("Email Address")
        if not context.phone: missing.append("Phone Number")
        
        contact_val = ContactValidation(
            has_website=bool(context.website),
            has_email=bool(context.email),
            has_phone=bool(context.phone),
            has_social=True, # Assuming fallback social presence
            missing_critical=missing
        )

        # 2. Channel Strategy (Deterministic)
        primary = "Professional Email"
        reason = "Standard B2B outreach protocol preferred for proposals."
        secondary = "LinkedIn"
        
        if context.category.lower() in ["restaurant", "retail"] and context.phone:
            primary = "WhatsApp Business"
            reason = "High engagement rate for hyper-local retail/hospitality businesses."
            secondary = "Professional Email"

        channels = ChannelStrategy(primary_channel=primary, secondary_channel=secondary, reasoning=reason)

        # 3. Personalization Engine
        issues_str = ", ".join(context.verified_issues[:2])
        val_prop = f"We noticed {issues_str}. We built a custom {context.solution_type} to automate these workflows."
        
        personalization = PersonalizationSummary(
            business_name=context.business_name,
            industry=context.category,
            verified_pain_points=context.verified_issues,
            value_proposition=val_prop,
            demo_url=context.preview_url
        )

        # 4. CRM Automation
        crm = CRMStatus(
            current_stage="Demo Ready - Awaiting CTO Approval",
            previous_stage="Solution Deployed",
            last_updated=datetime.now(timezone.utc).isoformat(),
            next_action="Review Artifact and Approve Outreach"
        )

        # 5. Follow-up Strategy
        follow_ups = [
            "Day 1: Initial Pitch + Demo Link",
            "Day 3: Value Add (ROI highlights)",
            "Day 7: Final Check-in + Alternative Availability"
        ]

        artifact_id = f"acq_{uuid.uuid4().hex[:8]}"

        return ClientAcquisitionArtifact(
            artifact_id=artifact_id,
            agent_run_id=session_id,
            lead_id=context.lead_id,
            contact_validation=contact_val,
            channel_strategy=channels,
            personalization_summary=personalization,
            crm_status=crm,
            follow_up_strategy=follow_ups,
            approval_required=True,
            execution_metadata={"evaluation_method": "Deterministic CRM Processing"}
        )