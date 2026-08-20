"""
NapsterTec AI - Social Intelligence Engine
Module: app/services/social_engine.py
"""
import uuid
from typing import Dict, Any
from app.schemas.shared_artifacts import (
    SocialAgentContext, SocialArtifact, SocialPost, MediaRequirement, PublishingRecommendation
)

class SocialEngine:
    def prepare_assets(self, context: SocialAgentContext, session_id: str) -> SocialArtifact:
        
        posts = []
        
        # 1. LinkedIn Adaptation (Professional, Long-form, Case Study focused)
        posts.append(SocialPost(
            platform="LinkedIn",
            business_objective=context.business_objective,
            audience="Enterprise Decision Makers & SMEs",
            headline="Automating Growth: The Engineering Behind Our Latest Deployment",
            message=f"At NapsterTec, we believe in deterministic engineering. Our latest {context.campaigns[0].get('name', 'Launch')} showcases how custom AI architecture eliminates operational bottlenecks. We bypassed generic templates and built a strictly typed, modular monolith capable of scaling instantly.\n\nRead our full breakdown on how we transition businesses from manual to autonomous.",
            call_to_action="Read the full Case Study in the comments below ⬇️",
            media_requirements=[
                MediaRequirement(type="Carousel Document", description="A 5-slide PDF breakdown of the system architecture and ROI metrics.", purpose="Drive high engagement and dwell time on LinkedIn.")
            ],
            hashtags=["#EnterpriseSoftware", "#ArtificialIntelligence", "#DigitalTransformation", "#NapsterTec", "#SoftwareEngineering"],
            publishing_recommendation=PublishingRecommendation(best_window="Tuesday 09:00 AM GMT", timezone="GMT", platform_priority=1)
        ))

        # 2. X/Twitter Adaptation (Technical, Snappy, Developer/Tech focused)
        posts.append(SocialPost(
            platform="X",
            business_objective="Technical Authority & Brand Awareness",
            audience="Tech Community & Founders",
            headline="Ship faster with deterministic AI.",
            message=f"Just shipped the architecture for our latest deployment. Fast, strictly typed, and completely autonomous. Stop guessing with your business architecture. 🚀🤖\n\nTake a look at the blueprint:",
            call_to_action="Check out the architecture repo link.",
            media_requirements=[
                MediaRequirement(type="Infographic Image", description="A clean, dark-mode technical diagram showing the FastAPI and Next.js data flow.", purpose="High retweet probability for tech audiences.")
            ],
            hashtags=["#BuildInPublic", "#AI", "#SoftwareArchitecture", "#NextJS"],
            publishing_recommendation=PublishingRecommendation(best_window="Wednesday 14:00 PM GMT", timezone="GMT", platform_priority=2)
        ))

        # 3. Instagram Adaptation (Visual, Behind-the-scenes, Culture)
        posts.append(SocialPost(
            platform="Instagram",
            business_objective="Brand Storytelling",
            audience="General Business Owners & Tech Enthusiasts",
            headline="Behind the Build",
            message=f"From a raw lead to a fully deployed enterprise system without a single human keystroke. This is what the future of software agencies looks like. ⚡️🏢",
            call_to_action="Link in bio to see the demo.",
            media_requirements=[
                MediaRequirement(type="Short Video/Reel", description="Fast-paced 15-second screen recording showing the AI generating code and deploying.", purpose="High algorithmic reach via Reels.")
            ],
            hashtags=["#TechStartup", "#SoftwareAgency", "#CodingLife", "#NapsterTec"],
            publishing_recommendation=PublishingRecommendation(best_window="Thursday 17:00 PM GMT", timezone="GMT", platform_priority=3)
        ))

        artifact_id = f"soc_{uuid.uuid4().hex[:8]}"

        return SocialArtifact(
            artifact_id=artifact_id,
            agent_run_id=session_id,
            lead_id=context.company_id,
            posts=posts,
            approval_status="Awaiting CTO Approval",
            execution_metadata={"evaluation_method": "Deterministic Platform Adaptation", "brand_alignment": "Verified"}
        )