"""
NapsterTec AI - Solution Engine
Module: app/services/solution_engine.py
"""
import uuid
from typing import Dict, Any
from app.schemas.shared_artifacts import SolutionAgentContext, BusinessSolutionArtifact, SolutionModule, SolutionFeature, SolutionIntegration, ImplementationPhase

class SolutionEngine:
    def design_blueprint(self, context: SolutionAgentContext, session_id: str) -> BusinessSolutionArtifact:
        cat = context.category.lower() if context.category else "custom"
        
        # 1. Base Deterministic Mapping
        solution_type = "Custom Business Digital Platform"
        modules = [
            SolutionModule(name="Home & Branding", justification="Establishes digital identity."),
            SolutionModule(name="Admin Dashboard", justification="Centralized business management."),
            SolutionModule(name="Analytics", justification="Traffic and conversion tracking.")
        ]
        features = [
            SolutionFeature(name="Contact Form", linked_need="Customer inquiries"),
            SolutionFeature(name="Role Management", linked_need="Secure admin access")
        ]
        integrations = [
            SolutionIntegration(name="Google Analytics", purpose="Traffic tracking"),
            SolutionIntegration(name="WhatsApp", purpose="Direct customer communication")
        ]
        tech_stack = ["React", "FastAPI", "PostgreSQL", "Firebase Auth", "Cloudflare"]
        benefits = ["Establish professional digital presence", "Centralize operations"]

        # 2. Industry Specific Hardening
        if "restaurant" in cat or "food" in cat:
            solution_type = "Restaurant Digital Platform"
            modules.extend([
                SolutionModule(name="Interactive Menu", justification="Showcase offerings dynamically."),
                SolutionModule(name="Reservations", justification="Solve missing reservation system issue."),
                SolutionModule(name="Online Ordering", justification="Direct revenue channel.")
            ])
            features.extend([
                SolutionFeature(name="Reservation Calendar", linked_need="Automate table bookings"),
                SolutionFeature(name="Payment Integration", linked_need="Process online orders")
            ])
            integrations.extend([
                SolutionIntegration(name="Paystack", purpose="Payment processing"),
                SolutionIntegration(name="Google Maps", purpose="Location routing")
            ])
            benefits.extend(["Increase Online Bookings", "Reduce Manual Reservations", "Improve Customer Experience"])

        elif "hotel" in cat or "hospitality" in cat:
            solution_type = "Hotel Reservation Platform"
            modules.append(SolutionModule(name="Booking Engine", justification="Direct room booking."))
            features.append(SolutionFeature(name="Room Availability Sync", linked_need="Prevent double booking"))
            integrations.append(SolutionIntegration(name="Stripe/Paystack", purpose="Deposit collection"))

        # 3. Phase Planning
        phases = [
            ImplementationPhase(phase_number=1, name="Core Digital Identity", focus="Frontend branding, SEO foundation, and Analytics."),
            ImplementationPhase(phase_number=2, name="Business Operations", focus="Admin Dashboard and core module integration (e.g., Reservations)."),
            ImplementationPhase(phase_number=3, name="Scale & Automation", focus="Payment gateways, notifications, and advanced reporting.")
        ]

        artifact_id = f"sol_{uuid.uuid4().hex[:8]}"

        return BusinessSolutionArtifact(
            artifact_id=artifact_id,
            agent_run_id=session_id,
            lead_id=context.lead_id,
            solution_type=solution_type,
            complexity="Medium",
            modules=modules,
            features=features,
            integrations=integrations,
            technology_stack=tech_stack,
            implementation_phases=phases,
            business_benefits=benefits,
            execution_metadata={"evaluation_method": "Deterministic Solution Mapping"}
        )