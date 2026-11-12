import math
import struct

from fake_payer.tone import DialToneDetector, dial_tone_pcm


def pcm(freqs, seconds=1.0, amp=3000):
    return b"".join(struct.pack("<h", int(amp * sum(math.sin(2 * math.pi * f * n / 8000) for f in freqs) / len(freqs))) for n in range(int(seconds * 8000)))


def test_a_dial_tone_is_recognised_after_half_a_second_and_not_before():
    tone = dial_tone_pcm(1.0)
    detector = DialToneDetector()
    assert detector.feed(tone[:6400]) is False
    assert detector.feed(tone[6400:12800]) is True


def test_ringing_other_sounds_and_silence_are_not_a_dial_tone():
    for sound in (pcm((440, 480)), pcm((300, 1200, 2500)), pcm((440,)), bytes(16000)):
        assert DialToneDetector().feed(sound) is False
