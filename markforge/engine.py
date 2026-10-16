"""MarkForge 引擎门面 — 把五个模块串成一条流水线。

用法（库）：
    from markforge.engine import Engine
    eng = Engine(ocr_backend="auto")
    shot = eng.capture("http://127.0.0.1:8765/layer-demo.html")
    els  = eng.detect(shot)
    marked = eng.annotate(shot, els, "marked.png")
    result = eng.ground("color:#1769e0", els)
    eng.click(result, dry_run=True)

用法（CLI，见 cli.py）：
    python3 -m markforge.cli detect shot.png --json
    python3 -m markforge.cli annotate shot.png --out marked.png
    python3 -m markforge.cli ground shot.png "text:视觉确认" --json
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .detect import detect_elements
from .ground import ground as ground_query
from .marking import annotate as annotate_image
from .ocr import attach_text_to_elements, ocr_available, run_ocr
from .types import Element, GroundResult
from .vlm import VLMRefuser

# 危险动作词（命中即拒签，除非 override=True 由上层显式确认）
DANGEROUS_PATTERNS = (
    "删除", "delete", "remove", "提交", "submit", "购买", "buy",
    "支付", "pay", "登录", "login", "上传", "upload", "发送", "send",
    "注销", "logout", "格式化", "format",
)


class Engine:
    """MarkForge 主引擎。"""

    def __init__(
        self,
        ocr_backend: str = "auto",
        ocr_languages: str = "eng+chi_sim",
        vlm_command: str | None = None,
        dangerous_patterns: tuple[str, ...] = DANGEROUS_PATTERNS,
    ):
        self.ocr_backend = ocr_backend
        self.ocr_languages = ocr_languages
        if ocr_backend == "auto":
            self.ocr_active = ocr_available()
        elif ocr_backend == "none":
            self.ocr_active = None
        else:
            # 显式指定的后端必须真的可用——快速失败，绝不静默降级
            if ocr_available(ocr_backend) is None:
                raise ValueError(
                    f"指定的 OCR 后端 '{ocr_backend}' 不可用。"
                    f"装 tesseract: brew install tesseract；"
                    f"装 easyocr: pip install easyocr；"
                    f"或改用 ocr_backend='auto'/'none'。"
                )
            self.ocr_active = ocr_backend
        self.vlm = VLMRefuser(vlm_command)
        self.dangerous_patterns = dangerous_patterns
        self.last_audit: dict[str, Any] = {}

    # ---------- 流水线各步 ----------

    def capture(self, url: str, out_path: str | Path | None = None,
                wait_ms: int = 800) -> Path:
        """截图（L4 输入）。后端可插拔：playwright → cdp → 报错。

        - playwright: headless chromium（首选）
        - cdp: 用户浏览器上的 CDP proxy（http://localhost:3456）
        """
        if out_path is None:
            out_path = Path(f"/tmp/markforge-{int(time.time()*1000)}.png")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(viewport={"width": 1280, "height": 900})
                page = ctx.new_page()
                page.goto(url, wait_until="networkidle")
                page.wait_for_timeout(wait_ms)
                page.screenshot(path=str(out_path))
                browser.close()
            if out_path.exists() and out_path.stat().st_size > 0:
                return out_path
        except ImportError:
            pass  # 降级到 CDP
        except Exception:
            pass  # 浏览器二进制缺失等运行时错误也降级到 CDP

        import urllib.request
        try:
            req = urllib.request.Request(
                "http://localhost:3456/new", data=url.encode(),
                method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                import json as _json
                target = _json.loads(resp.read())["targetId"]
            time.sleep(max(wait_ms, 1500) / 1000.0)
            req2 = urllib.request.Request(
                f"http://localhost:3456/screenshot?target={target}"
                f"&file={out_path}")
            urllib.request.urlopen(req2, timeout=30).read()
            # 关掉自己开的 tab
            try:
                urllib.request.urlopen(
                    f"http://localhost:3456/close?target={target}",
                    timeout=5).read()
            except Exception:
                pass
            if out_path.exists() and out_path.stat().st_size > 0:
                return out_path
            raise RuntimeError("CDP 截图失败（proxy 未运行或返回空图）")
        except Exception as e:
            raise RuntimeError(
                f"无可用截图后端：playwright 未安装且 CDP proxy 不可用（{e}）。"
                f"可 pip install playwright && playwright install chromium，"
                f"或先启动 web-access 的 cdp-proxy。"
            ) from e

    def load(self, image_path: str | Path) -> np.ndarray:
        img = cv2.imread(str(image_path))
        if img is None:
            raise FileNotFoundError(f"无法读取图片: {image_path}")
        return img

    def detect(self, image_or_path, ocr: bool = True) -> list[Element]:
        """截图 → 候选元素列表（含可选 OCR 文本）。"""
        if isinstance(image_or_path, (str, Path)):
            bgr = self.load(image_or_path)
            image_path = Path(image_or_path)
        else:
            bgr = image_or_path
            image_path = None

        elements = detect_elements(bgr)

        if ocr and self.ocr_active:
            from PIL import Image
            if image_path is not None:
                pil = Image.open(image_path).convert("RGB")
            else:
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                pil = Image.fromarray(rgb)
            ocr_results = run_ocr(pil, backend=self.ocr_active,
                                  languages=self.ocr_languages)
            elements = attach_text_to_elements(elements, ocr_results)
            elements.sort(key=lambda e: e.area, reverse=True)
            for i, e in enumerate(elements, start=1):
                e.idx = i
        return elements

    def annotate(self, image_path: str | Path, elements: list[Element],
                 out_path: str | Path | None = None) -> dict:
        if out_path is None:
            src = Path(image_path)
            out_path = src.with_name(src.stem + "-marked.png")
        return annotate_image(image_path, elements, out_path)

    def ground(self, query: str, elements: list[Element]) -> GroundResult:
        return ground_query(query, elements)

    def _validate_fresh(
        self,
        fresh_png: Path,
        orig: Element,
        tol: int = 20,
    ) -> dict:
        """在新截图上验证原坐标仍然落在某个候选元素上。

        防陈旧坐标的真实检查（不是只拍照存档）：
          1. 对新截图重新跑 detect（纯几何，无 OCR，快）；
          2. 原质心必须落在某候选的 bbox（含 tol 容差）内；
          3. 该候选面积不得比原元素大太多（防止整页横幅吞掉判定）。
        任一不满足 → 坐标已陈旧，点击应拒签。
        """
        try:
            els = detect_elements(self.load(fresh_png))
        except Exception as e:
            return {"ok": False, "reason": f"新截图检测失败: {e}"}
        cx, cy = orig.centroid
        for e in els:
            x, y, w, h = e.bbox
            if not (x - tol <= cx <= x + w + tol and y - tol <= cy <= y + h + tol):
                continue
            if e.area > orig.area * 4 + 2000:
                continue  # 巨大背景元素，不算有效覆盖
            return {"ok": True, "fresh_idx": e.idx,
                    "fresh_bbox": list(e.bbox)}
        return {"ok": False,
                "reason": (f"新截图上 ({cx},{cy})±{tol}px 内无候选元素"
                           f"（页面可能已变化，坐标陈旧）")}

    def click(
        self,
        result: GroundResult,
        marked_image: str | Path | None = None,
        url_for_playwright: str | None = None,
        dry_run: bool = False,
        override_danger: bool = False,
    ) -> dict:
        """按 grounding 结果点击。

        安全规则：
          - status != GROUNDED → 拒绝点击（AMBIGUOUS 需上层裁决）
          - 命中文本含危险词 → 拒签，除非 override_danger=True
          - VLM 复核（若配置）失败 → 拒签
          - 每次点击前重新截图（若给了 url_for_playwright）
        """
        audit: dict[str, Any] = {
            "action": "click", "query": result.query,
            "status": result.status, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self.last_audit = audit

        if result.status != "GROUNDED":
            audit["decision"] = "REFUSED"
            audit["reason"] = f"grounding 状态 {result.status}: {result.reason}"
            return audit

        chosen = result.chosen
        assert chosen is not None

        # 危险词检查
        text = (chosen.text or "")
        hit = next((p for p in self.dangerous_patterns
                    if p and p.lower() in text.lower()), None)
        if hit and not override_danger:
            audit["decision"] = "REFUSED"
            audit["reason"] = f"命中文本 '{text}' 含危险词 '{hit}'；需 override_danger=True"
            audit["danger"] = hit
            return audit

        # VLM 复核
        if self.vlm.enabled and marked_image is not None:
            verdict = self.vlm.verify(marked_image, chosen)
            audit["vlm"] = verdict
            if not verdict["ok"]:
                audit["decision"] = "REFUSED"
                audit["reason"] = f"VLM 复核未通过: {verdict['reason']}"
                return audit

        x, y = chosen.centroid
        audit["target"] = chosen.to_dict()

        if dry_run:
            audit["decision"] = "DRY_RUN"
            audit["reason"] = "dry_run=True，未实际点击"
            return audit

        # 实际点击：优先 Playwright（可控）；CDP 不可用时明确拒绝
        if url_for_playwright:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError:
                audit["decision"] = "REFUSED"
                audit["reason"] = "playwright 未安装，无点击后端（pip install playwright）"
                return audit
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(viewport={"width": 1280, "height": 900})
                page = ctx.new_page()
                page.goto(url_for_playwright, wait_until="networkidle")
                page.wait_for_timeout(500)
                # 点击前重新截图，并用它验证坐标没有陈旧
                fresh = Path("/tmp") / f"markforge-fresh-{int(time.time()*1000)}.png"
                page.screenshot(path=str(fresh))
                check = self._validate_fresh(fresh, chosen)
                audit["fresh_check"] = check
                if not check["ok"]:
                    audit["decision"] = "REFUSED"
                    audit["reason"] = f"陈旧坐标拒签: {check['reason']}"
                    browser.close()
                    return audit
                page.mouse.click(x, y)
                page.wait_for_timeout(300)
                after = Path("/tmp") / f"markforge-after-{int(time.time()*1000)}.png"
                page.screenshot(path=str(after))
                browser.close()
            audit["decision"] = "CLICKED"
            audit["screenshot_fresh"] = str(fresh)
            audit["screenshot_after"] = str(after)
            return audit

        audit["decision"] = "REFUSED"
        audit["reason"] = "未提供 url_for_playwright（无可用点击后端）"
        return audit
