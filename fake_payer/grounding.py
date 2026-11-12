"""Checks that what the line said about money, percentages and frequencies came from the lookup.

The model is told never to state a coverage figure that the lookup did not return, and a prompt is not a guarantee. This reads what was actually said (digits or number
words) and reports every figure attached to a unit (percent, dollars, times) that is not among the numbers in the tool results of the same call.

Only figures with a unit are checked: "one moment please" and a member number read back are not coverage facts. The check is for tests and for reviewing call logs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_UNITS = {"percent": "percent", "%": "percent", "dollar": "dollars", "dollars": "dollars", "time": "times", "times": "times"}
_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_ADVERBS = {"once": 1, "twice": 2}
_NUMBER_WORDS = set(_ONES) | set(_TENS) | {"hundred", "thousand", "and"}
_DIGITS = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


@dataclass(frozen=True)
class Figure:
    value: float
    unit: str

    def __str__(self) -> str:
        if self.value != self.value:
            return f"an unreadable figure in {self.unit}"
        shown = int(self.value) if float(self.value).is_integer() else self.value
        return f"{shown} {self.unit}"


def _words_to_number(words: Sequence[str]) -> Optional[int]:
    """'eighty' -> 80, 'fifteen hundred' -> 1500, 'one thousand five hundred and ten' -> 1510. None when the words are not one well-formed number: "thirteen twenty" could
    be 1320 or nothing, and is not guessed at."""
    total, current, seen = 0, 0, False
    last = None                                   # the kind of the previous word in this group: "ones", "teens", "tens", "hundred", "thousand"
    for word in words:
        if word == "and":
            continue
        seen = True
        if word in _ONES:
            kind = "ones" if _ONES[word] < 10 else "teens"
            if last in ("ones", "teens", "tens") and not (last == "tens" and kind == "ones"):
                return None
            current += _ONES[word]
        elif word in _TENS:
            if last in ("ones", "teens", "tens"):
                return None
            kind = "tens"
            current += _TENS[word]
        elif word == "hundred":
            if last not in ("ones", "teens", None) or current >= 100:
                return None
            kind = "hundred"
            current = (current or 1) * 100
        elif word == "thousand":
            if last in ("thousand",) or current >= 1000:
                return None
            kind = "thousand"
            total += (current or 1) * 1000
            current = 0
        else:
            return None
        last = kind
    return total + current if seen else None


def spoken_figures(text: str) -> List[Figure]:
    """Every figure in `text` that has a unit after it ("80 percent", "eighty percent", "$1,500", "twice") or a dollar sign before it."""
    figures: List[Figure] = []
    lowered = text.lower().replace("%", " % ")
    # digits
    for match in _DIGITS.finditer(lowered):
        token = match.group(0)
        after = lowered[match.end():].split()[:1]
        unit = "percent" if token.endswith("%") else None
        if unit is None and after and after[0].strip(".,;:!?") in _UNITS:
            unit = _UNITS[after[0].strip(".,;:!?")]
        if unit is None and token.startswith("$"):
            unit = "dollars"
        if unit is None and lowered[:match.start()].rstrip().endswith("$"):
            unit = "dollars"
        if unit:
            figures.append(Figure(float(token.strip("$%").replace(",", "")), unit))
    # words
    tokens = re.findall(r"[a-z%$]+|\d[\d,.]*", lowered.replace("-", " "))
    i = 0
    while i < len(tokens):
        if tokens[i] in _ADVERBS:
            figures.append(Figure(float(_ADVERBS[tokens[i]]), "times"))
            i += 1
            continue
        if tokens[i] in _NUMBER_WORDS and tokens[i] != "and":
            j = i
            while j < len(tokens) and tokens[j] in _NUMBER_WORDS:
                j += 1
            run = tokens[i:j]
            while run and run[-1] == "and":
                run = run[:-1]
            unit = _UNITS.get(tokens[j]) if j < len(tokens) else None
            value = _words_to_number(run)
            if unit:
                # An unreadable number before a unit is kept (as NaN, which equals nothing) so the check fails loudly instead of skipping it.
                figures.append(Figure(float(value) if value is not None else float("nan"), unit))
            i = j
            continue
        i += 1
    return figures


def numbers_in(value: Any) -> Set[float]:
    """Every number in a tool result: JSON numbers, and digits inside strings (such as "2 per calendar year")."""
    found: Set[float] = set()
    if isinstance(value, bool) or value is None:
        return found
    if isinstance(value, (int, float)):
        found.add(float(value))
    elif isinstance(value, str):
        for match in re.finditer(r"\d[\d,]*(?:\.\d+)?", value):
            found.add(float(match.group(0).replace(",", "")))
    elif isinstance(value, dict):
        for item in value.values():
            found |= numbers_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= numbers_in(item)
    return found


def unsupported_figures(spoken: str, tool_results: Iterable[Any]) -> List[Figure]:
    """Figures in `spoken` that no tool result contains."""
    allowed: Set[float] = set()
    for result in tool_results:
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                pass
        allowed |= numbers_in(result)
    return [figure for figure in spoken_figures(spoken) if figure.value not in allowed]


def check_turns(turns: Iterable[Dict[str, Any]]) -> List[Tuple[int, Figure]]:
    """Walk a call's turns in order: each assistant turn may state only figures from the tool results seen so far in the call. Returns (turn index, figure) for each breach.

    A turn is {"role": "assistant", "text": ...} or {"role": "tool_result", "content": <the result, as data or a JSON string>}.
    """
    results: List[Any] = []
    breaches: List[Tuple[int, Figure]] = []
    for index, turn in enumerate(turns):
        if turn.get("role") == "tool_result":
            results.append(turn.get("content"))
        elif turn.get("role") == "assistant":
            breaches.extend((index, figure) for figure in unsupported_figures(turn.get("text", ""), results))
    return breaches
