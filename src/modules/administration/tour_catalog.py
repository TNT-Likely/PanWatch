"""Optional page walkthroughs; completion acknowledges instructions, not trades or AI results."""

TRACK_STEPS = {
    "opportunities": ("filters", "signals", "evaluation"),
    "paper": ("capital", "controls", "positions"),
    "assistant": ("composer", "context", "activity"),
    "automation": ("schedule", "binding", "history"),
    "datasources": ("catalog", "config", "test"),
}


def track_statuses(state: dict) -> list[dict]:
    saved = state.get("tracks") or {}
    items = []
    for key, steps in TRACK_STEPS.items():
        progress = saved.get(key) or {}
        completed = [step for step in steps if step in (progress.get("completed_steps") or [])]
        items.append({"key": key, "step": progress.get("step"), "started": bool(progress),
                      "completed_steps": completed, "completed": len(completed) == len(steps),
                      "total": len(steps)})
    return items
