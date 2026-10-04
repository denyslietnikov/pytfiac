"""Regression checks for the HACS workflow's repository/ref selection."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_hacs_validates_triggering_commit_without_ignoring_checks():
    """Release/manual/scheduled events must not silently select another ref."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    job = workflow.split("\n  hacs:\n", 1)[1].split("\n  hassfest:\n", 1)[0]
    assert "uses: hacs/action@main" in job
    assert re.search(
        r"^        env:\n(?:          #[^\n]*\n)*"
        r"          REPOSITORY_REF: \$\{\{ "
        r"github\.event\.pull_request\.head\.sha \|\| github\.sha \}\}$",
        job,
        re.MULTILINE,
    )
    # Do not override repository selection: HACS resolves the PR head's fork.
    assert not re.search(
        r"^\s+(?:repository|REPOSITORY|INPUT_REPOSITORY):", job, re.MULTILINE
    )
    assert not re.search(r"^\s+(?:ignore|continue-on-error):", job, re.MULTILINE)
    assert "category: integration" in job
