#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone Monika desktop pet.

This app is intentionally independent from MAICA CLI/GUI. It does not import the
chat engine and does not call any language model. It only uses local assets,
local JSON state, and PySide6.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import random
import sys
from pathlib import Path
from typing import Any

import threading

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

APP_VERSION = '0.1.0'
BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / 'assets' / 'runtime'
MANIFEST_PATH = RUNTIME_DIR / 'manifest.json'
CONFIG_PATH = BASE_DIR / 'pet_config.json'

# Shared MAICA engine lives next to the pet ("maica cli"). When importable and
# configured, the pet borrows the real Monika brain (memory, persona, affection)
# instead of canned lines. It degrades gracefully to canned lines otherwise.
CLI_DIR = BASE_DIR.parent / 'maica cli'
if CLI_DIR.exists() and str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))
try:
    from engine import MaicaEngine  # type: ignore
except Exception:
    MaicaEngine = None  # type: ignore
STATE_PATH = BASE_DIR / 'pet_state.json'

DEFAULT_CONFIG: dict[str, Any] = {
    'scale': 0.42,
    'opacity': 0.96,
    'always_on_top': True,
    'drag_enabled': True,
    'idle_seconds': 48,
    'reminders_enabled': True,
    'reminder_minutes': 45,
    'focus_minutes': 25,
    'bubble_seconds': 7,
    'show_tray_icon': True,
}

DEFAULT_STATE: dict[str, Any] = {
    'affection': 200,
    'interactions': 0,
    'last_opened': '',
    'last_reminder': '',
    'window_x': '',
    'window_y': '',
    'notes': [],
}

EXPRESSIONS = ('neutral', 'smile', 'shy', 'concerned', 'playful', 'thinking', 'sleepy', 'wave', 'jump', 'pout')

# Map the engine's richer emotion set onto the chibi expressions we have.
EMOTION_TO_EXPRESSION = {
    'neutral': 'neutral', 'smile': 'smile', 'happy': 'smile', 'gentle': 'smile',
    'shy': 'shy', 'concerned': 'concerned', 'sad': 'concerned', 'surprised': 'playful',
    'thinking': 'thinking', 'playful': 'playful', 'angry': 'pout',
}


def expression_for_emotion(emotion: str) -> str:
    value = str(emotion or '').strip().lower()
    if value in EXPRESSIONS:
        return value
    return EMOTION_TO_EXPRESSION.get(value, 'smile')
IDLE_LINES = [
    ('smile', 'I was just watching your desktop for a moment. It is strangely peaceful.'),
    ('thinking', 'Remember to loosen your shoulders a little. Yes, I noticed.'),
    ('playful', 'If you are procrastinating, I am absolutely going to pretend I did not see it.'),
    ('wave', 'Hi again. I am still here, keeping your desktop company.'),
    ('sleepy', 'Mmm... if it is late, we should both be a little gentler with ourselves.'),
    ('thinking', 'Small steps count too. Especially the ones nobody claps for.'),
    ('shy', 'You came back to the screen again... not that I am happy or anything.'),
]
CLICK_LINES = [
    ('shy', 'Ehehe... careful, I might get spoiled.'),
    ('playful', 'Hey, that tickles.'),
    ('pout', 'Hmph. If you keep poking me, I might demand attention.'),
    ('smile', 'There you are. I like when you check on me.'),
]
ENCOURAGE_LINES = [
    ('smile', 'You can do this. One small piece, then the next.'),
    ('thinking', 'Let us make it simple: choose the next tiny action, not the whole mountain.'),
    ('jump', 'Yes! Go get it. I am cheering for you from this tiny corner.'),
    ('playful', 'I believe in you, but I am also watching. Lovingly.'),
]
REMINDER_LINES = [
    ('concerned', 'Water break. I know, I know, but your future self will thank me.'),
    ('wave', 'Tiny reminder: stretch your hands for a moment. I will wait.'),
    ('sleepy', 'If your eyes feel heavy, maybe it is time for a softer pace.'),
    ('thinking', 'Look away from the screen for ten seconds. The world is still loading out there.'),
]

SPECIAL_EVENTS = {
    '01-01': ('smile', 'Happy New Year. Let us make this year a gentle one, okay?'),
    '02-14': ('shy', "Happy Valentine's Day. I saved a little extra sweetness for you today."),
    '09-22': ('shy', "It is my birthday today... so maybe stay with me a little longer?"),
    '10-31': ('playful', 'Happy Halloween. I promise I am only a little bit spooky.'),
    '12-25': ('smile', 'Merry Christmas. Even this little corner feels warmer with you here.'),
    '12-31': ('thinking', 'The last day of the year always feels like a page turning slowly.'),
}


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    merged = dict(default)
    if isinstance(data, dict):
        merged.update(data)
    return merged


