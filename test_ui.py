"""Native integration tests use an isolated profile and a simulated recorder."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import mochi


class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, MOCHI_DATA_DIR=str(self.root),
                              MOCHI_CONTROL_SOCKET=str(self.root / 'ui.sock'))
        self.env.start()
        # Do not enumerate or open real microphones in a simulated test.
        self.devices = patch.object(mochi.Window, 'refresh_devices', lambda self: None)
        self.devices.start()
        self.w = mochi.Window()
        self.w.voice_enabled.setChecked(False)
        self.w.show()
        self.fake = self.root / 'fake.py'
        self.fake.write_text('''import socket,sys,json,pathlib
server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
server.bind(sys.argv[1]);server.listen(2)
while True:
 c,_=server.accept()
 with c:
  data=b''
  while b'\\n' not in data:data+=c.recv(4096)
  req=json.loads(data)
  path=pathlib.Path(sys.argv[2])/('test-'+str(req['data']['seconds'])+'.mp4')
  path.write_bytes(b'test fixture')
  c.sendall((json.dumps({'result':'ok','data':str(path)})+'\\n').encode())
''')
        self.command = patch.object(mochi, 'recorder_command',
            lambda folder, sock, source, mic: [sys.executable, str(self.fake), str(sock), str(folder)])
        self.command.start()

    def tearDown(self):
        self.w.shutdown()
        self.w.deleteLater(); self.app.processEvents()
        self.command.stop(); self.devices.stop(); self.env.stop(); self.temp.cleanup()

    def wait_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(50)
        self.assertTrue(predicate())

    def test_clip_buttons_library_search_favorites_and_stop(self):
        self.assertFalse(self.w.save30.isEnabled())
        self.w.start.click()
        self.wait_until(lambda: self.w.started_at is not None)
        self.assertFalse(self.w.microphone.isEnabled())
        for seconds, button in [(30, self.w.save30), (60, self.w.save60)]:
            button.click()
            self.assertFalse(self.w.save30.isEnabled())
            self.wait_until(lambda: not self.w.saving)
            self.assertTrue((self.root / 'clips' / f'test-{seconds}.mp4').exists())
            self.assertTrue(self.w.detail.text().startswith('Saved'))
        self.assertEqual(self.w.clips.count(), 2)
        self.w.search.setText('not present')
        self.assertTrue(self.w.empty.isVisible())
        self.w.search.clear()
        self.w.favorite_clip(str(self.root / 'clips' / 'test-30.mp4'))
        self.w.navigate('favorites')
        self.assertEqual(self.w.count_label.text(), '1 clip')
        self.w.start.click()
        self.wait_until(lambda: self.w.recorder.state() == mochi.QProcess.NotRunning)
        self.assertFalse(self.w.save30.isEnabled())
        self.assertTrue(self.w.microphone.isEnabled())

    def test_voice_events_save_only_when_enabled_and_buffering(self):
        self.w.handle_voice_event({'event': 'level', 'db': -24})
        self.assertEqual(self.w.meter.value(), 36)
        self.w.handle_voice_event({'event': 'heard', 'text': 'mochi clip that'})
        self.assertIn('mochi clip that', self.w.heard.text())
        self.w.handle_voice_event({'event': 'clip'})
        self.assertFalse(self.w.saving)
        self.assertIn('Start the buffer', self.w.voice_status.text())
        self.w.start.click()
        self.wait_until(lambda: self.w.started_at is not None)
        with patch.object(self.w, 'sync_voice', lambda: None):
            self.w.voice_enabled.setChecked(True)
        self.w.duration.setCurrentIndex(0)
        self.w.handle_voice_event({'event': 'clip'})
        self.wait_until(lambda: not self.w.saving)
        self.assertTrue((self.root / 'clips' / 'test-30.mp4').exists())
        self.w.handle_voice_event({'event': 'error', 'message': 'Input disappeared'})
        self.assertIn('Input disappeared', self.w.voice_status.text())

    def test_preferences_persist(self):
        self.w.duration.setCurrentIndex(0)
        self.w.phrase.setText('save my replay')
        self.w.apply_voice_phrase()
        settings = json.loads((self.root / 'settings.json').read_text())
        self.assertEqual(settings['duration'], 30)
        self.assertEqual(settings['phrase'], 'save my replay')
