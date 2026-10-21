"""Tests for the commit-message hook (``.github/scripts/check-commit-msg.sh``).

Code commits (``feat``, ``fix``, ``perf``, ``refactor``, ``test``) must carry a
YouTrack prefix such as ``DCE-12:``; the other types may omit it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).parents[2] / ".github" / "scripts" / "check-commit-msg.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def run_hook(message: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text(message)
    return subprocess.run(
        ["bash", str(HOOK), str(msg_file)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "message",
    [
        "DCE-12: feat: add pipeline behaviors",
        "DCE-12: fix(cqrs): drain event buffers on rollback",
        "DCE-12: perf: cache handler lookup",
        "DCE-12: refactor(ddd): extract id guard",
        "DCE-12: test(cqrs): reproduce event leak after rollback",
        "DCE-12: feat(api)!: change dispatch return type",
        "DCE-12:fix: no space after the prefix",
        "docs: fix a typo",
        "chore(release): bump version to 0.3.1",
        "ci: add Python 3.14 to the matrix",
        "build: pin hatchling",
        "style: reformat",
        "revert: undo the relay change",
        "DCE-12: docs(adr): add ADR-066",
    ],
)
def test_valid_messages_are_accepted(message: str, tmp_path: Path) -> None:
    result = run_hook(message, tmp_path)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    "message",
    [
        "fix: drain event buffers on rollback",
        "feat(cqrs): add pipeline behaviors",
        "perf: cache handler lookup",
        "refactor: extract id guard",
        "test(cqrs): reproduce event leak",
        "feat(api)!: change dispatch return type",
    ],
)
def test_code_commits_without_issue_prefix_are_rejected(
    message: str, tmp_path: Path
) -> None:
    result = run_hook(message, tmp_path)
    assert result.returncode == 1
    assert "issue prefix" in result.stdout


@pytest.mark.parametrize(
    "message",
    [
        "update things",
        "fix drain event buffers",
        "DCE-12: fix:",
        "DCE-12: wip: something",
        "",
    ],
)
def test_malformed_messages_are_rejected(message: str, tmp_path: Path) -> None:
    result = run_hook(message, tmp_path)
    assert result.returncode == 1


def test_comment_lines_are_ignored(tmp_path: Path) -> None:
    result = run_hook(
        "# Please enter the commit message\n\nDCE-12: fix: drain buffers\n", tmp_path
    )
    assert result.returncode == 0, result.stdout
