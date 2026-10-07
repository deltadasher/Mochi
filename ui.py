"""Native Medal-inspired library, capture toolbar and audio diagnostics."""
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import Qt, QSize, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QFont, QPen, QIcon, QPainterPath
from PySide6.QtWidgets import (QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QComboBox, QCheckBox, QLineEdit, QProgressBar, QListWidget,
    QAbstractItemView, QStackedWidget, QStyledItemDelegate, QStyle, QScrollArea)


def label(text, name=''):
    item = QLabel(text)
    if name:
        item.setObjectName(name)
    item.setWordWrap(True)
    return item


def button(text, callback, name=''):
    item = QPushButton(text)
    if name:
        item.setObjectName(name)
    item.clicked.connect(callback)
    item.setCursor(Qt.PointingHandCursor)
    return item


def panel():
    frame = QFrame()
    frame.setObjectName('panel')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(24, 22, 24, 22)
    layout.setSpacing(16)
    return frame, layout


class Notice(QLabel):
    """Short-lived confirmations; actionable errors remain visible."""
    def __init__(self):
        super().__init__('')
        self.setObjectName('notice')
        self.setWordWrap(True)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def setText(self, text):
        super().setText(text)
        self.timer.stop()
        self.setVisible(bool(text))
        if text and not any(word in text.lower() for word in ['error', 'failed', 'could not', 'not confirmed', 'install', 'denied', 'unavailable']):
            self.timer.start(5000)


