"""A check that keeps anything real out of this public repository.

Finds, in tracked text files: a real insurer's name, a telephone number outside the fictional range (area code any, exchange 555, line 0100 to 0199), an internal
host name, and anything shaped like a credential. It reports the file, line and KIND of each finding and never the matched text, so a finding cannot leak what it found.

Run it:  python -m fake_payer.real_data_check        (exit status 1 if anything is found)
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Real dental and health insurers. Matching is by whole word, ignoring case. This file is exempt from the check (it has to name them).
REAL_INSURERS = (
    "delta dental", "metlife", "cigna", "aetna", "guardian life", "guardian dental", "humana", "unitedhealthcare", "united healthcare", "anthem",
    "blue cross", "blue shield", "principal financial", "sun life", "ameritas", "careington", "aflac", "spirit dental", "dentaquest", "geha",
    "united concordia", "premera", "kaiser", "healthpartners", "emblemhealth", "wellcare", "centene", "molina", "carefirst", "highmark",
)
# Internal names that identify the author's own systems.
INTERNAL_NAMES = ("cyberiad", "builder-vm")

_PHONE = re.compile(r"(?<![\w.])(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-](\d{3})[\s.-](\d{4})(?!\d)|(?<![\w.])\+1(\d{3})(\d{3})(\d{4})(?!\d)")
_CREDENTIALS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9/+_=-]{24,}['\"]"),
)

EXEMPT = {"fake_payer/real_data_check.py", "tests/test_real_data_check.py"}
SKIP_DIRS = {".git", ".venv", "calls", "__pycache__", ".pytest_cache", "node_modules", "site-packages", "fake_payer.egg-info"}
SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".zip"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str                                    # insurer | phone | internal-name | credential

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}"


def _fictional(exchange: str, line: str) -> bool:
    return exchange == "555" and "0100" <= line <= "0199"


def scan_text(text: str, path: str = "<text>") -> List[Finding]:
    findings: List[Finding] = []
    insurer_patterns = [re.compile(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", re.I) for name in REAL_INSURERS]
    for number, line in enumerate(text.splitlines(), start=1):
        if any(p.search(line) for p in insurer_patterns):
            findings.append(Finding(path, number, "insurer"))
        if any(name in line.lower() for name in INTERNAL_NAMES):
            findings.append(Finding(path, number, "internal-name"))
        for match in _PHONE.finditer(line):
            groups = [g for g in match.groups() if g]
            if len(groups) == 3 and not _fictional(groups[1], groups[2]):
                findings.append(Finding(path, number, "phone"))
        if any(p.search(line) for p in _CREDENTIALS):
            findings.append(Finding(path, number, "credential"))
    return findings


def _tracked_files(root: Path) -> List[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root, capture_output=True, text=True, check=True).stdout
        return [root / line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in root.rglob("*") if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS)]


def scan_repo(root: Optional[Path] = None) -> List[Finding]:
    root = root or Path(__file__).resolve().parent.parent
    findings: List[Finding] = []
    for path in _tracked_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in EXEMPT or set(path.relative_to(root).parts) & SKIP_DIRS or path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(scan_text(text, relative))
    return findings


def main() -> int:
    findings = scan_repo()
    for finding in findings:
        print(finding)
    if findings:
        print(f"{len(findings)} finding(s): the matched text is not shown; open the line to see it", file=sys.stderr)
        return 1
    print("no real data found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
