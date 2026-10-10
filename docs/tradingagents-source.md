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
python scripts/prepare-tradingagents-requirements.py --source "git+https://github.com/idkwhodatis/TradingAgents.git@c12865ae3d4be7790853a1e047d067a0a9931821"
python -m pip install -r .requirements-custom.txt
```

The public URL above is a tested example, not a product dependency. Substitute
your own reviewed repository and full 40-character commit SHA. An exact package
version such as `--source "0.6.0"` selects `tradingagents==0.6.0` from your
configured pip index, if that release exists there; package-index availability
is not guaranteed. Branches, floating version constraints, arbitrary requirement
lines, local paths, SSH URLs, URL credentials, query strings and fragments are
not accepted. Git must be installed for Git sources.

The generated manifest preserves every other runtime requirement, include and
editable local package. Keep it in the repository root and run pip from that
root. It is ignored by Git. The commands above also work in PowerShell; there
is no shell-specific environment-assignment syntax. To run tests, install
`pytest` separately. Do not subsequently install `requirements.txt`, which
would explicitly select the official source again. To return to the default,
run `python -m pip install -r requirements.txt`.

## Docker

```sh
docker build --build-arg "TRADINGAGENTS_SOURCE=git+https://github.com/idkwhodatis/TradingAgents.git@c12865ae3d4be7790853a1e047d067a0a9931821" -t panwatch-custom .
```

Omit the build argument (or leave it empty) for the official default. The
Python dependency stage uses the same manifest generator and converts local
editable packages to regular installed packages, just as the default image did.
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
US fallback and nested router restoration behavior is retained.

Run `python -m pytest -q tests/test_tradingagents*.py tests/test_ta*.py` after
installing your source. Check `python -m pip show tradingagents` and the installed
package's `direct_url.json` for Git commit provenance; avoid source-directory
shadowing of the installed distribution. CI tests the official default and the
reviewed example fork as separate installations/builds. Tests use deterministic
inputs without paid LLM calls; they are not a live-market smoke test.

This branch alone preserves CN/HK/US. Canadian markets remain a separate feature
branch; selecting a source does not add TSX/TSXV UI, identity, calendar or
portfolio-valuation support. Test that feature's contracts too when combining.
