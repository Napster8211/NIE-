import json
import logging
import asyncio
from typing import Dict, Any, List

from app.planner.research_models import ResearchStrategy, EnterpriseReport, FactVerification
from app.tools.plugins.search_engine import WebIntelligenceTool

logger = logging.getLogger(__name__)

class DeepResearchPlanner:
    """
    Stateful orchestration engine for multi-hop research, fact verification, 
    and enterprise report generation.
    """
    
    def __init__(self, llm_client, max_iterations: int = 3):
        self.llm_client = llm_client
        self.search_tool = WebIntelligenceTool()
        self.max_iterations = max_iterations
        self.global_citations: Dict[str, str] = {}
        self.citation_counter = 1

    async def execute_research(self, topic: str) -> EnterpriseReport:
        """Main entry point for deep research execution."""
        logger.info(f"[DeepResearch] Initiating deep research on: {topic}")
        
        # Phase 1: Strategize
        strategy = await self._generate_strategy(topic)
        
        accumulated_context = []
        iteration = 0
        research_complete = False

        # Phase 2 & 3: Iterative Search & Read Sources
        while iteration < self.max_iterations and not research_complete:
            iteration += 1
            logger.info(f"[DeepResearch] Iteration {iteration}/{self.max_iterations}")
            
            # Execute pending tasks concurrently
            tasks_to_run = [t for t in strategy.tasks if t.status == "pending"]
            if not tasks_to_run:
                break
                
            execution_coros = [self._execute_task(task) for task in tasks_to_run]
            task_results = await asyncio.gather(*execution_coros)
            
            for task, result in zip(tasks_to_run, task_results):
                accumulated_context.append({"task": task.description, "evidence": result})
                task.status = "completed"

            # Phase 4 & 5: Evaluate Gaps and Contradictions
            gap_analysis = await self._evaluate_evidence(topic, strategy, accumulated_context)
            
            if gap_analysis.get("is_sufficient", False) and not gap_analysis.get("unresolved_contradictions", False):
                research_complete = True
            else:
                logger.warning(f"[DeepResearch] Research gaps found. Spawning new tasks: {gap_analysis.get('missing_information')}")
                # Append new tasks to strategy to fill gaps
                strategy.tasks.extend(self._create_followup_tasks(gap_analysis))

        # Phase 6 & 7: Fact Verification and Synthesis
        logger.info("[DeepResearch] Synthesizing Enterprise Report")
        final_report = await self._synthesize_report(topic, strategy, accumulated_context)
        
        return final_report

    async def _generate_strategy(self, topic: str) -> ResearchStrategy:
        """Prompts the LLM to break the topic down into actionable search strategies."""
        system_prompt = "You are an elite Lead Researcher. Decompose the user's request into a concrete, multi-step web search strategy. Return JSON matching the ResearchStrategy schema."
        
        response = await self.llm_client.generate_structured(
            prompt=topic,
            system_prompt=system_prompt,
            response_model=ResearchStrategy
        )
        return response

    async def _execute_task(self, task: Any) -> Dict[str, Any]:
        """Runs the search queries for a specific task using the Web Intelligence Tool."""
        task_data = {}
        for q in task.queries:
            # We enforce deep_read_top_n=2 so the engine actually reads the underlying markdown 
            # instead of just relying on search snippets.
            search_output = await self.search_tool.execute(
                query=q.query,
                providers=[q.provider_preference],
                max_results=5,
                deep_read_top_n=2 
            )
            
            # Remap local citations to global planner citations
            mapped_results = self._merge_citations(search_output)
            task_data[q.query] = mapped_results
            
        return task_data

    def _merge_citations(self, search_output: Dict[str, Any]) -> Dict[str, Any]:
        """Maintains a unified citation registry across multiple asynchronous search tasks."""
        local_to_global_map = {}
        
        for local_cite, url in search_output.get("citations", {}).items():
            if url not in self.global_citations.values():
                global_key = f"[{self.citation_counter}]"
                self.global_citations[global_key] = url
                local_to_global_map[local_cite] = global_key
                self.citation_counter += 1
            else:
                # Find existing global key for this URL
                for g_key, g_url in self.global_citations.items():
                    if g_url == url:
                        local_to_global_map[local_cite] = g_key
                        break

        # Replace local citation tags in the text with global ones
        processed_results = []
        for res in search_output.get("ranked_results", []):
            res["citation"] = local_to_global_map.get(res["citation"], res["citation"])
            processed_results.append(res)
            
        search_output["ranked_results"] = processed_results
        search_output["citations"] = self.global_citations
        return search_output

    async def _evaluate_evidence(self, topic: str, strategy: ResearchStrategy, context: List[Dict]) -> Dict[str, Any]:
        """Cross-references sources to detect contradictions and determine if more searches are needed."""
        prompt = f"""
        Original Topic: {topic}
        Expected Evidence: {strategy.expected_evidence}
        Collected Context: {json.dumps(context)[:15000]} # Truncated for token limits
        
        Analyze the collected context. 
        1. Is the evidence sufficient to generate a comprehensive enterprise report?
        2. Are there any direct contradictions between sources?
        3. What specific information is still missing?
        Return a JSON object with keys: "is_sufficient" (bool), "unresolved_contradictions" (bool), and "missing_information" (list of strings).
        """
        response = await self.llm_client.generate_json(prompt=prompt)
        return response

    def _create_followup_tasks(self, gap_analysis: Dict[str, Any]) -> List[Any]:
        """Dynamically generates new queries if the initial strategy failed to find the data."""
        from app.planner.research_models import ResearchTask, SearchQuery
        
        new_tasks = []
        for i, gap in enumerate(gap_analysis.get("missing_information", [])):
            task = ResearchTask(
                task_id=f"followup_{i}",
                description=f"Resolve missing data: {gap}",
                queries=[SearchQuery(query=gap, intent="Fill research gap", provider_preference="duckduckgo")]
            )
            new_tasks.append(task)
        return new_tasks

    async def _synthesize_report(self, topic: str, strategy: ResearchStrategy, context: List[Dict]) -> EnterpriseReport:
        """The final cognitive step: generating the report with inline citations and fact confidence scores."""
        system_prompt = """
        You are a Senior Intelligence Analyst. Synthesize the provided context into a polished, 
        highly structured Enterprise Report. 
        
        CRITICAL RULES:
        1. You MUST use inline citations (e.g., [1], [2]) corresponding strictly to the provided global_citations mapping.
        2. Extract 3-5 core facts and assign a confidence_score (0.0 - 1.0) based on source quality.
        3. Document any contradictions clearly.
        4. Do not invent information. If a detail is missing, state that it is unknown.
        """
        
        payload = {
            "topic": topic,
            "strategy": strategy.summary_plan,
            "global_citations": self.global_citations,
            "raw_context": context
        }
        
        report = await self.llm_client.generate_structured(
            prompt=json.dumps(payload),
            system_prompt=system_prompt,
            response_model=EnterpriseReport
        )
        
        # Inject the final citation map into the object before returning
        report.citations = self.global_citations
        return report