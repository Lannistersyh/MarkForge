"""Small macOS AX bridge; uses system frameworks, no extra PyObjC package."""
from __future__ import annotations

import ctypes as C


class Accessibility:
    def __init__(self, pid):
        self.pid = pid
        self.ax = C.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self._bind(self.ax, 'AXIsProcessTrusted', C.c_bool, [])
        self._bind(self.ax, 'AXUIElementCreateApplication', C.c_void_p, [C.c_int])
        self._bind(self.ax, 'AXUIElementCopyAttributeValue', C.c_int, [C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)])
        self._bind(self.ax, 'AXUIElementPerformAction', C.c_int, [C.c_void_p, C.c_void_p])
        self._bind(self.cf, 'CFStringCreateWithCString', C.c_void_p, [C.c_void_p, C.c_char_p, C.c_uint32])
        self._bind(self.cf, 'CFStringGetCString', C.c_bool, [C.c_void_p, C.c_char_p, C.c_long, C.c_uint32])
        self._bind(self.cf, 'CFStringGetLength', C.c_long, [C.c_void_p])
        self._bind(self.cf, 'CFGetTypeID', C.c_ulong, [C.c_void_p])
        self._bind(self.cf, 'CFStringGetTypeID', C.c_ulong, [])
        self._bind(self.cf, 'CFArrayGetTypeID', C.c_ulong, [])
        self._bind(self.cf, 'CFArrayGetCount', C.c_long, [C.c_void_p])
        self._bind(self.cf, 'CFArrayGetValueAtIndex', C.c_void_p, [C.c_void_p, C.c_long])
        self._bind(self.cf, 'CFRetain', C.c_void_p, [C.c_void_p])
        self._bind(self.cf, 'CFRelease', None, [C.c_void_p])

    @staticmethod
    def _bind(lib, name, restype, argtypes):
        func = getattr(lib, name)
        func.restype, func.argtypes = restype, argtypes

    @property
    def trusted(self):
        return bool(self.ax.AXIsProcessTrusted())

    def _string(self, text):
        return self.cf.CFStringCreateWithCString(None, text.encode(), 0x08000100)

    def _copy(self, node, name):
        attr = self._string(name)
        value = C.c_void_p()
        try:
            error = self.ax.AXUIElementCopyAttributeValue(node, attr, C.byref(value))
            return value.value if error == 0 else None
        finally:
            self.cf.CFRelease(attr)

    def _text(self, node, name):
        value = self._copy(node, name)
        if not value:
            return ''
        try:
            if self.cf.CFGetTypeID(value) != self.cf.CFStringGetTypeID():
                return ''
            buf = C.create_string_buffer(self.cf.CFStringGetLength(value)*4+1)
            return buf.value.decode('utf-8') if self.cf.CFStringGetCString(value, buf, len(buf), 0x08000100) else ''
        finally:
            self.cf.CFRelease(value)

    def _nodes(self):
        if not self.trusted:
            raise RuntimeError('Accessibility permission is unavailable for this process')
        pending = [self.ax.AXUIElementCreateApplication(self.pid)]
        kept = []
        try:
            seen = 0
            while pending and seen < 240:
                node = pending.pop(0)
                seen += 1
                try:
                    role = self._text(node, 'AXRole')
                    data = {name: self._text(node, attr) for name, attr in
                            [('label', 'AXDescription'), ('title', 'AXTitle'),
                             ('value', 'AXValue'), ('identifier', 'AXIdentifier')]}
                    data['role'] = role
                    if any(data[k] for k in ('label', 'title', 'value', 'identifier')):
                        kept.append((self.cf.CFRetain(node), data))
                    children = self._copy(node, 'AXChildren')
                    if children:
                        try:
                            if self.cf.CFGetTypeID(children) == self.cf.CFArrayGetTypeID():
                                for i in range(min(self.cf.CFArrayGetCount(children), 240-seen-len(pending))):
                                    pending.append(self.cf.CFRetain(self.cf.CFArrayGetValueAtIndex(children, i)))
                        finally:
                            self.cf.CFRelease(children)
                finally:
                    self.cf.CFRelease(node)
            return kept
        except BaseException:
            for node, _ in kept:
                self.cf.CFRelease(node)
            raise
        finally:
            for node in pending:
                self.cf.CFRelease(node)

    def describe(self):
        if not self.trusted:
            return {'text': '', 'controls': [], 'accessibility': False}
        nodes = self._nodes()
        try:
            controls = [data for _, data in nodes]
            return {'text': '\n'.join(dict.fromkeys(d['value'] or d['title'] for d in controls
                    if d['role'] in ('AXStaticText', 'AXTextField', 'AXTextArea') and (d['value'] or d['title']))),
                    'controls': controls, 'accessibility': True}
        finally:
            for node, _ in nodes:
                self.cf.CFRelease(node)

    def click(self, label, role='button'):
        nodes = self._nodes()
        try:
            ax_role = {'button': 'AXButton', 'checkbox': 'AXCheckBox', 'menuitem': 'AXMenuItem'}.get(role, role)
            matches = [node for node, data in nodes if data['role'] == ax_role and
                       label in (data['label'], data['title'], data['value'], data['identifier'])]
            if len(matches) != 1:
                raise ValueError(f'AX target {label!r}: expected one match, got {len(matches)}')
            action = self._string('AXPress')
            try:
                error = self.ax.AXUIElementPerformAction(matches[0], action)
                if error:
                    raise RuntimeError(f'AXPress failed: {error}')
            finally:
                self.cf.CFRelease(action)
        finally:
            for node, _ in nodes:
                self.cf.CFRelease(node)
