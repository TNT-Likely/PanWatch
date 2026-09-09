# PanAgent Runtime

`pan-agent-runtime` is a deliberately small, host-agnostic runtime for
bounded, observable, tool-using assistants.  It contains no HTTP, database,
provider, or business-domain dependency.  Hosts supply model, tool, and event
adapters through Protocols.

```python
from pan_agent import AgentRuntime, ToolRegistry
```

