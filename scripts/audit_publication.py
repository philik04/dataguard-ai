"""Offline checks of the current Git publication candidate; never prints matches.

Heuristics supplement manual review, not a guarantee that a repository is safe.
Ignored files stay private. Already tracked ignored files are still audited.
"""

import json
import re
import shlex
import subprocess
from pathlib import Path

PATTERNS = {
    "personal local path": re.compile(r"/(?:Users|home)/[^/\s]+/|[A-Za-z]:\\Users\\"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "API key shaped value": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "record ID shaped value": re.compile(r"(?<![a-f0-9])[a-f0-9]{32}(?![a-f0-9])"),
}


def candidate_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        check=True,
    )
    return sorted({Path(name.decode()) for name in result.stdout.split(b"\0") if name})


def local_secret_values() -> list[str]:
    """Compare exact locally configured secrets in memory; never print them."""
    path = Path(".env")
    if not path.exists():
        return []
    values = []
    for line in path.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, raw = line.split("=", 1)
        if not any(
            word in key.upper() for word in ("PASSWORD", "SECRET", "KEY", "TOKEN")
        ):
            continue
        parsed = shlex.split(raw)
        if (
            parsed
            and len(parsed[0]) >= 8
            and parsed[0] not in ("replace_me", "example")
        ):
            values.append(parsed[0])
    return values


def audit() -> dict:
    candidates = candidate_files()
    secrets = local_secret_values()
    findings = []
    for path in candidates:
        name = path.as_posix()
        if (
            name.startswith(
                (
                    "reports/generated/",
                    "reports/private/",
                    "data/raw/",
                    ".venv/",
                    "dist/",
                )
            )
            or path.suffix in (".csv", ".parquet", ".sqlite", ".db")
            or (path.name.startswith(".env") and path.name != ".env.example")
            or name == ".streamlit/secrets.toml"
        ):
            findings.append({"file": name, "category": "private artifact in candidate"})
        if not path.is_file():
            continue
        data = path.read_bytes()
        for value in secrets:
            if value.encode() in data:
                findings.append({"file": name, "category": "exact local secret value"})
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            continue  # Screenshots require separate visual inspection.
        for category, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append({"file": name, "category": category})
    return {
        "candidate_files": len(candidates),
        "findings": findings,
        "tracked_files": len(
            subprocess.run(
                ["git", "ls-files"], capture_output=True, check=True, text=True
            ).stdout.splitlines()
        ),
        "limitations": (
            "Heuristic scan and exact local-secret matching; "
            "manual artifact/license review still required"
        ),
    }


if __name__ == "__main__":
    result = audit()
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result["findings"]))
