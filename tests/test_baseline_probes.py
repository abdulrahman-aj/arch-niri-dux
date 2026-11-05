import math
from pathlib import Path
import struct
import tempfile
import unittest
import wave


from scripts.verification import apps as baseline


class AudioEvidenceTests(unittest.TestCase):
    def amplitude(self, frequency, target=660):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'recording.wav'
            rate = 48000
            samples = [int(4000 * math.sin(2 * math.pi * frequency * n / rate))
                       if frequency else 0 for n in range(rate // 10)]
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(rate)
                output.writeframes(struct.pack('<' + 'h' * len(samples), *samples))
            return baseline.tone_amplitude(path, target)

    def test_silence_or_an_unrelated_tone_cannot_qualify_playback(self):
        self.assertEqual(self.amplitude(0), 0)
        self.assertLess(self.amplitude(440), 1)
        self.assertGreater(self.amplitude(660), 3900)
        self.assertGreater(self.amplitude(440, target=440), 3900)
