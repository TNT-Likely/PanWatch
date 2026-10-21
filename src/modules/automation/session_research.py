"""One mandatory graph entry for the three session collectors."""
from src.modules.automation.base import BaseAgent


class SessionResearchAgent(BaseAgent):
    def __init__(self, research_graph=None):
        self.research_graph = research_graph

    async def run(self, context):
        from src.modules.research.orchestration.entry import run_research
        return await run_research(self, context)
