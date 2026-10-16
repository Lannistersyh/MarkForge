"""模块 5 — VLM 坐标复核器（可选安全网）。

VLM 全权派（截图丢给 VLM 直接要坐标）的问题是：
  1) 坐标不可审计——模型说 (640, 512) 你无法知道为什么；
  2) 幻觉坐标可能点到界面外或错误元素。

本模块反过来：grounding 由确定性引擎完成，VLM 只在**点击前**做一次
"这个坐标确实落在目标上吗"的布尔复核。VLM 不可用时跳过（降级运行）。

复核协议：
  输入：带标记截图 + 目标编号 + 计算出的坐标
  输出：{"ok": bool, "reason": str, "raw": <原始回复>}
"""
from __future__ import annotations

import json
from pathlib import Path

from .types import Element


class VLMRefuser:
    """调用外部 VLM CLI 做坐标复核。"""

    def __init__(self, command: str | None = None):
        """command 是一个可执行的复核命令模板，
        其中 {image} {idx} {x} {y} {question} 会被替换。
        例如：
          'echo {"ok": true}'
        或指向一个调用 Claude/GPT 的脚本。
        None = 禁用复核。"""
        self.command = command

    @property
    def enabled(self) -> bool:
        return bool(self.command)

    def verify(
        self,
        marked_image: str | Path,
        element: Element,
        question: str | None = None,
    ) -> dict:
        """返回 {"ok": bool, "reason": str, "raw": str}。"""
        if not self.enabled:
            return {"ok": True, "reason": "复核未配置，跳过（确定性引擎结果）",
                    "raw": "", "skipped": True}
        import shlex
        import subprocess

        q = question or f"编号 {element.idx} 的元素中心是否位于 ({element.centroid[0]}, {element.centroid[1]})？"
        cmd = (self.command
               .replace("{image}", str(marked_image))
               .replace("{idx}", str(element.idx))
               .replace("{x}", str(element.centroid[0]))
               .replace("{y}", str(element.centroid[1]))
               .replace("{question}", q))
        try:
            proc = subprocess.run(
                shlex.split(cmd), capture_output=True, text=True,
                timeout=60, check=False,
            )
            raw = proc.stdout.strip()
            if proc.returncode != 0:
                return {"ok": False, "reason": f"复核命令退出码 {proc.returncode}",
                        "raw": raw[:500] or proc.stderr[:500]}
            # 尝试解析 JSON
            try:
                data = json.loads(raw)
                return {"ok": bool(data.get("ok", False)),
                        "reason": str(data.get("reason", "")), "raw": raw[:500]}
            except json.JSONDecodeError:
                # 简单文本判定
                low = raw.lower()
                ok = ("yes" in low) or ("true" in low) or ("是" in raw) or ("对" in raw)
                return {"ok": ok, "reason": "文本启发式判定", "raw": raw[:500]}
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "复核超时", "raw": ""}
        except FileNotFoundError as e:
            return {"ok": False, "reason": f"复核命令不存在: {e}", "raw": ""}