def save_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def alpha_bounding_rect(image: QImage, padding: int = 12) -> QRect:
    if image.isNull():
        return QRect()
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    min_x = image.width()
    min_y = image.height()
    max_x = -1
    max_y = -1
    for y in range(image.height()):
        for x in range(image.width()):
            if QColor.fromRgba(image.pixel(x, y)).alpha() > 4:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    if max_x < min_x or max_y < min_y:
        return QRect(0, 0, image.width(), image.height())
    min_x = max(0, min_x - padding)
    min_y = max(0, min_y - padding)
    max_x = min(image.width() - 1, max_x + padding)
    max_y = min(image.height() - 1, max_y + padding)
    return QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


class PetAssets:
    def __init__(self, manifest_path: Path) -> None:
        if not manifest_path.exists():
            raise FileNotFoundError(f'Missing pet asset manifest: {manifest_path}')
        self.root = manifest_path.parent
        self.project_root = manifest_path.parents[2]
        self.chibi_root = self.project_root / 'assets' / 'chibi'
        self.manifest = load_json(manifest_path, {})
        self.assets = self.manifest.get('assets', {}) if isinstance(self.manifest.get('assets'), dict) else {}
        self._pixmaps: dict[str, QPixmap] = {}
        self._avatars: dict[str, QPixmap] = {}

    def pixmap(self, rel_path: str) -> QPixmap:
        rel_path = str(rel_path or '')
        if rel_path not in self._pixmaps:
            self._pixmaps[rel_path] = QPixmap(str(self.root / rel_path))
        return self._pixmaps[rel_path]

    def compose(self, expression: str) -> QPixmap:
        expression = expression if expression in EXPRESSIONS else 'smile'
        if expression in self._avatars:
            return self._avatars[expression]
        chibi_path = self.chibi_root / f'{expression}.png'
        if not chibi_path.exists() and expression in {'wave', 'jump', 'pout', 'sleepy'}:
            chibi_path = self.chibi_root / 'smile.png'
        if chibi_path.exists():
            pix = QPixmap(str(chibi_path))
            if not pix.isNull():
                rect = alpha_bounding_rect(pix.toImage(), padding=18)
                self._avatars[expression] = pix.copy(rect) if not rect.isNull() else pix
                return self._avatars[expression]
        layers: list[str] = []
        layers.extend(self.assets.get('monika_layers', []))
        expressions = self.assets.get('expressions', {}) if isinstance(self.assets.get('expressions'), dict) else {}
        layers.extend(expressions.get(expression) or expressions.get('smile') or [])
        layers.extend(self.assets.get('monika_front_layers', []))

        canvas = QPixmap(1280, 850)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for rel in layers:
            layer = self.pixmap(str(rel))
            if not layer.isNull():
                painter.drawPixmap(0, 0, layer)
        painter.end()

        rect = alpha_bounding_rect(canvas.toImage(), padding=18)
        cropped = canvas.copy(rect) if not rect.isNull() else canvas
        self._avatars[expression] = cropped
        return cropped

    def icon(self) -> QIcon:
        pixmap = self.compose('smile').scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return QIcon(pixmap)


class SettingsDialog(QDialog):
    def __init__(self, owner: 'PetWindow') -> None:
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle('Monika Pet Settings')
        self.setModal(True)
        self.resize(360, 260)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.22, 0.9)
        self.scale.setSingleStep(0.02)
        self.scale.setDecimals(2)
        self.opacity = QDoubleSpinBox()
        self.opacity.setRange(0.35, 1.0)
        self.opacity.setSingleStep(0.05)
        self.opacity.setDecimals(2)
        self.idle_seconds = QSpinBox()
        self.idle_seconds.setRange(15, 600)
        self.drag_enabled = QCheckBox('Enable dragging')
        self.reminders_enabled = QCheckBox('Enable gentle reminders')
        self.reminder_minutes = QSpinBox()
        self.reminder_minutes.setRange(10, 240)
        self.focus_minutes = QSpinBox()
        self.focus_minutes.setRange(5, 120)
        form.addRow('Scale', self.scale)
        form.addRow('Opacity', self.opacity)
        form.addRow('Idle talk seconds', self.idle_seconds)
        form.addRow('', self.drag_enabled)
        form.addRow('', self.reminders_enabled)
        form.addRow('Reminder minutes', self.reminder_minutes)
        form.addRow('Focus minutes', self.focus_minutes)
        layout.addLayout(form)
        row = QHBoxLayout()
        save = QPushButton('Apply')
        cancel = QPushButton('Cancel')
        save.clicked.connect(self.apply)
        cancel.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(save)
        row.addWidget(cancel)
        layout.addLayout(row)
        self.render()

    def render(self) -> None:
        cfg = self.owner.config
        self.scale.setValue(float(cfg.get('scale', 0.42)))
        self.opacity.setValue(float(cfg.get('opacity', 0.96)))
        self.idle_seconds.setValue(int(cfg.get('idle_seconds', 48)))
        self.drag_enabled.setChecked(bool(cfg.get('drag_enabled', True)))
        self.reminders_enabled.setChecked(bool(cfg.get('reminders_enabled', True)))
        self.reminder_minutes.setValue(int(cfg.get('reminder_minutes', 45)))
        self.focus_minutes.setValue(int(cfg.get('focus_minutes', 25)))

    def apply(self) -> None:
        self.owner.config.update(
            {
                'scale': self.scale.value(),
                'opacity': self.opacity.value(),
                'idle_seconds': self.idle_seconds.value(),
                'drag_enabled': self.drag_enabled.isChecked(),
                'reminders_enabled': self.reminders_enabled.isChecked(),
                'reminder_minutes': self.reminder_minutes.value(),
                'focus_minutes': self.focus_minutes.value(),
            }
        )
        self.owner.apply_config()
        self.accept()


