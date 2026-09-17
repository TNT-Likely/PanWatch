# Context Engineering

The context extension turns a long conversation into a bounded model input
without deleting the host's original messages.

## Basic usage

```python
from pan_agent import ContextBudget, ContextEngine

engine = ContextEngine(summarizer=my_summarizer)
result = await engine.prepare(
    messages,
    budget=ContextBudget(
        max_tokens=12_000,
        soft_limit_tokens=8_400,
        hard_limit_tokens=10_200,
        keep_recent_messages=8,
        summary_max_tokens=800,
    ),
)
```

`result.usage_before` is the measured input before compaction and
`result.usage_after` is the input that should be sent to the model. The
measurement is a dependency-free estimate unless the host injects an optional
`TokenMeter` implementation. The runtime does not import a tokenizer SDK.
The breakdown includes system instructions, summaries, page context, tool
definitions, older history, and recent messages. The UI can display
`usage_after.state` as `normal`, `warning`, or `needs_compression`.

## Optional token meter plugin

The runtime exposes a small port rather than choosing a tokenizer:

```python
from pan_agent import ContextEngine
from pan_agent_token_meter import TiktokenTokenMeter

engine = ContextEngine(
    summarizer=my_summarizer,
    token_meter=TiktokenTokenMeter("cl100k_base"),
    model="gpt-4o-mini",
)
```

`ContextUsage.measurement` identifies the strongest claim that can be made
about the report: `estimated`, `tokenizer`, or `provider`. A preflight meter
is useful for deciding whether to compact, but it is not billing truth.
Provider-reported usage belongs to `ModelTurn.usage` and is emitted as the
provider-neutral `model_usage` runtime event after a model turn completes.
The optional `pan-agent-token-meter` package supplies the default heuristic
meter, the optional `tiktoken` implementation, and adapters for common
provider usage payloads. A host can replace it with its own tokenizer or omit
it entirely.

## Compression behavior

- Below the soft limit, the engine keeps the history unchanged.
- At or above the soft limit, it summarizes older messages and keeps the recent
  window.
- `force_compress=True` provides an explicit user action.
- Trusted system messages and page context are retained.
- A compaction candidate is accepted only when its estimated input is smaller
  than the current input. Otherwise the original context is kept and
  `compression_status` is `no_gain`.
- A previously persisted summary replaces the messages it covers when measuring
  the next model input; the original conversation records remain immutable.
- A structured summary contains `goal`, `constraints`, `decisions`, `facts`,
  `current_state`, `open_items`, and `tool_findings`.
- A summarizer failure falls back to `ExtractiveContextSummarizer`; the engine
  does not pretend a model summary succeeded.

## Host responsibilities

The host should persist a versioned snapshot if users need to inspect or reuse
the summary. The snapshot should include the conversation ID, summary version,
compression mode, covered message boundary, source message count, before/after
usage reports, and creation time.

The host should keep original messages immutable and pass the prepared message
list only into the next `RunRequest`. Approval checkpoints are already prepared
runtime state and should resume directly without another compression pass.

If the host needs accounting, persist provider usage events separately from
preflight context reports. One model response may contain cached input tokens,
reasoning output tokens, or provider-specific fields that cannot be inferred
from the serialized message text.

## Model-backed summarizer contract

`ContextSummarizer.summarize()` receives only provider-neutral messages and a
compression mode. A host adapter may call any model provider, use JSON schema
output, or implement an extractive strategy. The runtime does not require a
specific vendor, tokenizer, or persistence engine.
