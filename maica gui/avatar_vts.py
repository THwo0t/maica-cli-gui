# -*- coding: utf-8 -*-
"""VTube Studio avatar driver.

This driver is intentionally best-effort: if VTube Studio is closed, the plugin
is not authorized, or the active model has no matching parameters, MAICA keeps
running and the GUI falls back to the PNG avatar.
"""

from __future__ import annotations

import json
import math
import queue
import threading
import time
import uuid
from typing import Any, Callable

try:
    import websocket  # type: ignore
except Exception:  # pragma: no cover - optional dependency path
    websocket = None  # type: ignore


StatusCallback = Callable[[str], None]
TokenCallback = Callable[[str], None]


EMOTION_INDEX = {
    'neutral': 0.0,
    'smile': 1.0,
    'happy': 2.0,
    'gentle': 3.0,
    'shy': 4.0,
    'playful': 5.0,
    'thinking': 6.0,
    'concerned': 7.0,
    'sad': 8.0,
    'surprised': 9.0,
}

EMOTION_AXES = {
    'neutral': {'happy': 0.15, 'shy': 0.0, 'thinking': 0.0, 'concerned': 0.0, 'surprised': 0.0},
    'smile': {'happy': 0.45, 'shy': 0.0, 'thinking': 0.0, 'concerned': 0.0, 'surprised': 0.0},
    'happy': {'happy': 0.9, 'shy': 0.0, 'thinking': 0.0, 'concerned': 0.0, 'surprised': 0.0},
    'gentle': {'happy': 0.35, 'shy': 0.15, 'thinking': 0.0, 'concerned': 0.0, 'surprised': 0.0},
    'shy': {'happy': 0.35, 'shy': 0.85, 'thinking': 0.0, 'concerned': 0.0, 'surprised': 0.0},
    'playful': {'happy': 0.7, 'shy': 0.1, 'thinking': 0.0, 'concerned': 0.0, 'surprised': 0.0},
    'thinking': {'happy': 0.1, 'shy': 0.0, 'thinking': 0.85, 'concerned': 0.0, 'surprised': 0.0},
    'concerned': {'happy': 0.0, 'shy': 0.0, 'thinking': 0.25, 'concerned': 0.85, 'surprised': 0.0},
    'sad': {'happy': 0.0, 'shy': 0.0, 'thinking': 0.2, 'concerned': 0.6, 'surprised': 0.0},
    'surprised': {'happy': 0.2, 'shy': 0.0, 'thinking': 0.15, 'concerned': 0.0, 'surprised': 0.9},
}


