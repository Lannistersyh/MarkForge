"""MarkForge 端到端测试 — 完整闭环验证。

策略（与网络环境解耦）：
  A. 合成 UI 闭环（必跑）：PIL 造一张真实布局的 UI 截图 →
     detect → annotate → ground → 安全检查 → dry_run click。
     这验证引擎全链路逻辑，不依赖任何浏览器。
  B. 真实浏览器闭环（可选，标记 browser）：本地 HTTP + 截图后端 +
     实际点击 + #result 状态验证。仅当 playwright 可导入时运行。
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

PLAYWRIGHT_OK = True
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    with sync_playwright() as _p:
        _p.chromium.launch(headless=True).close()
except Exception:
    PLAYWRIGHT_OK = False

pytestmark_browser = pytest.mark.browser if not PLAYWRIGHT_OK else pytest.mark.browser


def _make_realistic_ui(path: Path) -> None:
    """合成一张"真实风格"的 UI 截图：标题、说明段、两个按钮、状态栏。"""
    img = Image.new("RGB", (640, 480), (245, 247, 251))
    d = ImageDraw.Draw(img)
    # 卡片
    d.rounded_rectangle([80, 60, 560, 420], radius=18, fill="white")
    # 标题条（深色）
    d.rectangle([80, 60, 560, 100], fill=(16, 33, 62))
    # 绿按钮 #18794e
    d.rounded_rectangle([180, 200, 320, 260], radius=10, fill=(24, 121, 78))
    # 蓝按钮 #1769e0
    d.rounded_rectangle([340, 200, 480, 260], radius=10, fill=(23, 105, 224))
    # 红色小图标
    d.ellipse([200, 320, 230, 350], fill=(200, 30, 30))
    img.save(path)


class TestSyntheticE2E:
    """A. 合成 UI 完整闭环（无浏览器依赖）。"""

    @pytest.fixture(scope="class")
    def ui_shot(self, tmp_path_factory) -> Path:
        p = tmp_path_factory.mktemp("mf-e2e") / "ui.png"
        _make_realistic_ui(p)
        return p

    def test_full_pipeline_ground_blue(self, ui_shot: Path):
        from markforge.engine import Engine
        eng = Engine(ocr_backend="none")
        els = eng.detect(ui_shot)
        assert len(els) >= 3, f"至少 3 个候选（绿/蓝/红），实际 {len(els)}"

        marked = eng.annotate(ui_shot, els,
                              ui_shot.with_name("marked.png"))
        assert Path(marked["path"]).exists()

        r = eng.ground("color:#1769e0", els)
        assert r.status == "GROUNDED", r.reason
        assert r.chosen is not None
        # 蓝按钮质心必须落在其矩形内
        x, y, w, h = r.chosen.bbox
        cx, cy = r.chosen.centroid
        assert x <= cx <= x + w and y <= cy <= y + h

        dry = eng.click(r, marked_image=marked["path"], dry_run=True)
        assert dry["decision"] == "DRY_RUN"
        assert dry["target"]["idx"] == r.chosen.idx

    def test_full_pipeline_ground_green(self, ui_shot: Path):
        from markforge.engine import Engine
        eng = Engine(ocr_backend="none")
        els = eng.detect(ui_shot)
        r = eng.ground("color:#18794e", els)
        assert r.status == "GROUNDED"
        x, y, w, h = r.chosen.bbox
        cx, cy = r.chosen.centroid
        assert x <= cx <= x + w and y <= cy <= y + h

    def test_ground_by_mark_index_after_annotate(self, ui_shot: Path):
        """SoM 引用闭环：annotate 的编号能直接被 #N 反查。"""
        from markforge.engine import Engine
        eng = Engine(ocr_backend="none")
        els = eng.detect(ui_shot)
        for e in els:
            r = eng.ground(f"#{e.idx}", els)
            assert r.status == "GROUNDED"
            assert r.chosen.idx == e.idx

    def test_audit_trail_populated(self, ui_shot: Path):
        from markforge.engine import Engine
        eng = Engine(ocr_backend="none")
        els = eng.detect(ui_shot)
        r = eng.ground("max_area", els)
        eng.click(r, dry_run=True)
        audit = eng.last_audit
        assert audit["action"] == "click"
        assert audit["ts"]  # 时间戳存在
        assert "target" in audit


@pytest.mark.browser
@pytest.mark.skipif(not PLAYWRIGHT_OK, reason="playwright chromium 不可用（CDN 下载受限）")
class TestRealBrowserE2E:
    """B. 真实浏览器闭环（playwright 可用时）。"""

    def _free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _run(self, page: str, query: str):
        port = self._free_port()
        server = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port),
             "--directory", str(ROOT / "fixture")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.8)
            url = f"http://127.0.0.1:{port}/{page}"
            from markforge.engine import Engine
            eng = Engine(ocr_backend="none")
            shot = Path(f"/tmp/mf-e2e-{port}.png")
            eng.capture(url, shot)
            els = eng.detect(shot)
            assert len(els) >= 2
            r = eng.ground(query, els)
            assert r.status == "GROUNDED", r.reason

            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(viewport={"width": 1280, "height": 900})
                pg = ctx.new_page()
                pg.goto(url, wait_until="networkidle")
                pg.mouse.click(*r.chosen.centroid)
                pg.wait_for_timeout(400)
                text = pg.locator("#result").inner_text(timeout=3000)
                browser.close()
            return text
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()

    def test_blue_button_click_verifies(self):
        assert "L4 完成" in self._run("layer-demo.html", "color:#1769e0")

    def test_green_button_click_verifies(self):
        assert "L3 完成" in self._run("layer-demo.html", "color:#18794e")


def test_cli_ground_flow():
    """CLI 路径 + ===JSON=== 协议（用合成图，避免浏览器依赖）。"""
    tmp = Path("/tmp/mf-cli-test-ui.png")
    _make_realistic_ui(tmp)
    p = subprocess.run(
        [sys.executable, "-m", "markforge.cli",
         "--ocr", "none", "ground", str(tmp), "color:#1769e0"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    marker = p.stdout.rfind("===JSON===")
    assert marker >= 0
    tail = p.stdout[marker + len("===JSON==="):].strip()
    payload = json.loads(tail.splitlines()[0])
    assert payload["status"] == "GROUNDED"
    assert payload["chosen"]["color_hex"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
