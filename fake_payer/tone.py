"""The dial tone: what a caller hears once the other end has hung up. The line plays it after it hangs up, and hangs up itself when it hears it.

Audio is PCM16 mono at 8 kHz."""
from __future__ import annotations

import math
import struct

SAMPLE_RATE = 8000
WINDOW_SAMPLES = 800                # 100 ms
MIN_RMS = 150                       # quieter than this is silence or line noise, not a tone


def _goertzel_power(samples, freq: int) -> float:
    coeff = 2 * math.cos(2 * math.pi * freq / SAMPLE_RATE)
    s1 = s2 = 0.0
    for x in samples:
        s1, s2 = x + coeff * s1 - s2, s1
    return s1 * s1 + s2 * s2 - coeff * s1 * s2


DIAL_TONE_FREQUENCIES = (350, 440)
DIAL_TONE_AMPLITUDE = 5000
DIAL_TONE_MIN_SHARE_EACH = 0.3      # each of the two frequencies must hold at least this share of a window's energy...
DIAL_TONE_MIN_SHARE_BOTH = 0.7      # ...and together at least this much (440 Hz alone, half of the ringing tone, does not pass)
DIAL_TONE_WINDOWS = 5               # in a row (half a second) before it is called a dial tone


def dial_tone_pcm(seconds: float) -> bytes:
    """The North American dial tone (350 Hz and 440 Hz together, steady) as PCM16 mono at 8 kHz."""
    out = bytearray()
    total = int(seconds * SAMPLE_RATE)
    for n in range(total):
        t = n / SAMPLE_RATE
        edge = min(1.0, n / 80, (total - n) / 80)
        out += struct.pack("<h", int(DIAL_TONE_AMPLITUDE * edge * (math.sin(2 * math.pi * 350 * t) + math.sin(2 * math.pi * 440 * t))))
    return bytes(out)


def window_is_dial_tone(samples) -> bool:
    n = len(samples)
    energy = sum(x * x for x in samples)
    if n == 0 or math.sqrt(energy / n) < MIN_RMS:
        return False
    shares = [_goertzel_power(samples, f) / (energy * n / 2) for f in DIAL_TONE_FREQUENCIES]
    return min(shares) >= DIAL_TONE_MIN_SHARE_EACH and sum(shares) >= DIAL_TONE_MIN_SHARE_BOTH


class DialToneDetector:
    """Feed it the line's audio as it arrives; `feed` says whether the dial tone has now been heard for half a second running."""

    def __init__(self) -> None:
        self._pending = bytearray()
        self._run = 0

    def feed(self, pcm: bytes) -> bool:
        self._pending += pcm
        step = WINDOW_SAMPLES * 2
        while len(self._pending) >= step:
            window = struct.unpack("<%dh" % WINDOW_SAMPLES, bytes(self._pending[:step]))
            del self._pending[:step]
            self._run = self._run + 1 if window_is_dial_tone(window) else 0
        return self._run >= DIAL_TONE_WINDOWS
