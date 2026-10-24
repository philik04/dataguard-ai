"""Smoke test for the public command-line behavior."""

import subprocess
import sysconfig
from pathlib import Path


def test_installed_cli_prints_project_and_phase() -> None:
    """Check that packaging creates a working console entry point."""
    executable = Path(sysconfig.get_path("scripts")) / "dataguard-ai"
    result = subprocess.run(
        [str(executable)], capture_output=True, text=True, check=True
    )

    assert result.stdout == "DataGuard AI | Phase 0: repository foundation\n"
    assert result.stderr == ""
