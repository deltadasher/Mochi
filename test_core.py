import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from core import is_clip_phrase, recorder_command, request, validate_clip


class CoreTests(unittest.TestCase):
    def test_voice_phrase_boundaries(self):
        for text in ["Mochi, clip that!", "hey mochi clip that please"]:
            self.assertTrue(is_clip_phrase(text))
        for text in ["clip that", "mochi", "mochi clip", "mochi clipped that", "mochik clip that", ""]:
            self.assertFalse(is_clip_phrase(text))

    def test_command_preserves_path_and_audio_mix(self):
        cmd = recorder_command('/tmp/clips with spaces', '/tmp/recorder.sock')
        self.assertEqual(cmd[cmd.index('-a') + 1], 'default_output|default_input')
        self.assertEqual(cmd[cmd.index('-o') + 1], '/tmp/clips with spaces')
        self.assertEqual(cmd[cmd.index('-r') + 1], '60')

    def test_clip_must_exist_and_be_nonempty(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'clip.mp4'
            with self.assertRaises(RuntimeError):
                validate_clip(str(path))
            path.touch()
            with self.assertRaises(RuntimeError):
                validate_clip(str(path))
            path.write_bytes(b'fixture')
            self.assertEqual(validate_clip(str(path)), str(path))

    def exchange(self, response):
        with tempfile.TemporaryDirectory() as temp:
            sock = str(Path(temp) / 'recorder.sock')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(sock)
                server.listen(1)
                seen = []
                def respond():
                    client, _ = server.accept()
                    with client:
                        raw = b''
                        while b'\n' not in raw:
                            raw += client.recv(4096)
                        seen.append(json.loads(raw))
                        for chunk in response:
                            client.sendall(chunk)
                thread = threading.Thread(target=respond)
                thread.start()
                try:
                    result = request(sock, 'save-replay', {'seconds': 30}, timeout=2)
                finally:
                    thread.join(timeout=3)
                self.assertEqual(seen[0]['data'], {'seconds': 30})
                return result

    def test_fragmented_reply(self):
        result = self.exchange([b'{"result":"ok",', b'"data":"/tmp/clip.mp4"}\n'])
        self.assertEqual(result, '/tmp/clip.mp4')

    def test_recorder_error_propagates(self):
        with self.assertRaisesRegex(RuntimeError, 'Disk full'):
            self.exchange([b'{"result":"error","data":"Disk full"}\n'])

    def test_disconnect_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, 'without confirming'):
            self.exchange([])

class VoiceRegressionTests(unittest.TestCase):
    def test_stable_partial_then_final_fires_once(self):
        from core import PhraseTrigger
        t = PhraseTrigger()
        self.assertFalse(t.feed('mochi clip', 0))
        self.assertFalse(t.feed('mochi clip that', 1))
        self.assertTrue(t.feed('mochi clip that', 1.3))
        self.assertFalse(t.feed('mochi clip that', 2, final=True))
        self.assertFalse(t.feed('mochi clip that', 3, final=True))
        self.assertTrue(t.feed('mochi clip that', 6, final=True))

    def test_partial_correction_does_not_fire(self):
        from core import PhraseTrigger
        t = PhraseTrigger()
        self.assertFalse(t.feed('mochi clip that', 1))
        self.assertFalse(t.feed('mochi clip', 1.1))
        self.assertFalse(t.feed('mochi clip', 2, final=True))

    def test_split_wake_word_and_custom_phrase(self):
        self.assertTrue(is_clip_phrase('mo chi clip that'))
        self.assertTrue(is_clip_phrase('hey save my replay', 'save my replay'))
        self.assertFalse(is_clip_phrase('clip that please'))
        self.assertFalse(is_clip_phrase('mochi clip that', 'save my replay'))

    def test_voice_gain_and_silence(self):
        import array
        from voice import pcm_level
        audio, db = pcm_level(bytes(3200), 3)
        self.assertEqual(audio, bytes(3200))
        self.assertEqual(db, -120)
        raw = array.array('h', [20000, -20000]).tobytes()
        audio, db = pcm_level(raw, 3)
        self.assertEqual(list(array.array('h', audio)), [32767, -32768])
        self.assertGreater(db, -5)

    def test_explicit_microphone_is_used_in_clip_audio(self):
        command = recorder_command('/tmp/clips', '/tmp/rec.sock', 'screen', 'alsa_input.usb-fifine')
        self.assertEqual(command[command.index('-a') + 1], 'default_output|device:alsa_input.usb-fifine')


if __name__ == '__main__':
    unittest.main()
