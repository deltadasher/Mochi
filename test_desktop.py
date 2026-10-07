import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from install_desktop import APP_ID, data_home, install


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / 'Mochi'
        self.prepare(self.app)
        self.data = self.root / 'data'

    def prepare(self, app):
        (app / 'assets').mkdir(parents=True)
        (app / '.venv/bin').mkdir(parents=True)
        (app / '.venv/bin/python').symlink_to(sys.executable)
        (app / 'run.sh').write_text('#!/usr/bin/env bash\npwd > launched\n')
        (app / 'run.sh').chmod(0o755)
        (app / 'assets/mochi-app.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')

    def test_install_and_desktop_validation(self):
        entry = install(self.app, self.data)
        self.assertEqual(entry.name, APP_ID + '.desktop')
        self.assertTrue((self.data / 'icons/hicolor/scalable/apps' / (APP_ID + '.svg')).exists())
        text = entry.read_text()
        self.assertIn('Name=Mochi\n', text)
        self.assertIn('Terminal=false', text)
        self.assertNotIn('NoDisplay=true', text)
        self.assertNotIn('--record', text)
        if shutil.which('desktop-file-validate'):
            subprocess.run(['desktop-file-validate', str(entry)], check=True)

    def test_reinstall_updates_checkout_without_duplicates(self):
        entry = install(self.app, self.data)
        moved = self.root / 'Moved Mochi'
        self.prepare(moved)
        self.assertEqual(install(moved, self.data), entry)
        self.assertIn(str(moved), entry.read_text())
        self.assertEqual(len(list(entry.parent.glob('*.desktop'))), 1)

    def test_unprepared_install_does_not_create_entry(self):
        (self.app / '.venv/bin/python').unlink()
        with self.assertRaisesRegex(ValueError, 'setup.sh'):
            install(self.app, self.data)
        self.assertFalse(self.data.exists())

    def test_xdg_data_home(self):
        with patch.dict(os.environ, XDG_DATA_HOME=str(self.data)):
            self.assertEqual(data_home(), self.data)
        with patch.dict(os.environ, XDG_DATA_HOME='relative/invalid'):
            self.assertEqual(data_home(), Path.home() / '.local/share')

    def test_exec_handles_special_checkout_characters(self):
        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio
        except ImportError:
            self.skipTest('PyGObject is optional; needed for actual desktop launch validation')
        unusual = self.root / 'Mochi space "quote" $dollar `tick` \\slash %percent =equals'
        self.prepare(unusual)
        entry = install(unusual, self.data)
        if shutil.which('desktop-file-validate'):
            subprocess.run(['desktop-file-validate', str(entry)], check=True)
        info = Gio.DesktopAppInfo.new_from_filename(str(entry))
        self.assertIsNotNone(info)
        self.assertTrue(info.should_show())
        self.assertTrue(info.launch([], None))
        marker = unusual / 'launched'
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(marker.read_text().strip(), str(unusual))
