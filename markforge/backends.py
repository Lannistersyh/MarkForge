"""Stateful browser and macOS adapters. Neither launches a model."""
from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np


def decode(png):
    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError('screenshot decoder returned no image')
    return image


class BrowserBackend:
    """An owned page stays alive until stop; DOM and visual actions share it."""
    def __init__(self, url, *, headless=False, channel=None):
        from urllib.parse import urlparse
        if urlparse(url).scheme not in ('http', 'https'):
            raise ValueError('browser URL must use http or https')
        if channel not in (None, 'chrome', 'msedge'):
            raise ValueError('browser channel must be chrome or msedge')
        from playwright.sync_api import sync_playwright
        self.runtime = sync_playwright().start()
        self.browser = None
        try:
            options = {'headless': headless}
            if channel:
                options['channel'] = channel
            self.browser = self.runtime.chromium.launch(**options)
            self.browser_context = self.browser.new_context(viewport={'width': 1280, 'height': 900})
            self.page = self.browser_context.new_page()
            self.page.set_default_timeout(2000)
            self.page.goto(url, wait_until='domcontentloaded', timeout=15000)
        except BaseException:
            self.close()
            raise

    def context(self):
        return {'backend': 'browser', 'url': self.page.url,
                'viewport': self.page.viewport_size, 'focused': not self.page.is_closed(),
                'pages': len(self.browser_context.pages)}

    def capture(self):
        return decode(self.page.screenshot(type='png')), self.context()

    def describe(self):
        return {'text': self.page.locator('body').inner_text(), 'url': self.page.url}

    def validate(self, action):
        # Playwright performs locator/key validation before dispatching each action.
        if action['type'] == 'keypress' and any(len(key) > 40 for key in action['keys']):
            raise ValueError('invalid key name')

    def execute(self, action, shape):
        kind = action['type']
        if kind == 'click':
            if 'label' in action:
                self.page.get_by_role(action.get('role', 'button'), name=action['label'], exact=True).click()
            else:
                self.page.mouse.click(action['x'], action['y'])
        elif kind == 'move':
            self.page.mouse.move(action['x'], action['y'])
        elif kind == 'type':
            self.page.keyboard.insert_text(action['text'])
        elif kind == 'fill':
            self.page.get_by_role(action.get('role', 'textbox'), name=action['label'], exact=True).fill(action['text'])
        elif kind == 'keypress':
            aliases = {'CMD': 'Meta', 'CTRL': 'Control', 'ALT': 'Alt', 'SHIFT': 'Shift',
                       'ENTER': 'Enter', 'ESC': 'Escape', 'BACKSPACE': 'Backspace', 'TAB': 'Tab'}
            self.page.keyboard.press('+'.join(aliases.get(k.upper(), k) for k in action['keys']))
        elif kind == 'scroll':
            self.page.mouse.wheel(action['dx'], action['dy'])

    def close(self):
        try:
            if self.browser:
                self.browser.close()
        finally:
            self.runtime.stop()