class ClipGrid(QListWidget):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = max(260, self.viewport().width() - 8)
        columns = max(1, width // 275)
        tile = (width - columns * 8) // columns
        self.setGridSize(QSize(tile, int((tile - 12) * 9 / 16) + 78))


class ClipDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(5, 5, -5, -5)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        painter.setPen(QPen(QColor('#f6b84c' if selected else '#45464e' if hovered else '#303137'), 1))
        painter.setBrush(QColor('#25262b' if hovered else '#1d1e22'))
        painter.drawRoundedRect(r, 10, 10)
        image_rect = r.adjusted(1, 1, -1, 0)
        image_rect.setHeight(int(image_rect.width() * 9 / 16))
        painter.save()
        clip = QPainterPath(); clip.addRoundedRect(QRectF(image_rect), 9, 9); painter.setClipPath(clip)
        painter.fillRect(image_rect, QColor('#0c0d10'))
        icon = index.data(Qt.DecorationRole)
        if icon is not None:
            pixmap = icon.pixmap(QSize(500, 300))
            if not pixmap.isNull():
                fitted = pixmap.scaled(image_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                painter.drawPixmap(image_rect.center().x() - fitted.width() // 2,
                                   image_rect.center().y() - fitted.height() // 2, fitted)
        else:
            painter.setPen(QColor('#6f727e'))
            painter.drawText(image_rect, Qt.AlignCenter, 'Preparing preview…')
        painter.restore()
        meta = index.data(Qt.UserRole + 1) or {}
        duration = meta.get('duration')
        if duration is not None:
            length = f'{int(duration)//60}:{int(duration)%60:02}'
            badge = image_rect.adjusted(image_rect.width()-59, image_rect.height()-28, -8, -7)
            painter.fillRect(badge, QColor('#cc101114'))
            painter.setPen(QColor('#ffffff'))
            painter.setFont(QFont('DejaVu Sans', 9, QFont.Bold))
            painter.drawText(badge, Qt.AlignCenter, length)
        painter.setPen(QColor('#f4f4f7'))
        painter.setFont(QFont('DejaVu Sans', 10, QFont.DemiBold))
        text_rect = r.adjusted(13, image_rect.height() + 8, -13, -30)
        title = meta.get('title') or index.data(Qt.DisplayRole) or 'Replay'
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(title, Qt.ElideRight, text_rect.width()))
        painter.setFont(QFont('DejaVu Sans', 8))
        painter.setPen(QColor('#a0a2ad'))
        painter.drawText(r.adjusted(13, image_rect.height() + 34, -13, -8), Qt.AlignVCenter,
                         f"{meta.get('date', '')}  ·  {meta.get('size', 0):.0f} MB")
        if meta.get('favorite'):
            painter.setPen(QColor('#f6b84c'))
            painter.drawText(image_rect.adjusted(10, 7, -10, -100), Qt.AlignLeft, '★')
        painter.restore()

    def sizeHint(self, option, index):
        return option.widget.gridSize() if isinstance(option.widget, QListWidget) else QSize(280, 230)


def build(window):
    w = window
    w.resize(1180, 800)
    w.setMinimumSize(950, 700)
    root = QWidget()
    w.setCentralWidget(root)
    shell = QHBoxLayout(root)
    shell.setContentsMargins(0, 0, 0, 0)
    shell.setSpacing(0)
    side = QFrame(); side.setObjectName('sidebar'); side.setFixedWidth(190)
    sl = QVBoxLayout(side); sl.setContentsMargins(16, 28, 16, 22); sl.setSpacing(9)
    brand = QHBoxLayout(); brand.setSpacing(10)
    mark = QLabel(); mark.setPixmap(QIcon(str(w.root / 'assets/mochi.svg')).pixmap(36, 36))
    mark.setFixedSize(36, 36); brand.addWidget(mark)
    brand.addWidget(label('mochi', 'brand')); brand.addStretch(); sl.addLayout(brand)
    sl.addSpacing(34)
    w.library_nav = button('Library', lambda: w.navigate('library'), 'nav')
    w.favorites_nav = button('Favorites', lambda: w.navigate('favorites'), 'nav')
    w.audio_nav = button('Audio && voice', lambda: w.navigate('audio'), 'nav')
    w.capture_nav = button('Capture', lambda: w.navigate('capture'), 'nav')
    for b, icon in [(w.library_nav, 'library'), (w.favorites_nav, 'favorite'), (w.audio_nav, 'mic'), (w.capture_nav, 'capture')]:
        b.setIcon(QIcon(str(w.root / f'assets/{icon}.svg'))); b.setIconSize(QSize(18, 18))
        b.setCheckable(True); sl.addWidget(b)
    sl.addStretch()
    w.storage_label = label('Your clips stay local', 'muted'); sl.addWidget(w.storage_label)
    folder_button = button('Open folder', w.open_folder, 'subtle')
    folder_button.setIcon(QIcon(str(w.root / 'assets/folder.svg')))
    sl.addWidget(folder_button)
    shell.addWidget(side)
    main = QWidget(); main_layout = QVBoxLayout(main)
    main_layout.setContentsMargins(30, 26, 30, 22); main_layout.setSpacing(14)
    shell.addWidget(main, 1)
    capture, cl = panel(); capture.setObjectName('captureBar'); cl.setContentsMargins(16, 10, 16, 10); cl.setSpacing(8)
    bar = QHBoxLayout()
    state_col = QVBoxLayout(); state_col.setSpacing(4)
    w.status = label('●  Idle', 'status'); state_col.addWidget(w.status)
    bar.addLayout(state_col, 1)
    w.voice_summary = button('Mic off', lambda: w.navigate('audio'), 'micBadge')
    w.voice_summary.setIcon(QIcon(str(w.root / 'assets/mic.svg')))
    bar.addWidget(w.voice_summary)
    bar.addSpacing(10)
    w.save30 = button('Clip 30s', lambda: w.save_clip(30))
    w.save60 = button('Clip 60s', lambda: w.save_clip(60))
    w.start = button('Record', w.toggle_recording, 'primary')
    for b in [w.save30, w.save60, w.start]: bar.addWidget(b)
    cl.addLayout(bar)
    w.progress = QProgressBar(); w.progress.setRange(0, 60); w.progress.setValue(0)
    w.progress.setTextVisible(False); w.progress.setFixedHeight(4)
    w.progress.setToolTip('Estimated seconds buffered, up to 60.'); cl.addWidget(w.progress)
    main_layout.addWidget(capture)
    w.detail = Notice(); main_layout.addWidget(w.detail)
    w.pages = QStackedWidget(); main_layout.addWidget(w.pages, 1)

    library = QWidget(); ll = QVBoxLayout(library); ll.setContentsMargins(0, 22, 0, 0); ll.setSpacing(22)
    heading = QHBoxLayout()
    w.library_title = label('Library', 'heading'); heading.addWidget(w.library_title)
    heading.addStretch(); w.count_label = label('0 clips', 'muted'); heading.addWidget(w.count_label)
    ll.addLayout(heading)
    filters = QHBoxLayout()
    w.search = QLineEdit(); w.search.setPlaceholderText('Search clips'); w.search.textChanged.connect(w.filter_clips)
    filters.addWidget(w.search, 1)
    w.sort = QComboBox(); w.sort.addItems(['Newest first', 'Oldest first', 'Largest first'])
    w.sort.currentIndexChanged.connect(w.refresh_clips); filters.addWidget(w.sort)
    refresh = button('', w.refresh_clips)
    refresh.setIcon(QIcon(str(w.root / 'assets/refresh.svg'))); refresh.setFixedWidth(38)
    refresh.setToolTip('Refresh clips'); refresh.setAccessibleName('Refresh clips')
    filters.addWidget(refresh); ll.addLayout(filters)
    w.clips = ClipGrid(); w.clips.setViewMode(QListWidget.IconMode)
    w.clips.setResizeMode(QListWidget.Adjust); w.clips.setMovement(QListWidget.Static)
    w.clips.setSelectionMode(QAbstractItemView.SingleSelection); w.clips.setSpacing(5)
    w.clips.setUniformItemSizes(True); w.clips.setGridSize(QSize(280, 225))
    w.clips.setItemDelegate(ClipDelegate(w.clips)); w.clips.setMouseTracking(True)
    w.clips.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    w.clips.itemDoubleClicked.connect(w.open_clip)
    w.clips.setContextMenuPolicy(Qt.CustomContextMenu); w.clips.customContextMenuRequested.connect(w.clip_menu)
    ll.addWidget(w.clips, 1)
    w.empty = label('Nothing here yet.\nStart recording, then say “Mochi, clip that”.', 'empty')
    w.empty.setAlignment(Qt.AlignCenter); ll.addWidget(w.empty, 1)
    w.clips.setToolTip('Double-click to play · Right-click for options')
    w.pages.addWidget(library)

    audio_scroll = QScrollArea(); audio_scroll.setWidgetResizable(True); audio_scroll.setFrameShape(QFrame.NoFrame)
    audio_page = QWidget(); al = QVBoxLayout(audio_page); al.setContentsMargins(0, 10, 8, 0); al.setSpacing(16)
    al.addWidget(label('Audio & voice', 'heading'))
    mic, ml = panel(); ml.setContentsMargins(18, 16, 18, 16); ml.setSpacing(10)
    ml.addWidget(label('Microphone', 'section'))
    row = QHBoxLayout(); w.microphone = QComboBox(); w.microphone.setMinimumWidth(200)
    row.addWidget(w.microphone, 1); w.refresh_inputs = button('Refresh inputs', w.refresh_devices); row.addWidget(w.refresh_inputs)
    ml.addLayout(row)
    w.mic_info = label('Detecting audio inputs…', 'muted'); ml.addWidget(w.mic_info)
    w.meter = QProgressBar(); w.meter.setRange(0, 60); w.meter.setValue(0); w.meter.setFormat('Input level · off')
    w.meter.setFixedHeight(24); ml.addWidget(w.meter)
    test_row = QHBoxLayout()
    w.test_mic = button('Test microphone', w.toggle_mic_test)
    test_row.addWidget(w.test_mic); test_row.addStretch()
    test_row.addWidget(label('Voice boost', 'muted'))
    w.gain = QComboBox()
    for gain in [1, 2, 3, 4, 6, 8]: w.gain.addItem(f'{gain}×', gain)
    w.gain.setCurrentIndex(2); test_row.addWidget(w.gain); ml.addLayout(test_row)
    w.gain.setToolTip('Boost for voice recognition only. Clip volume uses your system settings.')
    al.addWidget(mic)
    voice, vl = panel(); vl.setContentsMargins(18, 16, 18, 16); vl.setSpacing(10)
    vr = QHBoxLayout(); vr.addWidget(label('Voice clipping', 'section')); vr.addStretch()
    w.voice_enabled = QCheckBox('Enabled'); w.voice_enabled.setChecked(True); vr.addWidget(w.voice_enabled)
    vl.addLayout(vr)
    phrase_row = QHBoxLayout(); w.phrase = QLineEdit('mochi clip that'); phrase_row.addWidget(w.phrase, 1)
    w.apply_phrase = button('Apply', w.apply_voice_phrase); phrase_row.addWidget(w.apply_phrase)
    vl.addLayout(phrase_row)
    dr = QHBoxLayout(); dr.addWidget(label('Clip length', 'muted'))
    w.duration = QComboBox(); w.duration.addItems(['30 seconds', '60 seconds']); w.duration.setCurrentIndex(1)
    dr.addWidget(w.duration); dr.addStretch(); vl.addLayout(dr)
    w.voice_status = label('Voice is off. Start the buffer or test your microphone.', 'muted'); vl.addWidget(w.voice_status)
    w.heard = label('Heard: —', 'heard'); w.heard.setTextFormat(Qt.PlainText); vl.addWidget(w.heard)
    w.heard.setToolTip('Processed locally. Recognition text is not saved.')
    al.addWidget(voice); al.addStretch(); audio_scroll.setWidget(audio_page); w.pages.addWidget(audio_scroll)

    capture_page = QWidget(); cp = QVBoxLayout(capture_page); cp.setContentsMargins(0, 10, 0, 0); cp.setSpacing(18)
    cp.addWidget(label('Capture', 'heading'))
    c, cs = panel(); cs.addWidget(label('Screen source', 'section'))
    w.source = QComboBox(); w.source.addItem('Choose a screen · Wayland portal', 'portal')
    w.source.addItem('Main display · direct capture', 'screen'); cs.addWidget(w.source)
    w.source.setToolTip('60 fps · H.264 · 60-second replay buffer. Stop recording to change inputs.')
    cp.addWidget(c)
    f, fs = panel(); fs.addWidget(label('Save location', 'section'))
    w.folder_label = label(str(w.folder), 'muted'); fs.addWidget(w.folder_label)
    fr = QHBoxLayout(); w.change_folder = button('Change folder', w.choose_folder); fr.addWidget(w.change_folder)
    fr.addWidget(button('Open folder ↗', w.open_folder)); fr.addStretch(); fs.addLayout(fr); cp.addWidget(f)
    h, hs = panel(); hs.addWidget(label('Keyboard shortcut', 'section'))
    hs.addWidget(label('Bind in Niri', 'muted'))
    command = QLineEdit(f'{w.root / "run.sh"} --clip default'); command.setReadOnly(True); hs.addWidget(command)
    cp.addWidget(h); cp.addStretch(); w.pages.addWidget(capture_page)
    w.setStyleSheet(STYLE.replace('assets/check.svg', str(w.root / 'assets' / 'check.svg')))


STYLE = '''
QWidget { background: #121316; color: #eeeef2; font-family: 'DejaVu Sans'; font-size: 12px; }
QFrame#sidebar { background: #0c0d10; border-right: 1px solid #292a30; }
QFrame#sidebar QLabel, QFrame#sidebar QPushButton { background: transparent; }
QLabel#brand { font-size: 27px; font-weight: 800; color: #f4f2ed; }
QLabel#eyebrow { font-size: 9px; font-weight: 700; color: #858894; letter-spacing: 1px; }
QLabel#heading { font-size: 29px; font-weight: 700; }
QLabel#section { font-size: 15px; font-weight: 700; }
QLabel#muted { color: #a3a5b1; font-size: 11px; background: transparent; }
QLabel#status { font-size: 12px; font-weight: 700; background: transparent; }
QLabel#empty { color: #9598a6; line-height: 2; font-size: 15px; }
QLabel#notice { background: #22252b; border-left: 2px solid #f6b84c; padding: 10px 14px; color: #c6c9d2; }
QFrame#captureBar { background: #1b1d21; border: 1px solid #2b2d33; border-radius: 9px; }
QPushButton#micBadge { background: transparent; border: none; font-size: 11px; color: #a8acb7; padding: 6px 8px; }
QPushButton#micBadge:hover { background: #30343a; }
QLabel#heard { background: #111216; border: 1px solid #363840; padding: 14px; border-radius: 5px; }
QFrame#panel { background: #1b1c21; border: 1px solid #2b2d34; border-radius: 7px; }
QFrame#panel QLabel, QFrame#panel QCheckBox { background: transparent; }
QPushButton, QComboBox, QLineEdit { background: #24262d; border: 1px solid #383b45; padding: 9px 12px; border-radius: 5px; }
QPushButton:hover, QComboBox:hover { background: #30333d; border-color: #606575; }
QPushButton:pressed { background: #3d414c; }
QPushButton:focus, QComboBox:focus, QLineEdit:focus { border: 1px solid #f6b84c; }
QPushButton:disabled, QComboBox:disabled { color: #757985; background: #1c1e24; border-color: #2f3239; }
QPushButton#primary { background: #f6b84c; color: #181209; border-color: #f6b84c; font-weight: 700; }
QPushButton#primary:hover { background: #ffcb70; }
QPushButton#primary:disabled { background: #68522f; color: #b7aa95; border-color: #68522f; }
QPushButton#nav { text-align: left; border: none; padding: 13px 14px; color: #a8aab7; }
QPushButton#nav:hover { background: #22242b; color: white; }
QPushButton#nav:checked { background: #26231c; color: #f6b84c; border-left: 2px solid #f6b84c; font-weight: 700; }
QPushButton#subtle { border: none; color: #b8bbc8; font-size: 11px; padding: 8px 2px; }
QPushButton#subtle:hover { color: #f6b84c; }
QListWidget { border: none; background: transparent; outline: 0; }
QProgressBar { border: none; background: #30333b; border-radius: 2px; text-align: center; font-size: 10px; }
QProgressBar::chunk { background: #719b38; border-radius: 2px; }
QScrollArea { background: transparent; }
QScrollBar:vertical { background: #191b20; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #414550; min-height: 30px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #777e8d; border-radius: 3px; background: #17191e; }
QCheckBox::indicator:checked { background: #f6b84c; border-color: #f6b84c; image: url(assets/check.svg); }
QMenu { background: #202229; border: 1px solid #414550; padding: 5px; }
QMenu::item { padding: 9px 18px; }
QMenu::item:selected { background: #3a3e48; }
'''
