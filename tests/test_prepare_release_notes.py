from __future__ import annotations

import pytest

from scripts.prepare_release_notes import (
    ReleaseNotesError,
    prepare_release_notes,
    validate_release_notes,
)


def _valid_notes(version: str = "0.16.0") -> str:
    return f"""# PanWatch {version}

## Highlights

- A user-visible improvement.

## Changes

- A concrete behavior change.

## Upgrade notes

- Back up the data volume before upgrading.

## Compatibility and known issues

- No known breaking changes.
"""


def test_prepare_release_notes_adds_distribution_metadata():
    rendered = prepare_release_notes(
        _valid_notes(),
        version="0.16.0",
        repository="TNT-Likely/PanWatch",
        previous_tag="0.15.0",
    )

    assert "`sunxiao0721/panwatch:0.16.0`" in rendered
    assert "TNT-Likely/PanWatch/compare/0.15.0...0.16.0" in rendered
    assert "## Highlights" in rendered


def test_validate_release_notes_accepts_v_prefixed_tag():
    assert validate_release_notes(_valid_notes(), "v0.16.0").startswith(
        "# PanWatch 0.16.0"
    )


def test_validate_release_notes_rejects_missing_section():
    notes = _valid_notes().replace("## Upgrade notes", "## Upgrade")

    with pytest.raises(ReleaseNotesError, match="missing required section"):
        validate_release_notes(notes, "0.16.0")


@pytest.mark.parametrize("placeholder", ["TODO", "TBD", "{{VERSION}}"])
def test_validate_release_notes_rejects_placeholders(placeholder: str):
    notes = _valid_notes().replace(
        "A user-visible improvement.", f"A user-visible improvement. {placeholder}"
    )

    with pytest.raises(ReleaseNotesError, match="placeholder text"):
        validate_release_notes(notes, "0.16.0")
