# Custom TradingAgents installation source

The default remains official `TauricResearch/TradingAgents` at the `v0.5.0` release commit
`2d17df8da1536c121e4d7395ac5a5dcec9e96d6f`. The immutable SHA avoids
Git/pip resolving the upstream branch named `v0.5.0` instead of the release tag.
`pip install -r requirements.txt` and a normal Docker build keep that default.
Source selection happens only during installation or image build, never through
the running application. Changing a container's environment does not replace
its dependencies: rebuild the image to select another source.

## Manual installation (Python 3.11+, Linux/macOS/Windows PowerShell)

Run from the PanWatch repository root in your activated virtual environment:

```sh
python scripts/prepare-tradingagents-requirements.py --install --source "git+https://github.com/idkwhodatis/TradingAgents.git@3e0dc2647749aeec8ec38fd36fed1298b0889c54"
```

The public URL above is a tested example, not a product dependency. Substitute
your own reviewed repository and full 40-character commit SHA. An exact package
version such as `--source "1.2.3"` selects `tradingagents==1.2.3` from your
configured pip index, if you have independently verified that artifact is a
compatible build of the intended project. A matching package name/version does
not establish source identity or API compatibility. In particular, the public
PyPI `tradingagents` project (including its `0.6.0` release) points to
[Mai0313/TradingAgents](https://pypi.org/project/tradingagents/), a different
project from the default TauricResearch source; it is not a supported substitute
just because the names or versions match. Branches, floating version constraints, arbitrary requirement
lines, local paths, SSH URLs, URL credentials, query strings and fragments are
not accepted. Git must be installed for Git sources.

The `--install` option first force-reinstalls only the selected TradingAgents
distribution with `--no-deps`, then runs normal dependency resolution for the
generated manifest. This handles same-version Git → index (or index → Git)
switches in a reused virtual environment without force-reinstalling unrelated
dependencies. Plain `pip install -r .requirements-custom.txt` can leave an old
Git source installed when an index selection has the same version; use
`--install` whenever selecting or switching sources. A failed install is not
a successful switch; resolve the reported error and rerun the command.

Without `--install`, the script only prepares the manifest. The generated
manifest preserves every other runtime requirement, include and
editable local package. Keep it in the repository root and run pip from that
root. It is ignored by Git. The commands above also work in PowerShell; there
is no shell-specific environment-assignment syntax. To run tests, install
`pytest` separately. Do not subsequently install `requirements.txt`, which
would explicitly select the official source again. To return to the default,
run `python scripts/prepare-tradingagents-requirements.py --install --source ""`.
The explicit empty source ignores any previous `TRADINGAGENTS_SOURCE` value.

## Docker

```sh
docker build --build-arg "TRADINGAGENTS_SOURCE=git+https://github.com/idkwhodatis/TradingAgents.git@3e0dc2647749aeec8ec38fd36fed1298b0889c54" -t panwatch-custom .
```

Omit the build argument (or leave it empty) for the official default. The
Python dependency stage uses the same manifest generator and converts local
editable packages to regular installed packages, just as the default image did.
The Docker stage starts fresh, so it uses manifest preparation plus a normal
pip install into its empty `/install` prefix; no source switch is needed.
For a dependency-stage-only check, add `--target python-builder`.

Build arguments can be recorded in image history, build logs and provenance.
Use only public, credential-free sources; never put passwords or tokens in this
argument, URL or generated manifest. Validation rejects credential-bearing
URLs without echoing the invalid value, but cannot erase inputs already stored
by your shell or build service. Private authenticated source setup is outside
this option's scope. Installing a repository executes its build code; syntax
validation is not a trust/security audit. Review the source before installing.

## Compatibility and verification

A compatible source must provide the graph/portfolio/callback contracts and
vendor-router, OHLCV and verified-snapshot APIs used by PanWatch. Unsupported
APIs are reported as unavailable before analysis starts. Module layout detection
supports official 0.5 and modern 0.6 without an arbitrary version allowlist;
this does not promise compatibility with every custom fork or future release.
The original request-local cache, cancellation, CN interception, HK formatting,
US fallback and nested router restoration behavior is retained. Exact numeric
`.SS`, `.SZ` and `.BJ` graph aliases compare against the bare CN cache identity
only when its recorded market is CN; outbound upstream requests retain their
provider symbol. Cache entries without an explicit matching market are not used.
Modern portfolio adaptation likewise retains each lot’s explicit market. After
the graph verifies a mainland A-share identity, its canonical symbol is applied
only to that CN holding, including same-market lot aggregation. Other markets,
explicitly different exchange suffixes and the caller’s portfolio are untouched.
Unverified identities never cause bare-code holdings to be guessed. The adapted
context reaches the real graph-state/Portfolio Manager boundary; market identity
is also retained in checkpoint fingerprints.

The configured `llm_timeout_seconds` must reach both actual LLM clients. The
legacy `_get_provider_kwargs` hook and the modern Python
`create_tier_client` → `build_llm_kwargs` constructor boundary are supported.
Modern adaptation uses graph-local function bindings, never a shared mutable
request configuration. Other constructor boundaries fail availability validation
with an explicit timeout-boundary error instead of silently dropping the timeout.
Modern news cancellation is checked outside enrichment's primary-error handler
and before/after additional source boundaries; cancellation is never news evidence.

Run `python -m pytest -q tests/test_tradingagents*.py tests/test_ta*.py` after
installing your source. Check `python -m pip show tradingagents` and the installed
package's `direct_url.json` for Git commit provenance; avoid source-directory
shadowing of the installed distribution. CI tests the official default and the
reviewed example fork as separate installations/builds. Tests use deterministic
inputs without paid LLM calls; they are not a live-market smoke test.

This branch alone preserves CN/HK/US. Canadian markets remain a separate feature
branch; selecting a source does not add TSX/TSXV UI, identity, calendar or
portfolio-valuation support. Test that feature's contracts too when combining.