class VTubeStudioDriver:
    def __init__(
        self,
        config: dict[str, Any],
        on_status: StatusCallback | None = None,
        on_token: TokenCallback | None = None,
    ) -> None:
        self.config = dict(config)
        self.on_status = on_status
        self.on_token = on_token
        self.url = str(config.get('vts_url') or 'ws://127.0.0.1:8001').strip()
        self.plugin_name = str(config.get('vts_plugin_name') or 'MAICA CLI GUI').strip()
        self.plugin_developer = str(config.get('vts_plugin_developer') or 'THwo0t').strip()
        self.auth_token = str(config.get('vts_auth_token') or '').strip()
        self.parameter_prefix = str(config.get('vts_parameter_prefix') or 'Maica').strip() or 'Maica'
        self._queue: queue.Queue[tuple[str, Any] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ws: Any = None
        self._status = 'stopped'
        self._emotion = 'neutral'
        self._speaking = False
        self._mouth_open = 0.0
        self._breath_phase = 0.0
        self._last_inject = 0.0

    def start(self) -> None:
        if websocket is None:
            self._set_status('websocket-client missing')
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='maica-vts-driver', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def set_emotion(self, emotion: str) -> None:
        self._emotion = str(emotion or 'neutral')
        self._put(('emotion', self._emotion))

    def play_action(self, action: dict[str, Any] | str | None) -> None:
        self._put(('action', action or {}))

    def set_speaking(self, speaking: bool) -> None:
        self._speaking = bool(speaking)
        self._put(('speaking', self._speaking))

    def set_mouth_open(self, value: float) -> None:
        self._mouth_open = max(0.0, min(1.0, float(value or 0.0)))
        self._put(('mouth', self._mouth_open))

    def refresh(self) -> None:
        self._put(('refresh', None))

    def tick(self) -> None:
        self._put(('tick', None))

    def status_text(self) -> str:
        return self._status

    def _put(self, item: tuple[str, Any]) -> None:
        if self._thread and self._thread.is_alive():
            self._queue.put(item)

    def _set_status(self, text: str) -> None:
        self._status = text
        if self.on_status:
            try:
                self.on_status(text)
            except Exception:
                pass

    def _run(self) -> None:
        self._set_status('connecting')
        try:
            self._connect()
            self._set_status('connected')
            self._main_loop()
        except Exception as exc:
            self._set_status(self._compact_error(exc))
        finally:
            try:
                if self._ws is not None:
                    self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _connect(self) -> None:
        assert websocket is not None
        self._ws = websocket.create_connection(self.url, timeout=3)
        self._authenticate()
        self._create_custom_parameters()
        self._inject_current_state(force=True)

    def _main_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.25)
            except queue.Empty:
                item = ('tick', None)
            if item is None:
                return
            kind, value = item
            if kind == 'emotion':
                self._emotion = str(value or 'neutral')
            elif kind == 'speaking':
                self._speaking = bool(value)
                if not self._speaking:
                    self._mouth_open = 0.0
            elif kind == 'mouth':
                self._mouth_open = max(0.0, min(1.0, float(value or 0.0)))
            elif kind == 'action':
                self._apply_action(value)
            self._inject_current_state(force=kind != 'tick')

    def _authenticate(self) -> None:
        token = self.auth_token
        if not token:
            response = self._request(
                'AuthenticationTokenRequest',
                {
                    'pluginName': self.plugin_name,
                    'pluginDeveloper': self.plugin_developer,
                },
            )
            token = str((response.get('data') or {}).get('authenticationToken') or '').strip()
            if not token:
                raise RuntimeError('VTube Studio did not return an auth token')
            self.auth_token = token
            if self.on_token:
                self.on_token(token)
        response = self._request(
            'AuthenticationRequest',
            {
                'pluginName': self.plugin_name,
                'pluginDeveloper': self.plugin_developer,
                'authenticationToken': token,
            },
        )
        data = response.get('data') or {}
        if data.get('authenticated') is False:
            raise RuntimeError(str(data.get('reason') or 'VTube Studio authorization denied'))

    def _create_custom_parameters(self) -> None:
        definitions = [
            (f'{self.parameter_prefix}Emotion', 0.0, 9.0, 0.0),
            (f'{self.parameter_prefix}Happy', 0.0, 1.0, 0.0),
            (f'{self.parameter_prefix}Shy', 0.0, 1.0, 0.0),
            (f'{self.parameter_prefix}Thinking', 0.0, 1.0, 0.0),
            (f'{self.parameter_prefix}Concerned', 0.0, 1.0, 0.0),
            (f'{self.parameter_prefix}Surprised', 0.0, 1.0, 0.0),
            (f'{self.parameter_prefix}MouthOpen', 0.0, 1.0, 0.0),
            (f'{self.parameter_prefix}Breath', 0.0, 1.0, 0.5),
        ]
        for name, minimum, maximum, default in definitions:
            try:
                self._request(
                    'ParameterCreationRequest',
                    {
                        'parameterName': name,
                        'explanation': 'MAICA avatar state bridge',
                        'min': minimum,
                        'max': maximum,
                        'defaultValue': default,
                    },
                )
            except Exception:
                # Already-existing or unsupported custom parameters should not
                # break the chat window.
                continue

    def _apply_action(self, action: Any) -> None:
        if not isinstance(action, dict):
            return
        expression = str(action.get('expression') or action.get('emotion') or '').strip().lower()
        gesture = str(action.get('gesture') or '').strip().lower()
        if expression:
            self._emotion = expression
        if gesture in {'surprise', 'jump'}:
            self._emotion = 'surprised'
        elif gesture in {'pout'}:
            self._emotion = 'concerned'
        elif gesture in {'wave', 'nod'}:
            self._emotion = 'happy'

    def _inject_current_state(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_inject < 0.75:
            return
        self._last_inject = now
        self._breath_phase += 0.18
        breath = 0.5 + 0.5 * math.sin(self._breath_phase)
        mouth = self._mouth_open
        if self._speaking and mouth <= 0.01:
            mouth = 0.18 + 0.55 * abs(math.sin(self._breath_phase * 2.7))
        axes = EMOTION_AXES.get(self._emotion, EMOTION_AXES['neutral'])
        values = [
            (f'{self.parameter_prefix}Emotion', EMOTION_INDEX.get(self._emotion, 0.0)),
            (f'{self.parameter_prefix}Happy', axes['happy']),
            (f'{self.parameter_prefix}Shy', axes['shy']),
            (f'{self.parameter_prefix}Thinking', axes['thinking']),
            (f'{self.parameter_prefix}Concerned', axes['concerned']),
            (f'{self.parameter_prefix}Surprised', axes['surprised']),
            (f'{self.parameter_prefix}MouthOpen', mouth),
            (f'{self.parameter_prefix}Breath', breath),
            ('ParamMouthOpenY', mouth),
            ('ParamAngleX', (axes['happy'] - axes['concerned']) * 8.0),
            ('ParamAngleY', (axes['surprised'] - axes['thinking']) * 4.0),
            ('ParamAngleZ', (axes['shy'] - axes['thinking']) * 5.0),
        ]
        self._request(
            'InjectParameterDataRequest',
            {
                'faceFound': False,
                'mode': 'set',
                'parameterValues': [
                    {'id': name, 'value': float(value), 'weight': 1.0}
                    for name, value in values
                ],
            },
            wait_response=False,
        )

    def _request(self, message_type: str, data: dict[str, Any] | None = None, wait_response: bool = True) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError('VTube Studio socket is not connected')
        request_id = uuid.uuid4().hex
        payload = {
            'apiName': 'VTubeStudioPublicAPI',
            'apiVersion': '1.0',
            'requestID': request_id,
            'messageType': message_type,
            'data': data or {},
        }
        self._ws.send(json.dumps(payload, ensure_ascii=False))
        if not wait_response:
            return {}
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            raw = self._ws.recv()
            response = json.loads(raw)
            if response.get('requestID') != request_id:
                continue
            if str(response.get('messageType') or '').endswith('Error'):
                detail = response.get('data') or {}
                raise RuntimeError(str(detail.get('message') or response.get('messageType')))
            return response
        raise TimeoutError(f'VTube Studio did not answer {message_type}')

    def _compact_error(self, exc: Exception) -> str:
        text = str(exc).strip() or exc.__class__.__name__
        lowered = text.lower()
        if 'connection refused' in lowered or 'timed out' in lowered or 'name or service' in lowered:
            return 'VTube Studio not connected'
        if 'authorization' in lowered or 'denied' in lowered or 'auth' in lowered:
            return 'VTube Studio authorization needed'
        if len(text) > 90:
            text = text[:87] + '...'
        return text
