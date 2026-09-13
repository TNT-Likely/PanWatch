# PanAgent Runtime Architecture

`pan-agent-runtime` follows a small-kernel, extension-oriented design. The
package deliberately does not become a database layer, a web framework, or a
provider SDK.

## Three logical layers

### Core contracts

The stable boundary is made of provider-neutral Pydantic models and async
protocols: model messages and turns, tools and results, policies and
checkpoints, plus runtime events and sinks. Core contracts must not import
FastAPI, SQLAlchemy, a model SDK, or a business module.

### Runtime loop

`AgentRuntime` owns the deterministic serial execution loop:

1. start a run and expose policy-approved tools;
2. run a model turn;
3. check permission before every tool call;
4. execute, retry, time out, or pause for approval;
5. emit structured events and return a result or checkpoint.

The runtime owns safety invariants such as approval boundaries, limits and
repeated-call protection. The host still supplies the actual permission policy
and persists checkpoints.

### Context extension

`pan_agent.context` is the first built-in extension point. It contains token
estimates, `ContextBudget`, section-level `ContextUsage`, inspectable
`ContextSummary` fields, `ContextEngine`, the `ContextSummarizer` protocol, and
the no-network `ExtractiveContextSummarizer` fallback.

The context layer does not know how conversations are stored. A host decides
whether snapshots live in SQL, Redis, object storage, or nowhere at all.

## Host composition

The first release keeps one distribution package to avoid premature package
fragmentation. Capabilities are still composed through explicit ports:

```text
pan-agent-runtime
|- core contracts and ports
|- runtime loop and safety invariants
|- context budgeting and compaction
`- optional host adapters
   |- model provider adapter
   |- persistence/checkpoint adapter
   |- SSE/WebSocket event adapter
   |- MCP/tool discovery adapter
   |- observability/evaluation adapter
   `- A2UI/frontend adapter
```

An adapter becomes a separate installable package only when a second project
actually reuses it. Logical plugin boundaries and PyPI package boundaries are
intentionally different.

## PanWatch current composition

PanWatch supplies the failover model adapter, the configurable context
summarizer, SQLAlchemy snapshots, FastAPI/SSE mapping, and React context/trace
views. The compression model is selected by the host: an explicit
`CONTEXT_COMPRESSION_MODEL_ID` uses that persisted model and its existing
failover chain; otherwise the default assistant model is used. The host also
controls `CONTEXT_SUMMARY_MAX_TOKENS`, which bounds the structured summary
before it can replace older history.

For long-running assistant tasks, the current host stores task snapshots,
events, approvals, and checkpoints in SQLite. The SSE endpoint replays and
tails the persisted event table, so a browser refresh does not depend on the
in-memory runner or Redis. The runner is still in-process; queued work can be
rescanned on startup, while an interrupted running task is currently marked
explicitly failed rather than resumed blindly.

Redis, durable queues, leases, and separate worker processes remain optional
host-side extensions for a future multi-instance or high-traffic deployment.

The runtime package never sees a model ID or a database session.

## Future plugin protocol

The current package uses constructor composition, which is explicit and easy
to test. A later plugin registry can add mounting and dependency metadata
without changing runtime contracts:

```python
class AgentPlugin(Protocol):
    name: str
    def mount(self, app: AgentApp) -> None: ...
```

Dynamic discovery, mount/unmount and third-party version negotiation are
deliberately outside M0.
