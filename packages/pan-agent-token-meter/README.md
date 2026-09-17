# Pan Agent Token Meter

`pan-agent-token-meter` is an optional plugin for token measurement and
provider usage normalization. The runtime does not depend on this package.

The plugin has two separate responsibilities:

- `HeuristicTokenMeter`: a dependency-free preflight estimate used before a
  request is sent;
- `normalize_provider_usage`: converts provider response usage into the
  provider-neutral `pan-agent-runtime.ModelUsage` contract.

The estimate is used for context and compaction decisions. Provider usage is
the authoritative measurement after a model request completes. Keeping these
two values separate avoids presenting a preflight estimate as billing data.

Provider-specific tokenizers can be added as optional implementations of the
runtime `TokenMeter` protocol without making `pan-agent-runtime` depend on a
vendor SDK.

For OpenAI-compatible models, install the optional extra and inject
`TiktokenTokenMeter` with the encoding appropriate for the selected model:

```python
from pan_agent_token_meter import TiktokenTokenMeter

meter = TiktokenTokenMeter("cl100k_base")
```
