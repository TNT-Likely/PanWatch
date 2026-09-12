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
    ),
)
```

`result.usage_before` is the measured input before compaction and
`result.usage_after` is the input that should be sent to the model. The UI can
display `usage_after.state` as `normal`, `warning`, or `needs_compression`.

## Compression behavior

- Below the soft limit, the engine keeps the history unchanged.
- At or above the soft limit, it summarizes older messages and keeps the recent
  window.
- `force_compress=True` provides an explicit user action.
- Trusted system messages and page context are retained.
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

## Model-backed summarizer contract

`ContextSummarizer.summarize()` receives only provider-neutral messages and a
compression mode. A host adapter may call any model provider, use JSON schema
output, or implement an extractive strategy. The runtime does not require a
specific vendor, tokenizer, or persistence engine.
