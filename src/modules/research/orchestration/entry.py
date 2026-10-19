"""Cheap legacy dispatch; the graph package is loaded only for enabled research runs."""
from src.platform.persistence.database import engine
from src.platform.persistence.models import AgentConfig
from src.platform.persistence.worker import run_db_operation


async def maybe_run_research(agent, context):
    def read(db):
        row = db.query(AgentConfig).filter(AgentConfig.name == agent.name).first()
        return dict((row.config or {}).get("research_graph") or {}) if row else {}
    config = await run_db_operation(engine, read)
    if config.get("engine", "legacy") == "legacy":
        return None
    from .service import execute
    return await execute(agent, context, config)