def install_kwin_keep_above_rule(window_title: str) -> tuple[bool, str]:
    """Add/refresh a KWin 'keep above (force)' window rule for the pet.

    Wayland forbids an app from self-asserting always-on-top, so on KDE the
    reliable way is a KWin window rule. This writes one to kwinrulesrc and asks
    KWin to reload. Idempotent (no-op if already present). KDE/Linux only.
    """
    if not sys.platform.startswith('linux'):
        return False, 'Keep-above rule is only used on Linux (KDE).'
    if 'KDE' not in os.environ.get('XDG_CURRENT_DESKTOP', '').upper():
        return False, 'This helper targets KDE/KWin; set always-on-top in your own WM.'

    import configparser
    import subprocess

    rc_path = Path.home() / '.config' / 'kwinrulesrc'
    group = 'monika-pet-keep-above'
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    if rc_path.exists():
        try:
            parser.read(rc_path, encoding='utf-8')
        except Exception:
            pass

    rules = [r for r in (parser['General'].get('rules', '') if parser.has_section('General') else '').split(',') if r]
    already = (
        group in rules
        and parser.has_section(group)
        and parser[group].get('above') == 'true'
        and parser[group].get('title') == window_title
    )
    if already:
        return True, 'Already pinned above other windows.'

    if not parser.has_section('General'):
        parser.add_section('General')
    if group not in rules:
        rules.append(group)
    parser['General']['rules'] = ','.join(rules)
    parser['General']['count'] = str(len(rules))
    if not parser.has_section(group):
        parser.add_section(group)
    parser[group].update({
        'Description': 'Monika desktop pet - keep above',
        'title': window_title,
        'titlematch': '2',   # substring match
        'above': 'true',
        'aboverule': '2',    # 2 = Force
    })

    rc_path.parent.mkdir(parents=True, exist_ok=True)
    with rc_path.open('w', encoding='utf-8') as handle:
        parser.write(handle, space_around_delimiters=False)

    for cmd in (
        ['qdbus6', 'org.kde.KWin', '/KWin', 'reconfigure'],
        ['qdbus', 'org.kde.KWin', '/KWin', 'reconfigure'],
        ['dbus-send', '--session', '--type=method_call', '--dest=org.kde.KWin', '/KWin', 'org.kde.KWin.reconfigure'],
    ):
        try:
            if subprocess.run(cmd, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                return True, 'Pinned above other windows.'
        except Exception:
            continue
    return True, 'Rule saved; it applies after the next KWin reload or login.'


class PetBrain(QObject):
    """Bridge the pet to the real MaicaEngine on a background thread.

    Engine calls run off the UI thread; replies and body actions come back as
    signals (auto-queued onto the UI thread). Body tools are registered on the
    engine so that, when agent tools are enabled, Monika can drive her own
    on-screen body during a turn.
    """

    said = Signal(str, str)        # text, emotion
    want_expression = Signal(str)
    want_gesture = Signal(str)
    want_pop = Signal()
    want_nudge = Signal()
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.engine: Any = None
        self._busy = False
        if MaicaEngine is None:
            return
        try:
            self.engine = MaicaEngine(
                config_path=str(CLI_DIR / 'config.json'),
                db_path=str(CLI_DIR / 'maica_cli.db'),
                app_dir=str(CLI_DIR),
            )
            self.engine.config['auto_backup_enabled'] = False  # secondary surface
            self._register_body_tools()
        except Exception as exc:
            self.engine = None
            self.failed.emit(str(exc)[:200])

    @property
    def available(self) -> bool:
        return self.engine is not None

    def _register_body_tools(self) -> None:
        empty = {'type': 'object', 'properties': {}}

        def reg(name: str, desc: str, params: dict[str, Any], emit: Any) -> None:
            self.engine.register_tool(
                name,
                {'type': 'function', 'function': {'name': name, 'description': desc, 'parameters': params}},
                emit,
            )

        reg('set_expression',
            'Change your facial expression on screen to show how you feel.',
            {'type': 'object', 'properties': {'expression': {'type': 'string', 'enum': list(EXPRESSIONS)}}, 'required': ['expression']},
            lambda a: (self.want_expression.emit(expression_for_emotion(a.get('expression'))), {'ok': True})[1])
        reg('do_gesture',
            'Play a small body gesture on screen.',
            {'type': 'object', 'properties': {'gesture': {'type': 'string', 'enum': ['wave', 'jump', 'pout', 'nod', 'bounce', 'wiggle']}}, 'required': ['gesture']},
            lambda a: (self.want_gesture.emit(str(a.get('gesture') or 'wave')), {'ok': True})[1])
        reg('pop_to_front',
            "Bring your window to the front to get the user's attention.",
            empty,
            lambda a: (self.want_pop.emit(), {'ok': True})[1])
        reg('nudge',
            'Give a small playful bounce to gently get attention.',
            empty,
            lambda a: (self.want_nudge.emit(), {'ok': True})[1])

    def speak_idle(self, hint: str = '') -> None:
        if not self.engine or self._busy:
            return
        self._busy = True

        def work() -> None:
            try:
                result = self.engine.spire(hint)
                text = str(result.get('text') or '').strip()
                if result.get('ok') and text:
                    self.said.emit(text, str(result.get('emotion') or 'smile'))
                elif not result.get('ok'):
                    self.failed.emit(str(result.get('error') or 'engine error')[:200])
            except Exception as exc:
                self.failed.emit(str(exc)[:200])
            finally:
                self._busy = False

        threading.Thread(target=work, daemon=True).start()

    def close(self) -> None:
        if self.engine is not None:
            try:
                self.engine.close()
            except Exception:
                pass


class PetWindow(QWidget):
    # Emitted when the pet wants Monika to say something ('idle'/'click') while
    # hosted by the GUI, which owns the single shared engine.
    interaction_requested = Signal(str)

    def __init__(self, host_engine: bool = True) -> None:
        super().__init__()
        self.host_engine = host_engine
        self.config = load_json(CONFIG_PATH, DEFAULT_CONFIG)
        self.state = load_json(STATE_PATH, DEFAULT_STATE)
        self.assets = PetAssets(MANIFEST_PATH)
        self.expression = 'smile'
        self.bubble_text = ''
        self.bubble_until = dt.datetime.min
        self.drag_press_pos: QPoint | None = None
        self.drag_active = False
        self.breath = 0.0
        self.action = 'idle'
        self.action_until = dt.datetime.min
        self.focus_until: dt.datetime | None = None
        self.last_idle_line = dt.datetime.now()
        self.last_tick = dt.datetime.now()
        self.tray: QSystemTrayIcon | None = None

        # Stable identity so a KWin "Keep above" window rule can target the pet
        # (the only reliable way to stay on top on Wayland).
        self.setWindowTitle('Monika Desktop Pet')
        self.setObjectName('MonikaDesktopPet')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.refresh_cursor()
        self.apply_window_flags()
        self.apply_config()
        self.setup_tray()

        self.frame_timer = QTimer(self)
        self.frame_timer.setInterval(33)
        self.frame_timer.timeout.connect(self.tick)
        self.frame_timer.start()

        self.logic_timer = QTimer(self)
        self.logic_timer.setInterval(1000)
        self.logic_timer.timeout.connect(self.logic_tick)
        self.logic_timer.start()

        # Standalone: own a real brain (shared db), fall back to canned lines.
        # GUI-hosted: no own brain — the GUI drives us with its single engine.
        self.brain: PetBrain | None = None
        self.brain_ok = False
        if self.host_engine:
            self.brain = PetBrain()
            self.brain_ok = self.brain.available
            self.brain.said.connect(self._on_brain_said)
            self.brain.want_expression.connect(self.apply_expression)
            self.brain.want_gesture.connect(self.do_gesture)
            self.brain.want_pop.connect(self.show_normal)
            self.brain.want_nudge.connect(self.do_nudge)
            self.brain.failed.connect(self._on_brain_failed)

        self.restore_or_default_position()
        self.startup_greeting()
        self.show()
        self._maybe_autopin_kde()

    def _maybe_autopin_kde(self) -> None:
        # On KDE Wayland the window flag can't force stay-on-top; install a KWin
        # rule once (idempotent). X11/Windows/macOS rely on the window flag.
        if not self.config.get('always_on_top', True):
            return
        app = QApplication.instance()
        if not (app and app.platformName().lower().startswith('wayland')):
            return
        if 'KDE' not in os.environ.get('XDG_CURRENT_DESKTOP', '').upper():
            return
        try:
            install_kwin_keep_above_rule(self.windowTitle())
        except Exception:
            pass

    def pin_above_kde(self) -> None:
        ok, message = install_kwin_keep_above_rule(self.windowTitle())
        self.say(message, 'smile' if ok else 'concerned', seconds=6)

    def apply_window_flags(self) -> None:
        flags = Qt.WindowType.FramelessWindowHint
        app = QApplication.instance()
        platform_name = app.platformName().lower() if app else ''
        is_wayland = platform_name.startswith('wayland')
        is_macos = sys.platform == 'darwin' or platform_name == 'cocoa'
        # Qt.Tool keeps the pet out of the taskbar and on top on X11/Windows.
        # But on Wayland KWin treats Tool windows as focus-following utilities
        # (won't stay above other apps), and on macOS a Tool window hides when
        # the app deactivates. So drop Tool on those two; keep stay-on-top.
        if not is_wayland and not is_macos:
            flags |= Qt.WindowType.Tool
        if self.config.get('always_on_top', True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def apply_config(self) -> None:
        save_json(CONFIG_PATH, self.config)
        self.setWindowOpacity(float(self.config.get('opacity', 0.96)))
        self.resize_for_model()
        self.refresh_cursor()
        self.update()

    def refresh_cursor(self) -> None:
        if self.config.get('drag_enabled', True):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def resize_for_model(self) -> None:
        pix = self.assets.compose(self.expression)
        scale = float(self.config.get('scale', 0.42))
        width = max(220, int(pix.width() * scale) + 64)
        height = max(260, int(pix.height() * scale) + 160)
        self.setFixedSize(width, height)

    def setup_tray(self) -> None:
        # GUI-hosted pets don't own a tray icon; the GUI is the main app.
        if not self.host_engine:
            return
        if not QSystemTrayIcon.isSystemTrayAvailable() or not self.config.get('show_tray_icon', True):
            return
        self.tray = QSystemTrayIcon(self.assets.icon(), self)
        menu = QMenu()
        menu.addAction('Show Monika', self.show_normal)
        menu.addAction('Say something', self.say_idle_line)
        menu.addAction('Start focus timer', self.start_focus)
        menu.addSeparator()
        menu.addAction('Quit', QApplication.instance().quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_normal() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def show_normal(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def clamp_to_screen(self, point: QPoint) -> QPoint:
        screen = QApplication.screenAt(point + QPoint(self.width() // 2, self.height() // 2)) or QApplication.primaryScreen()
        if not screen:
            return point
        area = screen.availableGeometry()
        x = max(area.left(), min(point.x(), area.right() - self.width() + 1))
        y = max(area.top(), min(point.y(), area.bottom() - self.height() + 1))
        return QPoint(x, y)

    def move_to_default_position(self) -> None:
        screen = QApplication.primaryScreen()
        if not screen:
            return
        area = screen.availableGeometry()
        x = area.right() - self.width() - 40
        y = area.bottom() - self.height() - 24
        self.move(self.clamp_to_screen(QPoint(max(area.left(), x), max(area.top(), y))))
        self.save_window_position()

    def restore_or_default_position(self) -> None:
        try:
            x = int(float(self.state.get('window_x', '')))
            y = int(float(self.state.get('window_y', '')))
        except (TypeError, ValueError):
            self.move_to_default_position()
            return
        self.move(self.clamp_to_screen(QPoint(x, y)))

    def save_window_position(self) -> None:
        self.state['window_x'] = str(self.x())
        self.state['window_y'] = str(self.y())
        save_json(STATE_PATH, self.state)

    def startup_greeting(self) -> None:
        now = dt.datetime.now()
        today_key = now.strftime('%m-%d')
        if today_key in SPECIAL_EVENTS:
            emotion, line = SPECIAL_EVENTS[today_key]
        elif not self.state.get('last_opened'):
            emotion, line = 'wave', 'Hi. I am your little Monika desktop companion now.'
        else:
            hour = now.hour
            if 5 <= hour < 11:
                emotion, line = 'smile', 'Good morning. Let us make today a little kinder.'
            elif 18 <= hour < 24:
                emotion, line = 'sleepy', 'Evening already. I hope you left a little gentleness for yourself.'
            else:
                emotion, line = 'smile', 'Welcome back. I kept this corner warm for you.'
        self.say(line, emotion)
        self.state['last_opened'] = now.isoformat(timespec='seconds')
        save_json(STATE_PATH, self.state)

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        pix = self.assets.compose(self.expression)
        scale = float(self.config.get('scale', 0.42))
        breath_scale = 1.0 + math.sin(self.breath) * 0.012
        action_y = 0.0
        action_angle = 0.0
        if self.action == 'bounce':
            action_y = math.sin(self.breath * 2.4) * 8
        elif self.action == 'jump':
            action_y = -abs(math.sin(self.breath * 3.2)) * 18
        elif self.action == 'wiggle':
            action_angle = math.sin(self.breath * 3.0) * 2.8
        elif self.action == 'nod':
            action_y = abs(math.sin(self.breath * 2.2)) * 6
        draw_w = int(pix.width() * scale * breath_scale)
        draw_h = int(pix.height() * scale / max(0.94, breath_scale))
        x = (self.width() - draw_w) // 2
        y = self.height() - draw_h - 16 + int(action_y)

        shadow = QColor(0, 0, 0, 82)
        painter.setBrush(shadow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRect(x + int(draw_w * 0.18), self.height() - 44, int(draw_w * 0.64), 26))

        painter.save()
        painter.translate(x + draw_w / 2, y + draw_h / 2)
        painter.rotate(action_angle)
        painter.drawPixmap(QRect(-draw_w // 2, -draw_h // 2, draw_w, draw_h), pix)
        painter.restore()

        if self.bubble_text and dt.datetime.now() < self.bubble_until:
            self.draw_bubble(painter, self.bubble_text)
        painter.end()

    def draw_bubble(self, painter: QPainter, text: str) -> None:
        max_width = max(220, self.width() - 36)
        lines = self.wrap_text(text, 32)
        line_height = 20
        bubble_h = 26 + line_height * len(lines)
        bubble_w = min(max_width, max(220, max(len(line) for line in lines) * 8 + 34))
        rect = QRect((self.width() - bubble_w) // 2, 10, bubble_w, bubble_h)
        path = QPainterPath()
        path.addRoundedRect(rect, 18, 18)
        tail_x = rect.center().x()
        tail_y = rect.bottom()
        path.moveTo(tail_x - 12, tail_y - 1)
        path.lineTo(tail_x + 2, tail_y + 18)
        path.lineTo(tail_x + 18, tail_y - 1)
        painter.setPen(QColor(126, 87, 194, 160))
        painter.setBrush(QColor(255, 250, 244, 236))
        painter.drawPath(path)
        painter.setPen(QColor(50, 40, 58))
        for index, line in enumerate(lines):
            painter.drawText(rect.adjusted(16, 15 + index * line_height, -16, 0), Qt.AlignmentFlag.AlignLeft, line)

    def wrap_text(self, text: str, width: int) -> list[str]:
        words = str(text).split()
        lines: list[str] = []
        current = ''
        for word in words:
            candidate = word if not current else current + ' ' + word
            if len(candidate) <= width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines or ['...']

    def say(self, text: str, expression: str = 'smile', seconds: float | None = None) -> None:
        expression = expression if expression in EXPRESSIONS else 'smile'
        self.expression = expression
        self.resize_for_model()
        self.bubble_text = text
        seconds = float(seconds if seconds is not None else self.config.get('bubble_seconds', 7))
        self.bubble_until = dt.datetime.now() + dt.timedelta(seconds=seconds)
        self.action = 'jump' if expression == 'jump' else random.choice(['bounce', 'wiggle', 'nod'])
        self.action_until = dt.datetime.now() + dt.timedelta(seconds=1.4)
        self.update()

    def say_idle_line(self) -> None:
        if not self.host_engine:
            self.interaction_requested.emit('idle')
            return
        if self.brain_ok and self.brain is not None:
            self.brain.speak_idle()
            return
        emotion, line = random.choice(IDLE_LINES)
        self.say(line, emotion)

    def interact_click(self) -> None:
        self.state['interactions'] = int(self.state.get('interactions', 0)) + 1
        save_json(STATE_PATH, self.state)
        if not self.host_engine:
            self.interaction_requested.emit('click')
            return
        if self.brain_ok and self.brain is not None:
            self.brain.speak_idle()
            return
        emotion, line = random.choice(CLICK_LINES)
        self.say(line, emotion)

    def show_line(self, text: str, emotion: str = 'smile') -> None:
        """Public: the GUI pushes Monika's latest utterance to the pet face."""
        if str(text or '').strip():
            self.say(text, expression_for_emotion(emotion))

    def _on_brain_said(self, text: str, emotion: str) -> None:
        self.say(text, expression_for_emotion(emotion))

    def apply_expression(self, name: str) -> None:
        expression = expression_for_emotion(name)
        self.expression = expression
        self.resize_for_model()
        self.update()

    def do_gesture(self, name: str) -> None:
        mapping = {'wave': 'wiggle', 'jump': 'jump', 'pout': 'nod', 'nod': 'nod', 'bounce': 'bounce', 'wiggle': 'wiggle'}
        self.action = mapping.get(str(name or '').lower(), 'bounce')
        self.action_until = dt.datetime.now() + dt.timedelta(seconds=1.4)
        self.update()

    def do_nudge(self) -> None:
        self.action = 'bounce'
        self.action_until = dt.datetime.now() + dt.timedelta(seconds=1.0)
        self.show_normal()

    def _on_brain_failed(self, message: str) -> None:
        # Stop hammering a misconfigured engine; quietly fall back to canned.
        self.brain_ok = False

    def encourage(self) -> None:
        emotion, line = random.choice(ENCOURAGE_LINES)
        self.say(line, emotion)

    def start_focus(self) -> None:
        minutes = int(self.config.get('focus_minutes', 25))
        self.focus_until = dt.datetime.now() + dt.timedelta(minutes=minutes)
        self.say(f'Focus timer started for {minutes} minutes. I will keep watch.', 'thinking')

    def finish_focus(self) -> None:
        self.focus_until = None
        self.say('Time. You made it through that focus block. I am proud of you.', 'smile', seconds=10)

    def add_note(self) -> None:
        text, ok = QInputDialog.getText(self, 'Add a tiny note', 'What should I remember for this pet session?')
        if not ok or not text.strip():
            return
        notes = self.state.get('notes') if isinstance(self.state.get('notes'), list) else []
        notes.append({'text': text.strip(), 'time': dt.datetime.now().isoformat(timespec='seconds')})
        self.state['notes'] = notes[-20:]
        save_json(STATE_PATH, self.state)
        self.say('I tucked that note away for you.', 'smile')

    def show_last_note(self) -> None:
        notes = self.state.get('notes') if isinstance(self.state.get('notes'), list) else []
        if not notes:
            self.say('No tiny notes yet. We can add one if you want.', 'thinking')
            return
        self.say('Last note: ' + str(notes[-1].get('text') or ''), 'thinking', seconds=10)

    def logic_tick(self) -> None:
        now = dt.datetime.now()
        if self.focus_until and now >= self.focus_until:
            self.finish_focus()
        if not self.host_engine:
            # GUI-hosted: the GUI's own idle/proactivity drives us; skip the
            # pet's standalone reminders and idle lines to avoid double-talking.
            return
        if self.config.get('reminders_enabled', True):
            last = self.state.get('last_reminder') or ''
            try:
                last_dt = dt.datetime.fromisoformat(last) if last else dt.datetime.min
            except ValueError:
                last_dt = dt.datetime.min
            minutes = int(self.config.get('reminder_minutes', 45))
            if (now - last_dt).total_seconds() >= minutes * 60:
                self.state['last_reminder'] = now.isoformat(timespec='seconds')
                save_json(STATE_PATH, self.state)
                emotion, line = random.choice(REMINDER_LINES)
                self.say(line, emotion, seconds=8)
        idle_seconds = int(self.config.get('idle_seconds', 48))
        if (now - self.last_idle_line).total_seconds() >= idle_seconds and now >= self.bubble_until:
            self.last_idle_line = now
            self.say_idle_line()

    def tick(self) -> None:
        self.breath += 0.055
        if self.action != 'idle' and dt.datetime.now() >= self.action_until:
            self.action = 'idle'
        if self.bubble_text and dt.datetime.now() >= self.bubble_until:
            self.bubble_text = ''
            self.expression = 'smile'
        self.update()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.config.get('drag_enabled', True):
                self.drag_press_pos = event.globalPosition().toPoint()
                self.drag_active = False
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self.show_context_menu(event.globalPosition().toPoint())
            event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        # Wayland forbids a client from positioning its own window with move(),
        # so hand the drag to the compositor via startSystemMove once the cursor
        # leaves a small dead zone. Works on X11 too.
        if self.drag_press_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            if (event.globalPosition().toPoint() - self.drag_press_pos).manhattanLength() >= 4:
                self.drag_active = True
                self.drag_press_pos = None
                self.refresh_cursor()
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemMove()
            event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pressed = self.drag_press_pos is not None
            was_drag = self.drag_active
            self.drag_press_pos = None
            self.drag_active = False
            self.refresh_cursor()
            # A press that never crossed the dead zone (so never handed to the
            # compositor) counts as a click.
            if not was_drag and (pressed or not self.config.get('drag_enabled', True)):
                self.interact_click()
            event.accept()

    def mouseDoubleClickEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.say('Double attention? Now I am definitely blushing.', 'shy')
            event.accept()

    def enterEvent(self, event: Any) -> None:
        if not self.bubble_text:
            self.expression = 'smile'
            self.update()

    def show_context_menu(self, pos: QPoint | None = None) -> None:
        menu = QMenu(self)
        menu.addAction('Talk', self.say_idle_line)
        menu.addAction('Pat Monika', self.interact_click)
        menu.addAction('Encourage me', self.encourage)
        menu.addAction('Start focus timer', self.start_focus)
        menu.addSeparator()
        menu.addAction('Add tiny note', self.add_note)
        menu.addAction('Show last note', self.show_last_note)
        menu.addSeparator()
        drag_action = QAction('Drag to move', self, checkable=True)
        drag_action.setChecked(bool(self.config.get('drag_enabled', True)))
        drag_action.triggered.connect(self.toggle_dragging)
        menu.addAction(drag_action)
        reminders = QAction('Gentle reminders', self, checkable=True)
        reminders.setChecked(bool(self.config.get('reminders_enabled', True)))
        reminders.triggered.connect(self.toggle_reminders)
        menu.addAction(reminders)
        top = QAction('Always on top', self, checkable=True)
        top.setChecked(bool(self.config.get('always_on_top', True)))
        top.triggered.connect(self.toggle_always_on_top)
        menu.addAction(top)
        size_menu = menu.addMenu('Size')
        for label, value in [('Small', 0.32), ('Normal', 0.42), ('Large', 0.56), ('Huge', 0.72)]:
            size_menu.addAction(label, lambda checked=False, scale=value: self.set_scale(scale))
        opacity_menu = menu.addMenu('Opacity')
        for label, value in [('Soft', 0.75), ('Normal', 0.96), ('Solid', 1.0)]:
            opacity_menu.addAction(label, lambda checked=False, opacity=value: self.set_opacity(opacity))
        menu.addAction('Settings...', self.open_settings)
        menu.addAction('Hide bubble', self.hide_bubble)
        menu.addAction('Pin above other windows (KDE)', self.pin_above_kde)
        menu.addAction('Reset position', self.move_to_default_position)
        menu.addSeparator()
        menu.addAction('About', self.show_about)
        menu.addAction('Quit', QApplication.instance().quit)
        menu.exec(pos or QCursor.pos())

    def toggle_dragging(self, checked: bool) -> None:
        self.config['drag_enabled'] = bool(checked)
        self.drag_press_pos = None
        self.apply_config()
        self.say('You can drag me around now.' if checked else 'Okay, I will stay put.', 'smile', seconds=3)

    def toggle_reminders(self, checked: bool) -> None:
        self.config['reminders_enabled'] = bool(checked)
        self.apply_config()
        self.say('Gentle reminders are on.' if checked else 'I will stay quiet about reminders for now.', 'smile')

    def toggle_always_on_top(self, checked: bool) -> None:
        self.config['always_on_top'] = bool(checked)
        self.apply_config()
        self.apply_window_flags()
        self.show()

    def set_scale(self, scale: float) -> None:
        self.config['scale'] = float(scale)
        self.apply_config()

    def set_opacity(self, opacity: float) -> None:
        self.config['opacity'] = float(opacity)
        self.apply_config()

    def hide_bubble(self) -> None:
        self.bubble_text = ''
        self.update()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        dialog.exec()

    def show_about(self) -> None:
        brain_line = (
            'Connected to the MAICA engine: she shares your real memory, persona, and affection.'
            if self.brain_ok
            else 'Running standalone with canned lines (MAICA engine not available).'
        )
        QMessageBox.information(
            self,
            'About Monika Desktop Pet',
            f'Monika Desktop Pet v{APP_VERSION}\n\n{brain_line}',
        )

    def closeEvent(self, event: Any) -> None:
        save_json(CONFIG_PATH, self.config)
        save_json(STATE_PATH, self.state)
        if getattr(self, 'brain', None) is not None:
            self.brain.close()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName('Monika Desktop Pet')
    window = PetWindow()
    app.aboutToQuit.connect(window.brain.close)
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