class MacBackend:
    """A single selected app/window, with image-pixel -> screen-point mapping."""
    KEYCODES = {'A': 0, 'S': 1, 'D': 2, 'F': 3, 'H': 4, 'G': 5, 'Z': 6,
                'X': 7, 'C': 8, 'V': 9, 'B': 11, 'Q': 12, 'W': 13, 'E': 14,
                'R': 15, 'Y': 16, 'T': 17, '1': 18, '2': 19, '3': 20, '4': 21,
                '6': 22, '5': 23, '=': 24, '9': 25, '7': 26, '-': 27, '8': 28,
                '0': 29, ']': 30, 'O': 31, 'U': 32, '[': 33, 'I': 34, 'P': 35,
                'ENTER': 36, 'L': 37, 'J': 38, "'": 39, 'K': 40, ';': 41,
                '\\': 42, ',': 43, '/': 44, 'N': 45, 'M': 46, '.': 47, 'TAB': 48,
                'SPACE': 49, '`': 50, 'BACKSPACE': 51, 'ESC': 53,
                'LEFT': 123, 'RIGHT': 124, 'DOWN': 125, 'UP': 126}
    MODIFIERS = {'CMD': 'kCGEventFlagMaskCommand', 'CTRL': 'kCGEventFlagMaskControl',
                 'ALT': 'kCGEventFlagMaskAlternate', 'SHIFT': 'kCGEventFlagMaskShift'}

    def __init__(self, app, *, window_id=None):
        import AppKit
        import Quartz
        from .accessibility import Accessibility
        self.q, self.ak = Quartz, AppKit
        if not Quartz.CGPreflightScreenCaptureAccess():
            raise RuntimeError('Screen Recording permission is unavailable for this process')
        running = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(app)
        if not running:
            subprocess.run(['/usr/bin/open', '-b', app], check=True, capture_output=True, timeout=5)
            deadline = time.monotonic()+3
            while not running and time.monotonic() < deadline:
                time.sleep(0.05)
                running = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(app)
        if len(running) != 1:
            raise RuntimeError('app must be one running bundle ID (for example com.apple.calculator)')
        self.app = running[0]
        self.pid, self.bundle = int(self.app.processIdentifier()), app
        self.ax = Accessibility(self.pid)
        if not self.ax.trusted:
            raise RuntimeError('Accessibility permission is unavailable for this process')
        windows = self._windows()
        if window_id is None:
            if len(windows) != 1:
                raise RuntimeError(f'select --window-id from these app windows: {windows}')
            self.window_id = windows[0]['id']
        else:
            if not any(w['id'] == window_id for w in windows):
                raise ValueError('window ID does not belong to the selected app')
            self.window_id = window_id
        self.app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        deadline = time.monotonic()+2
        while not self.context()['focused'] and time.monotonic() < deadline:
            time.sleep(0.02)
        self.source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)

    def _windows(self):
        q = self.q
        rows = q.CGWindowListCopyWindowInfo(q.kCGWindowListOptionOnScreenOnly, q.kCGNullWindowID)
        return [{'id': int(row[q.kCGWindowNumber]), 'title': str(row.get(q.kCGWindowName, '')),
                 'bounds': {k: float(v) for k, v in row[q.kCGWindowBounds].items()}}
                for row in rows if int(row[q.kCGWindowOwnerPID]) == self.pid and
                int(row[q.kCGWindowLayer]) == 0 and row[q.kCGWindowBounds]['Width'] > 80 and
                row[q.kCGWindowBounds]['Height'] > 80]

    def context(self):
        windows = [w for w in self._windows() if w['id'] == self.window_id]
        if not windows:
            raise RuntimeError('selected window is no longer visible')
        front = self.ak.NSWorkspace.sharedWorkspace().frontmostApplication()
        return {'backend': 'mac', 'app': self.bundle, 'pid': self.pid,
                'window_id': self.window_id, 'bounds': windows[0]['bounds'],
                'focused': bool(front and int(front.processIdentifier()) == self.pid)}

    def capture(self):
        context = self.context()
        q = self.q
        try:
            image = q.CGWindowListCreateImage(q.CGRectNull, q.kCGWindowListOptionIncludingWindow,
                        self.window_id, q.kCGWindowImageBoundsIgnoreFraming | q.kCGWindowImageBestResolution)
            if image is None:
                raise RuntimeError('CG screenshot unavailable')
            rep = self.ak.NSBitmapImageRep.alloc().initWithCGImage_(image)
            png = bytes(rep.representationUsingType_properties_(self.ak.NSPNGFileType, {}))
        except Exception:
            # Older/newer macOS versions differ in CGWindowListCreateImage support.
            with tempfile.TemporaryDirectory(prefix='markforge-capture-') as temp:
                path = Path(temp) / 'window.png'
                subprocess.run(['/usr/sbin/screencapture', '-x', '-o', '-l', str(self.window_id), str(path)],
                               check=True, capture_output=True, timeout=5)
                png = path.read_bytes()
        if context != self.context():
            raise RuntimeError('window changed during capture; observe again')
        return decode(png), context

    def describe(self):
        return self.ax.describe()

    def validate(self, action):
        if action['type'] == 'fill':
            raise ValueError('mac fill is unsupported; click a text field then use type/keypress')
        if action['type'] == 'keypress':
            keys = [key.upper() for key in action['keys']]
            if keys[-1] not in self.KEYCODES or any(k not in self.MODIFIERS for k in keys[:-1]):
                raise ValueError('unsupported mac key/chord')

    def execute(self, action, shape):
        q = self.q
        kind = action['type']
        if kind == 'click' and 'label' in action:
            self.ax.click(action['label'], action.get('role', 'button'))
        elif kind in ('click', 'move'):
            bounds = self.context()['bounds']
            point = (bounds['X'] + action['x'] * bounds['Width']/shape[1],
                     bounds['Y'] + action['y'] * bounds['Height']/shape[0])
            events = (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp) if kind == 'click' else (q.kCGEventMouseMoved,)
            for event in events:
                q.CGEventPost(q.kCGHIDEventTap, q.CGEventCreateMouseEvent(self.source, event, point, 0))
        elif kind == 'type':
            # Unicode input preserves Chinese, case and punctuation; no clipboard use.
            text = action['text']
            units = len(text.encode('utf-16-le'))//2
            for down in (True, False):
                event = q.CGEventCreateKeyboardEvent(self.source, 0, down)
                q.CGEventKeyboardSetUnicodeString(event, units, text)
                q.CGEventPost(q.kCGHIDEventTap, event)
        elif kind == 'keypress':
            keys = [k.upper() for k in action['keys']]
            flags = 0
            for modifier in keys[:-1]:
                flags |= getattr(q, self.MODIFIERS[modifier])
            for down in (True, False):
                event = q.CGEventCreateKeyboardEvent(self.source, self.KEYCODES[keys[-1]], down)
                q.CGEventSetFlags(event, flags)
                q.CGEventPost(q.kCGHIDEventTap, event)
        elif kind == 'scroll':
            event = q.CGEventCreateScrollWheelEvent(self.source, q.kCGScrollEventUnitPixel, 2,
                                                   -int(action['dy']), -int(action['dx']))
            q.CGEventPost(q.kCGHIDEventTap, event)

    def close(self):
        # Stop controlling the app; never quit a user's application.
        pass
