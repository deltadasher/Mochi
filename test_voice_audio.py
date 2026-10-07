"""Real Vosk decoding of local synthesized fixtures; no microphone/network."""
from pathlib import Path
import json
import subprocess
import unittest
from core import PhraseTrigger
from voice import make_recognizer, pcm_level

ROOT = Path(__file__).resolve().parent

class AudioRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from vosk import Model, SetLogLevel
        except ImportError:
            raise unittest.SkipTest('Install voice dependencies first')
        path = ROOT / 'models/vosk-model-small-en-us-0.15'
        if not path.exists():
            raise unittest.SkipTest('Download voice model first')
        SetLogLevel(-1)
        cls.model = Model(str(path))

    def decode(self, filename, gain=1):
        data = subprocess.check_output(['ffmpeg','-v','error','-i',str(ROOT/'tests/fixtures'/filename),
                                        '-ar','16000','-ac','1','-f','s16le','-']) + bytes(64000)
        rec = make_recognizer(self.model, 'mochi clip that')
        trigger = PhraseTrigger(); fires = 0
        for index in range(0, len(data), 3200):
            audio, _ = pcm_level(data[index:index+3200], gain)
            final = rec.AcceptWaveform(audio)
            result = json.loads(rec.Result() if final else rec.PartialResult())
            fires += trigger.feed(result.get('text' if final else 'partial', ''), index/32000, final)
        return fires

    def test_spoken_mochi_command_triggers_once(self):
        self.assertEqual(self.decode('clip-command.wav'), 1)

    def test_spoken_mochi_command_with_default_boost(self):
        self.assertEqual(self.decode('clip-command.wav', 3), 1)

    def test_unrelated_speech_and_clip_without_wake_word_do_not_trigger(self):
        self.assertEqual(self.decode('unrelated-speech.wav'), 0)
