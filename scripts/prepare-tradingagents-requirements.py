#!/usr/bin/env python3
"""Prepare a source manifest; optionally install it with a targeted source switch."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

# Deliberately bounded input, not arbitrary pip requirement/option syntax.
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)+(?:a[0-9]+|b[0-9]+|rc[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?")
_GIT = re.compile(r"git\+https://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?/(?:[A-Za-z0-9][A-Za-z0-9_.-]*/)+[A-Za-z0-9][A-Za-z0-9_.-]*\.git@[0-9a-fA-F]{40}")
_DEPENDENCY = re.compile(r"^tradingagents(?:\s|[=<>!~@\[]|$)", re.IGNORECASE)


def source_requirement(source: str) -> str | None:
    if source == "":
        return None
    if _VERSION.fullmatch(source):
        return f"tradingagents=={source}"
    if _GIT.fullmatch(source):
        host = urlsplit(source[4:]).hostname or ""
        labels = host.split(".")
        if all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels):
            return f"tradingagents @ {source}"
    # Do not echo potentially credential-bearing input into build logs.
    raise ValueError("TradingAgents source must be an exact version or a credential-free git+https URL ending in .git@<40-character commit SHA>; whitespace, query strings, fragments and pip options are forbidden")


def prepare_manifest(text: str, source: str = "", *, non_editable: bool = False) -> str:
    replacement = source_requirement(source)
    lines = text.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if _DEPENDENCY.match(line)]
    if len(matches) != 1:
        raise ValueError("Runtime manifest must contain exactly one TradingAgents dependency")
    if replacement is not None:
        index = matches[0]
        lines[index] = replacement + ("\n" if lines[index].endswith("\n") else "")
    if non_editable:
        lines = [line[3:] if line.startswith("-e ") else line for line in lines]
    return "".join(lines)


def install_manifest(path: Path) -> None:
    """Replace only the selected distribution, then resolve normal requirements.

    pip treats an installed Git distribution as satisfying an index requirement
    with the same version. A targeted --no-deps reinstall changes its origin
    deterministically, without force-reinstalling all runtime dependencies.
    """
    selected = [line for line in path.read_text(encoding="utf-8").splitlines()
                if _DEPENDENCY.match(line)]
    if len(selected) != 1:
        raise ValueError("Runtime manifest must contain exactly one TradingAgents dependency")
    subprocess.run([sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", selected[0]], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(path)], check=True)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse may otherwise echo unknown arguments containing credentials.
        super().error("Invalid source or manifest arguments. Run from the repository root; use an exact version or credential-free HTTPS Git URL pinned to a full SHA. Output must be .requirements-*.txt in the root. See --help.")


def main() -> None:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.environ.get("TRADINGAGENTS_SOURCE", ""))
    parser.add_argument("--output", default=".requirements-custom.txt")
    parser.add_argument("--non-editable", action="store_true", help="Install local packages normally (Docker)")
    parser.add_argument("--install", action="store_true", help="Replace only TradingAgents, then install the generated runtime manifest (for fresh or reused virtual environments)")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    if Path.cwd().resolve() != root:
        parser.error("Run this script from the PanWatch repository root so local package paths resolve correctly")
    output = Path(args.output).resolve()
    # Keep includes and relative package paths at the original manifest location.
    if output.parent != root or not re.fullmatch(r"\.requirements-[A-Za-z0-9_-]+\.txt", output.name):
        parser.error("Output must be a new manifest filename in the repository root")
    try:
        rendered = prepare_manifest((root / "requirements-runtime.txt").read_text(encoding="utf-8"), args.source, non_editable=args.non_editable)
    except ValueError as exc:
        parser.error(str(exc))
    output.write_text(rendered, encoding="utf-8")
    if args.install:
        install_manifest(output)


if __name__ == "__main__":
    main()
