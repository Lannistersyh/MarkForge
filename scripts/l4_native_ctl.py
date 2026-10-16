#!/usr/bin/env python3
"""macOS 真机 L4 控制器 — CGEvent 鼠标/键盘模拟。

这是四层实验室 L4 的真机后端：
  - move_to(x, y)     移动鼠标（零风险）
  - click(x, y)       单击（需坐标来自 MarkForge grounding）
  - type_text(s)      键盘输入

依赖：pyobjc-framework-Quartz（pip 装的，非系统 Python 需注意版本匹配）
权限：系统设置 → 隐私与安全性 → 辅助功能（勾选运行终端/DSH）

用法：
  python3 l4_native_ctl.py move 735 478        # 只移动
  python3 l4_native_ctl.py click 735 478       # 点击（先 ground 再来）
  python3 l4_native_ctl.py type "hello"        # 打字
"""
from __future__ import annotations

import sys
import time

import Quartz
from Quartz import (
    CGEventCreateMouseEvent,
    CGEventCreateKeyboardEvent,
    CGEventPost,
    kCGEventMouseMoved,
    kCGEventLeftMouseDown,
    kCGEventLeftMouseUp,
    kCGHIDEventTap,
    kCGEventSourceStateHIDSystemState,
    CGEventSourceCreate,
)
from Foundation import NSURL


def _source():
    return CGEventSourceCreate(kCGEventSourceStateHIDSystemState)


def move_to(x: float, y: float) -> None:
    ev = CGEventCreateMouseEvent(_source(), kCGEventMouseMoved,
                                 (x, y), 0)
    CGEventPost(kCGHIDEventTap, ev)


def click(x: float, y: float) -> None:
    src = _source()
    for kind in (kCGEventLeftMouseDown, kCGEventLeftMouseUp):
        ev = CGEventCreateMouseEvent(src, kind, (x, y), 0)
        CGEventPost(kCGHIDEventTap, ev)
        time.sleep(0.02)


# macOS 虚拟键码（仅小写字母与常用键；够演示用）
_KEYCODES = {
    'a': 0, 's': 1, 'd': 2, 'f': 3, 'h': 4, 'g': 5, 'z': 6, 'x': 7, 'c': 8,
    'v': 9, 'b': 11, 'q': 12, 'w': 13, 'e': 14, 'r': 15, 'y': 16, 't': 17,
    '1': 18, '2': 19, '3': 20, '4': 21, '6': 22, '5': 23, '=': 24, '9': 25,
    '7': 26, '-': 27, '8': 28, '0': 29, ']': 30, 'o': 31, 'u': 32, '[': 33,
    'i': 34, 'p': 35, '\n': 36, 'l': 37, 'j': 38, "'": 39, 'k': 40, ';': 41,
    '\\': 42, ',': 43, '/': 44, 'n': 45, 'm': 46, '.': 47, '`': 50, ' ': 49,
}


def type_text(text: str) -> None:
    src = _source()
    for ch in text.lower():
        code = _KEYCODES.get(ch)
        if code is None:
            continue  # 跳过不支持字符（大写/特殊符号不 shift，简化）
        down = CGEventCreateKeyboardEvent(src, code, True)
        up = CGEventCreateKeyboardEvent(src, code, False)
        CGEventPost(kCGHIDEventTap, down)
        CGEventPost(kCGHIDEventTap, up)
        time.sleep(0.01)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "move":
        move_to(float(sys.argv[2]), float(sys.argv[3]))
        print(f"moved to ({sys.argv[2]}, {sys.argv[3]})")
    elif cmd == "click":
        x, y = float(sys.argv[2]), float(sys.argv[3])
        move_to(x, y)
        time.sleep(0.05)
        click(x, y)
        print(f"clicked ({x}, {y})")
    elif cmd == "type":
        type_text(sys.argv[2])
        print(f"typed {len(sys.argv[2])} chars")
    else:
        print(f"unknown cmd: {cmd}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
