#!/usr/bin/env python3
"""Mochi: native Linux replay capture with an offline voice trigger."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
from datetime import datetime

from PySide6.QtCore import QObject, QProcess, QTimer, Qt, QUrl, Signal, QSize
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap, QColor, QPainter, QFont
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QComboBox, QCheckBox, QFileDialog,
    QListWidget, QListWidgetItem, QProgressBar, QSystemTrayIcon, QMenu, QDialog, QSlider)

from core import recorder_command, request, validate_clip

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "clips"
MODEL = ROOT / "models" / "vosk-model-small-en-us-0.15"


def control_path():
    if os.environ.get('MOCHI_CONTROL_SOCKET'):
        return os.environ['MOCHI_CONTROL_SOCKET']
    base = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/mochi-{os.getuid()}"))
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    return str(base / "mochi-control.sock")


class Events(QObject):
    saved = Signal(str)
    failed = Signal(str)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.root = ROOT
        self.data_root = Path(os.environ.get('MOCHI_DATA_DIR', str(ROOT)))
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.setWindowTitle("Mochi · Your library")
        self.testing = False
        self.voice_restart = False
        self.voice_stopping = False
        self.voice_config = None
        self.voice_error = ''
        self.voice_stderr = ''
        self.last_heard = ''
        self.last_db = -120.0
        self.view = 'library'
        self.metadata = {}
        self.thumb_queue = []
        self.closing = False
        self.settings_path = self.data_root / 'settings.json'
        try:
            self.settings = json.loads(self.settings_path.read_text())
        except (OSError, ValueError):
            self.settings = {}
        self.favorites = set(self.settings.get('favorites', []))
        self.active_phrase = self.settings.get('phrase', 'mochi clip that')
        self.resize(860, 660)
        self.setMinimumSize(640, 640)
        self.runtime = tempfile.TemporaryDirectory(prefix="mochi-")
        self.gsr_socket = Path(self.runtime.name) / "recorder.sock"
        self.recorder = QProcess(self)
        self.recorder.setProcessChannelMode(QProcess.MergedChannels)
        self.recorder.readyReadStandardOutput.connect(self.read_recorder)
        self.recorder.finished.connect(self.recorder_finished)
        self.recorder.errorOccurred.connect(self.recorder_error)
        self.voice = QProcess(self)
        self.voice.readyReadStandardOutput.connect(self.read_voice)
        self.voice.errorOccurred.connect(self.voice_process_error)
        self.voice.readyReadStandardError.connect(self.read_voice_stderr)
        self.voice.finished.connect(self.voice_finished)
        self.voice_data = bytearray()
        self.log = ""
        self.started_at = None
        self.stopping = False
        self.saving = False
        self.worker = None
        self.events = Events()
        self.events.saved.connect(self.clip_saved)
        self.events.failed.connect(self.save_failed)
        self.folder = Path(self.settings.get('folder', str(self.data_root / 'clips')))
        self.build_ui()
        self.setup_tray()
        self.probe = QProcess(self)
        self.probe.finished.connect(self.devices_ready)
        self.probe.errorOccurred.connect(lambda _: self.mic_info.setText('Could not list microphones. Check PipeWire.'))
        self.media = QProcess(self)
        self.media.finished.connect(self.media_ready)
        self.media.errorOccurred.connect(lambda _: self.detail.setText('Could not generate clip previews.'))
        self.refresh_devices()
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self.control_connection)
        # Caller has already checked for a live instance before removing stale sockets.
        QLocalServer.removeServer(control_path())
        if not self.server.listen(control_path()):
            raise RuntimeError(self.server.errorString())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(250)
        self.refresh_clips()
        self.navigate('library')

    def build_ui(self):
        import ui
        ui.build(self)
        self.microphone.addItem('System default microphone', 'default_input')
        self.source.setCurrentIndex(max(0, self.source.findData(self.settings.get('source', 'portal'))))
        self.duration.setCurrentIndex(0 if self.settings.get('duration') == 30 else 1)
        self.gain.setCurrentIndex(max(0, self.gain.findData(self.settings.get('gain', 3))))
        self.phrase.setText(self.active_phrase)
        self.voice_enabled.setChecked(self.settings.get('voice_enabled', True))
        self.microphone.currentIndexChanged.connect(self.mic_changed)
        self.gain.currentIndexChanged.connect(self.voice_settings_changed)
        self.duration.currentIndexChanged.connect(self.save_settings)
        self.source.currentIndexChanged.connect(self.save_settings)
        self.voice_enabled.toggled.connect(self.voice_settings_changed)
        self.update_controls()

    def setup_tray(self):
        icon = QIcon(str(ROOT / 'assets' / 'mochi-app.svg'))
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(QIcon(str(ROOT / 'assets' / 'mochi.svg')), self)
        self.tray.setToolTip("Mochi · replay capture")
        menu = QMenu(self)
        menu.addAction("Show Mochi", self.showNormal)
        menu.addAction("Clip 30 seconds", lambda: self.save_clip(30))
        menu.addAction("Clip 60 seconds", lambda: self.save_clip(60))
        menu.addSeparator()
        menu.addAction("Quit Mochi", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.showNormal() if reason == QSystemTrayIcon.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def control_connection(self):
        client = self.server.nextPendingConnection()
        buffer = bytearray()
        def read():
            buffer.extend(bytes(client.readAll()))
            if len(buffer) > 4096:
                client.disconnectFromServer()
                return
            if b"\n" not in buffer:
                return
            try:
                command = json.loads(buffer.split(b"\n", 1)[0])
                if command.get("name") == "show":
                    self.showNormal()
                    self.raise_()
                    result = {"result": "ok"}
                elif command.get("name") == "status":
                    result = {"result": "ok", "data": {
                        'recording': self.started_at is not None, 'saving': self.saving,
                        'microphone': self.microphone.currentText(), 'input_db': self.last_db,
                        'voice': self.voice_status.text(), 'heard': self.last_heard,
                        'message': self.detail.text(), 'clips': self.clips.count()}}
                elif command.get("name") == "quit":
                    result = {"result": "ok"}
                    QTimer.singleShot(100, self.quit_app)
                elif command.get("name") == "clip":
                    seconds = command.get("data") or (30 if self.duration.currentIndex() == 0 else 60)
                    if seconds not in (30, 60):
                        raise ValueError("Choose 30 or 60 seconds.")
                    if not self.save_clip(seconds):
                        raise ValueError("Buffer is not ready or a save is already running.")
                    result = {"result": "ok", "data": "Save requested; Mochi will confirm completion."}
                else:
                    raise ValueError("Unknown command")
            except Exception as error:
                result = {"result": "error", "data": str(error)}
            client.write((json.dumps(result) + "\n").encode())
            client.disconnectFromServer()
        client.readyRead.connect(read)
        client.disconnected.connect(client.deleteLater)
        if client.bytesAvailable():
            read()

    def toggle_recording(self):
        if self.recorder.state() != QProcess.NotRunning:
            self.stop_recording()
            return
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.detail.setText(str(error))
            return
        self.log = ""
        self.stopping = False
        self.started_at = None
        self.gsr_socket.unlink(missing_ok=True)
        command = recorder_command(self.folder, self.gsr_socket, self.source.currentData(), self.microphone.currentData())
        self.recorder.start(command[0], command[1:])
        self.status.setText("●  Starting")
        self.detail.setText("Choose what to capture in the system screen-sharing dialog.")
        self.start.setText("Stop")
        self.update_controls()

    def read_recorder(self):
        self.log = (self.log + bytes(self.recorder.readAllStandardOutput()).decode(errors="replace"))[-12000:]

    def recorder_error(self, error):
        if error == QProcess.FailedToStart:
            self.status.setText("●  Unavailable")
            self.detail.setText("Install gpu-screen-recorder with IPC support (5.10.2 or later).")
            self.start.setText("Record")
            self.update_controls()

    def recorder_finished(self, code, _status):
        self.read_recorder()
        self.started_at = None
        self.sync_voice()
        self.progress.setValue(0)
        self.start.setText("Record")
        self.status.setText("●  Idle" if self.stopping or code == 0 else "●  Capture failed")
        self.detail.setText("Recording stopped" if self.stopping
                            else "Capture ended. " + (self.log[-650:].strip() or "Try again and select a screen."))
        self.update_controls()

    def stop_recording(self):
        self.stopping = True
        self.started_at = None
        self.sync_voice()
        if self.recorder.state() != QProcess.NotRunning:
            pid = self.recorder.processId()
            if pid:
                os.kill(pid, signal.SIGINT)
            self.status.setText("●  Stopping")
            QTimer.singleShot(5000, self.force_stop_if_stopping)
        self.update_controls()

    def force_stop_if_stopping(self):
        if self.stopping and self.recorder.state() != QProcess.NotRunning:
            self.recorder.kill()

    def tick(self):
        if not self.stopping and self.recorder.state() == QProcess.Running and self.gsr_socket.exists():
            if self.started_at is None:
                self.started_at = time.monotonic()
                self.detail.setText("")
                self.sync_voice()
            elapsed = min(60, int(time.monotonic() - self.started_at))
            self.progress.setValue(elapsed)
            self.status.setText("●  Saving" if self.saving else f"●  Buffering · {elapsed}s" if elapsed < 60 else "●  Recording")
        self.status.setStyleSheet("color: #c7f36b" if self.started_at is not None else "color: #a1aaa4")
        self.update_controls()

    def update_controls(self):
        running = self.recorder.state() != QProcess.NotRunning
        ready = self.started_at is not None and not self.saving and not self.stopping
        self.save30.setEnabled(ready)
        self.save60.setEnabled(ready)
        self.source.setEnabled(not running)
        self.change_folder.setEnabled(not running)
        self.microphone.setEnabled(not running)
        self.refresh_inputs.setEnabled(not running)
        self.start.setEnabled(not self.saving and not (running and self.stopping))

    def set_voice_status(self, text):
        self.voice_status.setText(text)
        lower = text.lower()
        short = 'Listening' if 'listening' in lower else 'Saved' if 'clip saved' in lower else 'Voice off' if 'voice off' in lower else 'Loading' if 'loading' in lower else 'Recognized' if 'recognized' in lower else 'Check mic'
        self.voice_summary.setText(short)
        self.voice_summary.setToolTip(text)

    def read_voice_stderr(self):
        self.voice_stderr = (self.voice_stderr + bytes(self.voice.readAllStandardError()).decode(errors='replace'))[-3000:]

    def voice_process_error(self, error):
        if error == QProcess.FailedToStart:
            self.voice_error = 'Voice worker could not start. Run setup.sh and retry.'
            self.set_voice_status(self.voice_error)

    def sync_voice(self):
        enabled = not self.closing and (self.testing or (self.voice_enabled.isChecked() and self.started_at is not None))
        target = self.microphone.currentData() or 'default_input'
        target = 'auto' if target == 'default_input' else target
        config = (target, self.gain.currentData(), self.active_phrase)
        if self.voice.state() != QProcess.NotRunning:
            if not enabled or self.voice_config != config or self.voice_stopping:
                self.voice_restart = enabled
                self.voice_stopping = True
                self.voice.terminate()
            return
        if not enabled:
            self.meter.setValue(0); self.meter.setFormat('Input level · off')
            self.set_voice_status('Voice off')
            return
        if not (MODEL / 'am' / 'final.mdl').is_file():
            self.set_voice_status('Voice model missing. Run setup.sh first.')
            return
        self.voice_config = config
        self.voice_error = ''; self.voice_stderr = ''; self.voice_data.clear()
        self.set_voice_status('Loading…')
        self.voice.start(sys.executable, [str(ROOT / 'voice.py'), str(MODEL),
            '--target', target, '--gain', str(config[1]), '--phrase', self.active_phrase])

    def read_voice(self):
        self.voice_data.extend(bytes(self.voice.readAllStandardOutput()))
        while b"\n" in self.voice_data:
            line, _, self.voice_data = self.voice_data.partition(b"\n")
            try:
                event = json.loads(line)
            except ValueError:
                continue
            self.handle_voice_event(event)

    def handle_voice_event(self, event):
        kind = event.get('event')
        if kind == 'listening':
            self.set_voice_status('●  Listening · ' + self.microphone.currentText())
        elif kind == 'level':
            self.last_db = float(event['db'])
            self.meter.setValue(max(0, min(60, int(self.last_db + 60))))
            self.meter.setFormat(f'Input signal  ·  {self.last_db:.0f} dBFS')
        elif kind == 'heard':
            self.last_heard = event['text']
            self.heard.setText('Heard: ' + self.last_heard)
        elif kind == 'clip':
            if self.started_at is not None and self.voice_enabled.isChecked():
                if self.save_clip(30 if self.duration.currentIndex() == 0 else 60):
                    self.set_voice_status('●  Command recognized · saving clip')
                else:
                    self.set_voice_status('●  Command recognized · a save is already in progress')
            else:
                self.set_voice_status('●  Command recognized! Start the buffer to save clips.')
                self.heard.setText('✓  Recognized: ' + self.active_phrase)
        elif kind == 'error':
            self.voice_error = event.get('message', 'Voice recognition failed.')
            self.set_voice_status(self.voice_error)

    def voice_finished(self, code, _status):
        self.read_voice(); self.read_voice_stderr()
        self.voice_stopping = False
        self.meter.setValue(0); self.meter.setFormat('Input level · off')
        if self.closing:
            return
        if self.voice_restart:
            self.voice_restart = False
            QTimer.singleShot(0, self.sync_voice)
        elif self.voice_error:
            self.set_voice_status(self.voice_error)
        elif code and (self.testing or self.started_at is not None):
            self.set_voice_status('Voice stopped: ' + (self.voice_stderr[-180:].strip() or 'Retry microphone test.'))
        else:
            self.set_voice_status('Voice off')

    def voice_settings_changed(self):
        self.save_settings()
        self.sync_voice()

    def apply_voice_phrase(self):
        from core import normalized_words
        phrase = ' '.join(normalized_words(self.phrase.text()))
        if len(phrase.split()) < 3 or len(phrase) > 100:
            self.set_voice_status('Use a phrase of at least three English words, up to 100 characters.')
            return
        self.active_phrase = phrase
        self.phrase.setText(phrase)
        self.voice_settings_changed()

    def toggle_mic_test(self):
        self.testing = not self.testing
        self.test_mic.setText('Stop microphone test' if self.testing else 'Test microphone')
        self.sync_voice()
        if self.testing:
            self.detail.setText('Say your clip phrase to test.')

    def refresh_devices(self):
        if self.probe.state() != QProcess.NotRunning:
            return
        self.probe.start(sys.executable, [str(ROOT / 'audio_probe.py')])

    def devices_ready(self, _code, _status):
        try:
            result = json.loads(bytes(self.probe.readAllStandardOutput()))
        except ValueError:
            self.mic_info.setText('Could not list microphones. Check PipeWire / pactl.')
            return
        self.devices = result.get('inputs', [])
        wanted = self.settings.get('microphone') or result.get('default', 'default_input')
        self.microphone.blockSignals(True)
        self.microphone.clear()
        self.microphone.addItem('System default microphone', 'default_input')
        for device in self.devices:
            self.microphone.addItem(device['label'], device['name'])
        self.microphone.setCurrentIndex(max(0, self.microphone.findData(wanted)))
        self.microphone.blockSignals(False)
        self.mic_changed()
        if result.get('error'):
            self.mic_info.setText('Input discovery failed. System default is still available. ' + result['error'])

    def mic_changed(self):
        device = next((d for d in getattr(self, 'devices', []) if d['name'] == self.microphone.currentData()), None)
        if device:
            self.mic_info.setText(f"{'Muted' if device['muted'] else 'Connected'}  ·  System input volume {device['volume']}")
        else:
            self.mic_info.setText('System default')
        self.save_settings()
        self.sync_voice()

    def save_settings(self):
        self.settings.update(folder=str(self.folder), source=self.source.currentData(),
            microphone=self.microphone.currentData(), gain=self.gain.currentData(),
            phrase=self.active_phrase, duration=30 if self.duration.currentIndex() == 0 else 60,
            voice_enabled=self.voice_enabled.isChecked(), favorites=sorted(self.favorites))
        try:
            temp = self.settings_path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.settings, indent=2))
            temp.replace(self.settings_path)
        except OSError as error:
            self.detail.setText('Could not save preferences: ' + str(error))

    def save_clip(self, seconds):
        if self.started_at is None or self.saving or self.stopping:
            return False
        self.saving = True
        self.detail.setText(f"Saving {seconds}s…")
        self.update_controls()
        def work():
            try:
                path = validate_clip(request(self.gsr_socket, "save-replay", {"seconds": seconds}))
                self.events.saved.emit(path)
            except Exception as error:
                self.events.failed.emit(str(error))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        return True

    def clip_saved(self, path):
        self.saving = False
        self.detail.setText("Saved · " + Path(path).name)
        if self.voice.state() == QProcess.Running:
            self.set_voice_status('●  Listening · clip saved')
        self.refresh_clips()
        self.update_controls()
        if self.tray.isVisible():
            self.tray.showMessage("Mochi · clip saved", Path(path).name, QSystemTrayIcon.Information, 2500)

    def save_failed(self, message):
        self.saving = False
        self.detail.setText("Save not confirmed · " + message)
        self.update_controls()

    def navigate(self, view):
        self.view = view
        self.pages.setCurrentIndex({'library': 0, 'favorites': 0, 'audio': 1, 'capture': 2}[view])
        for name, btn in [('library', self.library_nav), ('favorites', self.favorites_nav),
                          ('audio', self.audio_nav), ('capture', self.capture_nav)]:
            btn.setChecked(name == view)
        if view in ('library', 'favorites'):
            self.library_title.setText('Favorites' if view == 'favorites' else 'Library')
            self.filter_clips()

    def refresh_clips(self):
        self.clips.clear()
        try:
            files = list(self.folder.glob('*.mp4')) if self.folder.exists() else []
            sort = self.sort.currentIndex()
            files.sort(key=lambda p: p.stat().st_size if sort == 2 else p.stat().st_mtime, reverse=sort != 1)
            total = sum(p.stat().st_size for p in files)
        except OSError as error:
            self.detail.setText('Could not read clips: ' + str(error)); return
        self.storage_label.setText(f'{len(files)} clips  ·  {total / 1048576:.0f} MB')
        self.thumb_queue = []
        for path in files:
            stat = path.stat()
            item = QListWidgetItem(path.stem.replace('_', ' '))
            item.setData(Qt.UserRole, str(path))
            meta = dict(self.metadata.get(str(path), {}), size=stat.st_size / 1048576,
                        favorite=str(path) in self.favorites,
                        title='Replay · ' + datetime.fromtimestamp(stat.st_mtime).strftime('%H:%M'),
                        date=datetime.fromtimestamp(stat.st_mtime).strftime('%d %b'))
            item.setData(Qt.UserRole + 1, meta)
            item.setToolTip(f'{path.name}\n{datetime.fromtimestamp(stat.st_mtime):%d %b %Y · %H:%M}')
            if meta.get('thumbnail'):
                item.setIcon(QIcon(meta['thumbnail']))
            elif str(path) not in self.metadata:
                self.thumb_queue.append(str(path))
            self.clips.addItem(item)
        self.filter_clips()
        if hasattr(self, 'media'):
            self.next_thumbnail()

    def filter_clips(self):
        needle = self.search.text().lower()
        count = 0
        for i in range(self.clips.count()):
            item = self.clips.item(i)
            visible = needle in item.text().lower() and (self.view != 'favorites' or item.data(Qt.UserRole) in self.favorites)
            item.setHidden(not visible)
            count += visible
        self.count_label.setText(f'{count} clip' + ('' if count == 1 else 's'))
        self.clips.setVisible(count > 0); self.empty.setVisible(count == 0)
        self.empty.setText('No matching clips.' if needle else 'No favorites yet' if self.view == 'favorites'
                           else 'No clips yet')

    def next_thumbnail(self):
        if self.closing or self.media.state() != QProcess.NotRunning or not self.thumb_queue:
            return
        path = self.thumb_queue.pop(0)
        self.media.start(sys.executable, [str(ROOT / 'media_probe.py'), path, str(self.data_root / '.cache' / 'thumbnails')])

    def media_ready(self, _code, _status):
        try:
            meta = json.loads(bytes(self.media.readAllStandardOutput()))
            self.metadata[meta['path']] = meta
            for i in range(self.clips.count()):
                item = self.clips.item(i)
                if item.data(Qt.UserRole) == meta['path']:
                    item.setData(Qt.UserRole + 1, dict(item.data(Qt.UserRole + 1), **meta))
                    if meta.get('thumbnail'):
                        item.setIcon(QIcon(meta['thumbnail']))
        except (ValueError, KeyError):
            pass
        QTimer.singleShot(0, self.next_thumbnail)

    def clip_menu(self, point):
        item = self.clips.itemAt(point)
        if not item:
            return
        path = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.addAction('Play clip', lambda: self.open_clip(item))
        menu.addAction('Remove favorite' if path in self.favorites else 'Add to favorites', lambda: self.favorite_clip(path))
        menu.addAction('Open containing folder', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent))))
        menu.exec(self.clips.viewport().mapToGlobal(point))

    def favorite_clip(self, path):
        if path in self.favorites:
            self.favorites.remove(path)
        else:
            self.favorites.add(path)
        self.save_settings(); self.refresh_clips()

    def open_clip(self, item):
        path = item.data(Qt.UserRole)
        if not path:
            return
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        from PySide6.QtMultimediaWidgets import QVideoWidget
        dialog = QDialog(self); dialog.setWindowTitle(Path(path).name); dialog.resize(960, 600)
        layout = QVBoxLayout(dialog); video = QVideoWidget(); layout.addWidget(video, 1)
        player = QMediaPlayer(dialog); output = QAudioOutput(dialog); output.setVolume(.6)
        player.setAudioOutput(output); player.setVideoOutput(video)
        slider = QSlider(Qt.Horizontal); layout.addWidget(slider)
        player.durationChanged.connect(lambda duration: slider.setRange(0, duration))
        player.positionChanged.connect(lambda position: slider.setValue(position) if not slider.isSliderDown() else None)
        slider.sliderMoved.connect(player.setPosition)
        controls = QHBoxLayout(); pause = QPushButton('Pause'); controls.addWidget(pause)
        def toggle():
            if player.playbackState() == QMediaPlayer.PlayingState:
                player.pause(); pause.setText('Play')
            else:
                player.play(); pause.setText('Pause')
        pause.clicked.connect(toggle)
        external = QPushButton('Open in external player ↗'); controls.addWidget(external)
        external.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        layout.addLayout(controls)
        error = QLabel(); error.setWordWrap(True); layout.addWidget(error)
        player.errorOccurred.connect(lambda _code, message: error.setText(message))
        player.setSource(QUrl.fromLocalFile(path)); player.play()
        dialog.exec(); player.stop()

    def open_folder(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder)))

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Clip destination", str(self.folder))
        if folder:
            self.folder = Path(folder)
            self.folder_label.setText(folder)
            self.save_settings()
            self.refresh_clips()

    def closeEvent(self, event):
        if self.tray.isVisible():
            self.hide()
            event.ignore()
        else:
            self.shutdown()
            event.accept()

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.testing = False
        self.save_settings()
        self.timer.stop()
        for process in [self.media, self.probe]:
            if process.state() != QProcess.NotRunning:
                process.terminate()
                if not process.waitForFinished(1500):
                    process.kill(); process.waitForFinished(1000)
        self.voice.terminate()
        if not self.voice.waitForFinished(4000):
            self.voice.kill()
            self.voice.waitForFinished(1000)
        # Allow a requested save to finish before stopping its recorder.
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=31)
        self.stop_recording()
        if not self.recorder.waitForFinished(5000):
            self.recorder.kill()
            self.recorder.waitForFinished(1000)
        self.server.close()
        self.runtime.cleanup()

    def quit_app(self):
        self.shutdown()
        QApplication.quit()


def main():
    parser = argparse.ArgumentParser(description="Mochi replay capture")
    parser.add_argument("--clip", choices=["30", "60", "default"], help="Ask the running Mochi app to save a replay")
    parser.add_argument('--status', action='store_true', help='Read running app diagnostics')
    parser.add_argument('--quit', action='store_true', help='Stop the running app safely')
    parser.add_argument('--record', action='store_true', help='Start the saved capture source on launch')
    args = parser.parse_args()
    if args.status or args.quit:
        try:
            print(json.dumps(request(control_path(), 'status' if args.status else 'quit', timeout=3), indent=2))
            return 0
        except Exception as error:
            print(str(error), file=sys.stderr); return 1
    if args.clip:
        try:
            print(request(control_path(), "clip", None if args.clip == "default" else int(args.clip), timeout=3))
        except Exception as error:
            print(f"Mochi: {error}", file=sys.stderr)
            return 1
        return 0
    try:
        request(control_path(), "show", timeout=1)
        return 0
    except (OSError, RuntimeError):
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("Mochi")
    app.setDesktopFileName("io.github.deltadasher.Mochi")
    window = Window()
    window.show()
    signal.signal(signal.SIGTERM, lambda *_: window.quit_app())
    signal.signal(signal.SIGINT, lambda *_: window.quit_app())
    if args.record:
        QTimer.singleShot(700, window.toggle_recording)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
