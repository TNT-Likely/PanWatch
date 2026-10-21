"""Validated graph templates; arbitrary node/edge wiring cannot bypass required checks."""
from copy import deepcopy

from pan_investment_research import ResearchPolicy


PROFILES = {"daily_report": "postmarket", "premarket_outlook": "premarket", "intraday_monitor": "intraday"}
DEFAULT_TEMPLATES = {"daily_report": "postmarket_deep", "premarket_outlook": "premarket_balanced", "intraday_monitor": "intraday_fast"}
PRESETS = {
    "intraday_fast": {"graph": "intraday_fast", "specialists": ["technical", "events"], "debate": False,
        "max_feedback": 0, "max_attempts": 6, "max_tools": 2, "concurrency": 2,
        "max_output_tokens": 1536, "max_total_tokens": 80000, "deadline_seconds": 45},
    "premarket_balanced": {"specialists": ["technical", "flow", "events"], "debate": True, "debate_on": "material",
        "max_attempts": 14, "max_tools": 4, "max_output_tokens": 3072, "deadline_seconds": 90},
    "postmarket_deep": {"specialists": ["technical", "flow", "events"], "debate": True, "debate_on": "material"},
}


def template_for(config, agent_name):
    value = config.get("template", "auto")
    if value == "auto":
        # Preserve older explicit full-graph configurations; do not silently discard their nodes.
        if agent_name == "intraday_monitor" and "template" not in config and (
            "flow" in config.get("specialists", []) or config.get("debate") or config.get("max_feedback", 0)
        ):
            return "custom_research"
        return DEFAULT_TEMPLATES[agent_name]
    if value not in {DEFAULT_TEMPLATES[agent_name], "custom_research"}:
        raise ValueError("template is unavailable for this session")
    return value


def validate_config(config, agent_name):
    if agent_name not in PROFILES:
        raise ValueError("research graph is unavailable for this agent")
    config = dict(config or {})
    mode = config.pop("engine", "langgraph")
    if mode != "langgraph":
        raise ValueError("legacy and shadow session engines have been retired; use langgraph")
    template = template_for(config, agent_name)
    config.pop("template", None)
    config.pop("profile", None)
    if "graph" in config:
        raise ValueError("select the graph through a validated template")
    defaults = {"profile": PROFILES[agent_name]}
    if template == "custom_research":
        if agent_name != "daily_report":
            defaults.update(debate=False, max_attempts=14 if agent_name == "premarket_outlook" else 10,
                deadline_seconds=90 if agent_name == "premarket_outlook" else 45)
        if agent_name == "intraday_monitor":
            defaults.update(specialists=["technical"], max_feedback=0, max_tools=2, concurrency=2)
    else:
        defaults.update(deepcopy(PRESETS[template]))
    return mode, ResearchPolicy.model_validate({**defaults, **config})


def templates_for(agent_name):
    if agent_name not in DEFAULT_TEMPLATES:
        raise ValueError("research graph is unavailable for this agent")
    options = []
    for name in (DEFAULT_TEMPLATES[agent_name], "custom_research"):
        _, policy = validate_config({"template": name}, agent_name)
        options.append({"id": name, "defaults": policy.model_dump(exclude={"profile", "graph"}),
            "graph": policy.graph, "allowed_specialists": ["technical", "events"] if policy.graph == "intraday_fast" else ["technical", "flow", "events"]})
    return {"default_template": DEFAULT_TEMPLATES[agent_name], "templates": options}
