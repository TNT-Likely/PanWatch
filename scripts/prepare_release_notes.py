#!/usr/bin/env python3
"""Validate and assemble user-facing GitHub release notes."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


REQUIRED_SECTIONS = (
    "## Highlights",
    "## Changes",
    "## Upgrade notes",
    "## Compatibility and known issues",
)

PLACEHOLDER_PATTERNS = (
    re.compile(r"\{\{\s*VERSION\s*\}\}", re.IGNORECASE),
    re.compile(r"\b(?:TODO|TBD)\b", re.IGNORECASE),
)


class ReleaseNotesError(ValueError):
    """Raised when a release-note source does not meet the public contract."""


def normalize_version(version: str) -> str:
    normalized = version.strip()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", normalized):
        raise ReleaseNotesError(f"unsupported release version: {version!r}")
    return normalized


def _section_body(text: str, heading: str) -> str:
    heading_match = re.search(rf"(?m)^{re.escape(heading)}\s*$", text)
    if heading_match is None:
        raise ReleaseNotesError(f"missing required section: {heading}")

    level = len(heading) - len(heading.lstrip("#"))
    next_heading = re.search(
        rf"(?m)^#{{2,{level}}}\s+", text[heading_match.end() :]
    )
    end = heading_match.end() + next_heading.start() if next_heading else len(text)
    body = text[heading_match.end() : end]
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()
    if not body:
        raise ReleaseNotesError(f"required section is empty: {heading}")
    return body


def validate_release_notes(source: str, version: str) -> str:
    normalized_version = normalize_version(version)
    text = source.strip()
    expected_title = f"# PanWatch {normalized_version}"
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first_line != expected_title:
        raise ReleaseNotesError(
            f"release notes must start with {expected_title!r}; got {first_line!r}"
        )

    positions: list[int] = []
    for heading in REQUIRED_SECTIONS:
        positions.append(text.find(heading))
        _section_body(text, heading)
    if positions != sorted(positions):
        raise ReleaseNotesError("release-note sections are not in the required order")

    for pattern in PLACEHOLDER_PATTERNS:
        match = pattern.search(text)
        if match:
            raise ReleaseNotesError(
                f"release notes still contain placeholder text: {match.group(0)!r}"
            )
    return text


def prepare_release_notes(
    source: str,
    *,
    version: str,
    repository: str,
    previous_tag: str | None = None,
) -> str:
    normalized_version = normalize_version(version)
    text = validate_release_notes(source, version)
    current_tag = version.strip()

    distribution = [
        "---",
        "",
        "## Distribution",
        "",
        f"- Docker: `sunxiao0721/panwatch:{normalized_version}`",
        "- Docker: `sunxiao0721/panwatch:latest`",
        f"- [Docker Hub](https://hub.docker.com/r/sunxiao0721/panwatch)",
        f"- [Installation and upgrade guide](https://github.com/{repository}#quick-start)",
    ]
    if previous_tag:
        distribution.append(
            f"- [Compare {previous_tag}...{current_tag}]"
            f"(https://github.com/{repository}/compare/{previous_tag}...{current_tag})"
        )
    return f"{text}\n\n" + "\n".join(distribution) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Release tag, such as 0.16.0")
    parser.add_argument("--repository", required=True, help="GitHub owner/repository")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous-tag")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        source = args.source.read_text(encoding="utf-8")
        output = prepare_release_notes(
            source,
            version=args.version,
            repository=args.repository,
            previous_tag=args.previous_tag,
        )
    except (OSError, ReleaseNotesError) as exc:
        parser.error(str(exc))
    args.output.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
