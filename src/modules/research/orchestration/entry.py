"""Mandatory graph dispatch; retired modes are migrated at database startup."""
from src.platform.persistence.database import engine
from src.platform.persistence.models import AgentConfig
from src.platform.persistence.worker import run_db_operation


async def run_research(agent, context):
    def read(db):
        row = db.query(AgentConfig).filter(AgentConfig.name == agent.name).first()
        return dict((row.config or {}).get("research_graph") or {}) if row else dict(agent.research_graph or {})
    config = await run_db_operation(engine, read)
    from .service import execute
    return await execute(agent, context, config)
