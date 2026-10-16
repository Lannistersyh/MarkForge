"""Verify continuous control without injecting any input into the user's computer."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from markforge.session import Session


class FakeBackend:
    def __init__(self):
        self.captures = 0
        self.events = []
        self.focused = True
        self.width = 600
        self.text = ''
        self.fail_at = None
        self.drift_after = None

    def context(self):
        return {'focused': self.focused, 'width': self.width}

    def capture(self):
        self.captures += 1
        image = np.full((400, 600, 3), 255, dtype=np.uint8)
        image[50:150, 100:300] = (224, 105, 23)
        return image, self.context()

    def describe(self):
        return {'text': self.text}

    def validate(self, action):
        pass

    def execute(self, action, shape):
        if self.fail_at == len(self.events):
            raise RuntimeError('target disappeared')
        self.events.append(action)
        if action['type'] == 'type':
            self.text += action['text']
        if self.drift_after == len(self.events):
            self.focused = False

    def close(self):
        pass


def setup():
    backend = FakeBackend()
    session = Session(backend)
    frame = session.observe()
    return backend, session, frame['frame_id']


def test_four_clicks_use_one_post_batch_capture():
    backend, session, frame = setup()
    result = session.act(frame_id=frame, stable_layout=True,
                         actions=[{'type': 'click', 'x': 150, 'y': 100}]*4)
    assert result['status'] == 'OK'
    assert result['completed'] == 4
    assert backend.captures == 2  # first observation + one final observation
    assert len(backend.events) == 4
    assert result['observation']['frame_id'] != frame


def test_queries_share_cached_detection(monkeypatch):
    backend, session, frame = setup()
    calls = []
    original = session.engine.detect
    monkeypatch.setattr(session.engine, 'detect', lambda *a, **kw: (calls.append(1), original(*a, **kw))[1])
    result = session.act(frame_id=frame, stable_layout=True,
                         actions=[{'type': 'click', 'query': 'color:#1769e0'}]*4)
    assert result['status'] == 'OK'
    assert len(calls) == 1
    assert len(backend.events) == 4


@pytest.mark.parametrize('actions', [
    [{'type': 'click', 'x': 1, 'y': 1}, {'type': 'click', 'x': 900, 'y': 1}],
    [{'type': 'click', 'x': 1, 'y': 1}, {'type': 'unknown'}],
    [{'type': 'click', 'x': float('nan'), 'y': 1}],
    [{'type': 'click', 'x': 1, 'y': 1, 'label': 'also label'}],
    [{'type': 'scroll', 'dy': 100}, {'type': 'click', 'x': 1, 'y': 1}],
    [{'type': 'click', 'label': 'delete account'}],
])
def test_validate_entire_batch_before_input(actions):
    backend, session, frame = setup()
    result = session.act(frame_id=frame, actions=actions, stable_layout=True)
    assert result['status'] == 'REFUSED'
    assert not backend.events


def test_coordinate_batch_needs_explicit_stable_layout():
    backend, session, frame = setup()
    result = session.act(frame_id=frame, actions=[{'type': 'click', 'x': 150, 'y': 100}]*2)
    assert result['status'] == 'REFUSED'
    assert not backend.events


@pytest.mark.parametrize('change', ['focus', 'geometry', 'expired', 'wrong_frame'])
def test_stale_or_changed_surface_gets_no_input(change):
    backend, session, frame = setup()
    if change == 'focus':
        backend.focused = False
    elif change == 'geometry':
        backend.width = 700
    elif change == 'expired':
        session.frame['time'] -= 61
    else:
        frame = 'old-frame'
    result = session.act(frame_id=frame, actions=[{'type': 'type', 'text': 'x'}])
    assert result['status'] == 'REFUSED'
    assert not backend.events


def test_focus_drift_stops_without_replaying_completed_action():
    backend, session, frame = setup()
    backend.drift_after = 1
    result = session.act(frame_id=frame, actions=[{'type': 'keypress', 'keys': ['1']}]*4)
    assert result['status'] == 'PARTIAL'
    assert result['completed'] == 1
    assert len(backend.events) == 1
    assert session.frame is None


def test_input_failure_returns_partial_and_invalidates_frame():
    backend, session, frame = setup()
    backend.fail_at = 1
    result = session.act(frame_id=frame, actions=[{'type': 'type', 'text': 'a'}]*3)
    assert result['status'] == 'PARTIAL'
    assert len(backend.events) == result['completed'] == 1
    assert session.frame is None


def test_typing_preserves_unicode_and_verifies_result_without_logging_text():
    backend, session, frame = setup()
    result = session.act(frame_id=frame, actions=[{'type': 'type', 'text': 'Hello，中文😀'}],
                         expect_text='中文😀')
    assert result['status'] == 'OK'
    assert result['verified'] is True
    assert backend.events[0]['text'] == 'Hello，中文😀'
    assert 'text' not in result['actions'][0]


def test_expected_text_failure_is_not_success():
    backend, session, frame = setup()
    result = session.act(frame_id=frame, actions=[{'type': 'type', 'text': 'a'}],
                         expect_text='absent', timeout_ms=0)
    assert result['status'] == 'VERIFY_FAILED'
    assert backend.captures == 2


def test_dry_run_sends_nothing_and_keeps_frame():
    backend, session, frame = setup()
    result = session.act(frame_id=frame, actions=[{'type': 'type', 'text': 'a'}], dry_run=True)
    assert result['status'] == 'DRY_RUN'
    assert not backend.events
    assert backend.captures == 1
    assert session.frame['id'] == frame


def test_retina_coordinates_map_to_window_points_without_a_separate_move():
    from markforge.backends import MacBackend
    class Quartz:
        kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGHIDEventTap = 1, 2, 3
        events = []
        def CGEventCreateMouseEvent(self, source, event, point, button):
            return event, point
        def CGEventPost(self, tap, event):
            self.events.append(event)
    backend = MacBackend.__new__(MacBackend)
    backend.q, backend.source = Quartz(), None
    backend.context = lambda: {'bounds': {'X': 100, 'Y': 200, 'Width': 300, 'Height': 200}}
    backend.execute({'type': 'click', 'x': 300, 'y': 100}, (400, 600, 3))
    assert backend.q.events == [(1, (250, 250)), (2, (250, 250))]


def test_native_unicode_event_preserves_text_and_utf16_length():
    from markforge.backends import MacBackend
    class Quartz:
        kCGHIDEventTap = 3
        text_events = []
        def CGEventCreateKeyboardEvent(self, source, key, down):
            return down
        def CGEventKeyboardSetUnicodeString(self, event, length, text):
            self.text_events.append((length, text))
        def CGEventPost(self, tap, event):
            pass
    backend = MacBackend.__new__(MacBackend)
    backend.q, backend.source = Quartz(), None
    backend.execute({'type': 'type', 'text': 'A中😀'}, (400, 600, 3))
    assert backend.q.text_events == [(4, 'A中😀'), (4, 'A中😀')]


@pytest.fixture
def rpc_dir():
    import tempfile
    # Darwin limits AF_UNIX addresses to 104 bytes; pytest's default path is longer.
    with tempfile.TemporaryDirectory(prefix='mf-rpc-', dir='/tmp') as directory:
        yield Path(directory)


def test_unix_rpc_keeps_one_session_across_client_connections(rpc_dir, monkeypatch):
    import argparse
    import importlib.util
    import threading
    import time
    import markforge.backends
    spec = importlib.util.spec_from_file_location('mf_control', Path(__file__).resolve().parents[2] / 'scripts/control.py')
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    backend = FakeBackend()
    monkeypatch.setattr(markforge.backends, 'MacBackend', lambda *args, **kw: backend)
    monkeypatch.setattr(control, 'paths', lambda name: (rpc_dir / 'session.sock', rpc_dir / 'log'))
    args = argparse.Namespace(session='test', backend='mac', app='fake', window_id=None)
    worker = threading.Thread(target=control.serve, args=(args,), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic()+3
        while not (rpc_dir / 'session.sock').exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        observed = control.rpc('test', {'op': 'observe'})
        result = control.rpc('test', {'op': 'act', 'frame_id': observed['frame_id'],
                                     'actions': [{'type': 'type', 'text': 'Hello中'}], 'expect_text': 'Hello中'})
        status = control.rpc('test', {'op': 'status'})
        assert result['status'] == 'OK'
        assert status['observation_count'] == backend.captures == 2
        assert status['frame_id'] == result['observation']['frame_id']
    finally:
        if worker.is_alive():
            control.rpc('test', {'op': 'stop'})
            worker.join(timeout=3)
    assert not worker.is_alive()
    assert not (rpc_dir / 'session.sock').exists()
