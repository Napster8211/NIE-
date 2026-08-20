from pydantic import BaseModel, Field
from typing import List, Dict, Optional

class SearchQuery(BaseModel):
    query: str = Field(..., description="The exact search string to execute.")
    intent: str = Field(..., description="The specific data point this query aims to extract.")
    provider_preference: str = Field(default="duckduckgo", description="Preferred search provider for this query.")

class ResearchTask(BaseModel):
    task_id: str
    description: str = Field(..., description="What this specific sub-task will accomplish.")
    queries: List[SearchQuery] = Field(..., description="Searches required to complete this task.")
    status: str = Field(default="pending", description="pending, in_progress, completed, or failed")

class ResearchStrategy(BaseModel):
    research_goals: List[str] = Field(..., description="High-level objectives of this research.")
    tasks: List[ResearchTask] = Field(..., description="Decomposed research tasks to be executed in parallel.")
    expected_evidence: List[str] = Field(..., description="Types of data/metrics required to consider the research complete.")
    summary_plan: str = Field(..., description="How the final report should be structured based on the request.")

class FactVerification(BaseModel):
    claim: str = Field(..., description="The extracted claim or data point.")
    confidence_score: float = Field(..., description="Score from 0.0 to 1.0 based on source authority and corroboration.")
    contradictions_found: Optional[str] = Field(default=None, description="Details of conflicting information across sources.")
    supporting_citations: List[str] = Field(default_factory=list, description="Citation keys (e.g., [1], [2]) backing this claim.")

class EnterpriseReport(BaseModel):
    title: str
    executive_summary: str
    detailed_findings: str = Field(..., description="Markdown-formatted main body of the report.")
    verified_facts: List[FactVerification]
    confidence_assessment: str = Field(..., description="An overarching assessment of the data reliability.")
    citations: Dict[str, str] = Field(..., description="Mapping of citation keys to their source URLs.")