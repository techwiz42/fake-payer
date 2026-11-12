"""The call log: one JSON file per call in a local directory, keyed by the call's reference number.

What is kept: the time, the caller's number masked to its last four digits, the reference number, the transcript (with the lookup results, in full, so what the representative
said can be checked against them), the figures the representative stated, any that no lookup returned, and how the call ended. Files are private to the user running the line,
because the line records the words of anyone who calls, and are removed after a retention period.
"""
from __future__ import annotations

import datetime
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .grounding import check_turns, spoken_figures


def mask(last4: str) -> str:
    return "withheld" if last4 in ("", "none") else f"***{last4}"


def turns_from_transcript(transcript: Iterable[Any]) -> List[Dict[str, Any]]:
    """The bridge's turn records as log turns: the shape `grounding.check_turns` reads."""
    turns: List[Dict[str, Any]] = []
    for record in transcript:
        if record.role == "tool_result":
            try:
                content: Any = json.loads(record.text)
            except ValueError:
                content = record.text
            turns.append({"role": "tool_result", "content": content})
        elif record.role in ("user", "assistant"):
            turns.append({"role": record.role, "text": record.text})
    return turns


def ended_by(terminated_by: Optional[str]) -> str:
    return {"watchdog": "time_limit", "error": "error"}.get(terminated_by or "", "caller_hung_up")


class CallLog:
    def __init__(self, directory: str, clock=time.time):
        self._dir = Path(directory)
        self._clock = clock

    def _now(self) -> datetime.datetime:
        return datetime.datetime.fromtimestamp(self._clock(), datetime.timezone.utc)

    def write(self, *, reference: str, last4: str, ended: str, duration_seconds: float = 0.0, turns: Optional[List[Dict[str, Any]]] = None) -> Path:
        turns = turns or []
        stated = [str(f) for turn in turns if turn["role"] == "assistant" for f in spoken_figures(turn["text"])]
        record = {
            "time": self._now().isoformat(),
            "reference": reference,
            "caller": mask(last4),
            "ended": ended,
            "duration_seconds": round(duration_seconds, 1),
            "stated_figures": stated,
            "ungrounded_figures": [f"{index}: {figure}" for index, figure in check_turns(turns)],
            "turns": turns,
        }
        self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._dir / f"{self._now().strftime('%Y%m%dT%H%M%SZ')}-{reference}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=1)
        return path

    def _files(self) -> List[Path]:
        return sorted(self._dir.glob("*.json"), reverse=True) if self._dir.is_dir() else []

    def get(self, reference: str) -> Optional[Dict[str, Any]]:
        for path in self._files():
            if path.stem.endswith(f"-{reference}"):
                return json.loads(path.read_text(encoding="utf-8"))
        return None

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        out = []
        for path in self._files()[:limit]:
            record = json.loads(path.read_text(encoding="utf-8"))
            out.append({key: record[key] for key in ("time", "reference", "caller", "ended", "duration_seconds")} | {"turns": len(record["turns"]), "ungrounded": len(record["ungrounded_figures"])})
        return out

    def cleanup(self, retention_days: float) -> int:
        """Remove entries older than the retention period. Returns how many were removed."""
        cutoff = self._now() - datetime.timedelta(days=retention_days)
        removed = 0
        for path in self._files():
            try:
                when = datetime.datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["time"])
            except (ValueError, KeyError, OSError):
                when = datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.timezone.utc)
            if when < cutoff:
                path.unlink()
                removed += 1
        return removed
