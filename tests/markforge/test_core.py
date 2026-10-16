"""MarkForge 单元测试 — 检测 / 标记 / grounding / 安全（无浏览器依赖）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from markforge.detect import detect_elements  # noqa: E402
from markforge.ground import ground  # noqa: E402
from markforge.marking import annotate  # noqa: E402
from markforge.types import Element  # noqa: E402


def _synthetic_ui(path: Path) -> None:
    """造一张合成 UI：白底 + 绿按钮 + 蓝按钮 + 红方块。"""
    img = Image.new("RGB", (400, 300), "white")
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 180, 90], fill=(24, 121, 78))     # 绿 #18794e
    d.rectangle([220, 40, 380, 90], fill=(23, 105, 224))   # 蓝 #1769e0
    d.rectangle([40, 200, 100, 260], fill=(200, 30, 30))   # 红 #c81e1e
    img.save(path)


@pytest.fixture(scope="module")
def ui_png(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("mf") / "ui.png"
    _synthetic_ui(p)
    return p


@pytest.fixture(scope="module")
def ui_bgr(ui_png: Path) -> np.ndarray:
    import cv2
    return cv2.imread(str(ui_png))


class TestDetect:
    def test_finds_three_blocks(self, ui_bgr):
        els = detect_elements(ui_bgr)
        colors = [e.color_hex for e in els if e.color_hex]
        assert len(els) >= 3, f"应至少找到 3 个色块，实际 {len(els)}"
        # 绿蓝红三主色都被抓到（允许色差）
        assert any(c.lower().startswith("#1") for c in colors)

    def test_deterministic(self, ui_bgr):
        a = [(e.bbox, e.centroid) for e in detect_elements(ui_bgr)]
        b = [(e.bbox, e.centroid) for e in detect_elements(ui_bgr)]
        assert a == b, "同图两次检测结果必须完全一致（确定性）"

    def test_indices_sequential(self, ui_bgr):
        els = detect_elements(ui_bgr)
        idxs = sorted(e.idx for e in els)
        assert idxs == list(range(1, len(els) + 1)), "Set-of-Mark 编号必须连续"


class TestMarking:
    def test_annotate_writes_marked_image(self, ui_png: Path, tmp_path: Path):
        import cv2
        bgr = cv2.imread(str(ui_png))
        els = detect_elements(bgr)
        out = tmp_path / "marked.png"
        meta = annotate(ui_png, els, out)
        assert out.exists()
        assert meta["n_marks"] == len(els)
        img = Image.open(out)
        assert img.size == (400, 300)
        # 标记图与原图不同
        import hashlib
        h1 = hashlib.md5(Image.open(ui_png).tobytes()).hexdigest()
        h2 = hashlib.md5(img.tobytes()).hexdigest()
        assert h1 != h2


class TestGround:
    @pytest.fixture(scope="class")
    def elements(self) -> list[Element]:
        return [
            Element(idx=1, bbox=(40, 40, 140, 50), centroid=(110, 65),
                    kind="button-ish", color_hex="#18794e", text="L3 语义确认",
                    area=7000, aspect=2.8, fill_ratio=1.0),
            Element(idx=2, bbox=(220, 40, 160, 50), centroid=(300, 65),
                    kind="button-ish", color_hex="#1769e0", text="L4 视觉确认",
                    area=8000, aspect=3.2, fill_ratio=1.0),
            Element(idx=3, bbox=(40, 200, 60, 60), centroid=(70, 230),
                    kind="button-ish", color_hex="#c81e1e", text=None,
                    area=3600, aspect=1.0, fill_ratio=1.0),
        ]

    def test_ground_by_index(self, elements):
        r = ground("#2", elements)
        assert r.status == "GROUNDED"
        assert r.chosen.idx == 2
        assert r.chosen.centroid == (300, 65)

    def test_ground_missing_index(self, elements):
        r = ground("#99", elements)
        assert r.status == "NO_MATCH"
        assert "不存在" in r.reason

    def test_ground_by_text(self, elements):
        r = ground("text:视觉确认", elements)
        assert r.status == "GROUNDED"
        assert r.chosen.idx == 2

    def test_ground_by_color(self, elements):
        r = ground("color:#1769e0", elements)
        assert r.status == "GROUNDED"
        assert r.chosen.idx == 2

    def test_ground_by_max_area(self, elements):
        r = ground("max_area", elements)
        assert r.status == "GROUNDED"
        assert r.chosen.idx == 2  # 8000 最大

    def test_ground_garbage_query(self, elements):
        r = ground("???what???", elements)
        assert r.status == "NO_MATCH"

    def test_ground_empty_elements(self):
        r = ground("#1", [])
        assert r.status == "NO_MATCH"

    def test_ambiguous_text(self):
        els = [
            Element(idx=1, bbox=(0, 0, 10, 10), centroid=(5, 5),
                    text="提交表单", area=100),
            Element(idx=2, bbox=(0, 20, 10, 10), centroid=(5, 25),
                    text="提交表单", area=100),
        ]
        r = ground("text:提交表单", els)
        # 完全相同得分 → 完全命中两个都 1.0，second >= ratio*top → AMBIGUOUS
        assert r.status == "AMBIGUOUS"
        assert r.runner_up is not None


class TestRegressions:
    """2026-10-13 复核发现的五个 bug 的回归锁。"""

    def test_b1_color_query_no_false_positive(self):
        """B1: 查询的颜色页面上不存在时必须 NO_MATCH，不许硬凑最近邻。"""
        els = [
            Element(idx=1, bbox=(0, 0, 10, 10), centroid=(5, 5),
                    color_hex="#999999", area=100),
            Element(idx=2, bbox=(0, 20, 10, 10), centroid=(5, 25),
                    color_hex="#888888", area=100),
        ]
        r = ground("color:#ffffff", els)  # 查白色——只有灰
        assert r.status == "NO_MATCH", f"假阳性: {r.status} {r.reason}"
        r2 = ground("color:#ff0000", els)  # 查纯红——只有灰
        assert r2.status == "NO_MATCH"

    def test_b1_color_query_near_match_still_grounded(self):
        """B1 反向验证：真实相近颜色（距离 ≤ 上限 80）仍应 GROUNDED。
        场景：查询色与实际渲染色有轻微光照偏差（如抗锯齿/主题变体）。"""
        els = [
            Element(idx=1, bbox=(0, 0, 10, 10), centroid=(5, 5),
                    color_hex="#3267d8", area=100),  # 实际渲染的蓝
        ]
        r = ground("color:#1769e0", els)  # 距离约 30 < 80
        assert r.status == "GROUNDED"

    def test_b1_color_query_reason_reports_distance(self):
        els = [Element(idx=1, bbox=(0, 0, 10, 10), centroid=(5, 5),
                       color_hex="#999999", area=100)]
        r = ground("color:#ffffff", els)
        assert "超过上限" in r.reason

    def test_b3_explicit_missing_backend_fails_fast(self):
        """B3: 显式指定不可用的 OCR 后端 → 构造时 ValueError，不静默也不崩在 detect。"""
        from markforge.engine import Engine
        with pytest.raises(ValueError, match="tesseract"):
            Engine(ocr_backend="tesseract")

    def test_b4_duplicate_ocr_blocks_deduped(self):
        """B4: OCR 重复输出同一词块 → 去重后不得产生幽灵元素/假 AMBIGUOUS。"""
        from markforge.ocr import attach_text_to_elements
        els = [
            Element(idx=1, bbox=(0, 0, 100, 40), centroid=(50, 20), area=4000),
            Element(idx=2, bbox=(0, 100, 100, 40), centroid=(50, 120), area=4000),
        ]
        ocr = [
            {"text": "确定", "bbox": (10, 10, 50, 20), "conf": 0.9},
            {"text": "确定", "bbox": (10, 10, 50, 20), "conf": 0.9},  # 重复
        ]
        out = attach_text_to_elements(els, ocr)
        r = ground("text:确定", out)
        assert r.status == "GROUNDED", f"假 AMBIGUOUS: {r.reason}"
        assert r.chosen.bbox == (0, 0, 100, 40)  # 归属到按钮而非幽灵

    def test_b5_cli_click_without_url_auto_dry_run(self):
        """B5: click 子命令 --url 缺省时自动 dry-run 而非报错。"""
        import json as _json
        import subprocess
        import tempfile
        from PIL import Image, ImageDraw
        tmp = Path(tempfile.mktemp(suffix=".png"))
        img = Image.new("RGB", (600, 400), "white")
        d = ImageDraw.Draw(img)
        d.rectangle([100, 50, 300, 150], fill=(23, 105, 224))
        img.save(tmp)
        p = subprocess.run(
            [sys.executable, "-m", "markforge.cli", "--ocr", "none",
             "click", str(tmp), "color:#1769e0"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        assert p.returncode == 0, p.stdout + p.stderr
        marker = p.stdout.rfind("===JSON===")
        payload = _json.loads(p.stdout[marker + 10:].strip().splitlines()[0])
        assert payload["click"]["decision"] == "DRY_RUN"
        assert "自动 dry-run" in payload["click"].get("note", "")

    def test_b2_stale_coordinate_refused(self, tmp_path: Path):
        """B2: 新截图上原坐标无元素 → 点击必须拒签（防陈旧坐标是行为，不是口号）。"""
        from markforge.engine import Engine
        from markforge.types import Element, GroundResult

        # 帧 1：蓝按钮在 (100,50)-(300,150)，质心 (200,100)
        # 画布 600x400（按钮占 8.3%，低于 25% max_area 上限）
        frame1 = tmp_path / "frame1.png"
        img1 = Image.new("RGB", (600, 400), "white")
        d1 = ImageDraw.Draw(img1)
        d1.rectangle([100, 50, 300, 150], fill=(23, 105, 224))
        img1.save(frame1)

        # 帧 2（页面已变）：按钮移到远处，原质心 (200,100) 处空白
        frame2 = tmp_path / "frame2.png"
        img2 = Image.new("RGB", (600, 400), "white")
        d2 = ImageDraw.Draw(img2)
        d2.rectangle([450, 300, 570, 360], fill=(23, 105, 224))  # 移到角落且变小
        img2.save(frame2)

        eng = Engine(ocr_backend="none")
        els = eng.detect(frame1)
        assert len(els) >= 1, "帧 1 应检出按钮"
        r = eng.ground("color:#1769e0", els)
        assert r.status == "GROUNDED", r.reason
        orig = r.chosen
        assert orig.centroid == (200, 100), f"质心应 (200,100)，实际 {orig.centroid}"

        check = eng._validate_fresh(frame2, orig)
        assert check["ok"] is False, "页面已变，坐标应判陈旧"
        assert "无候选" in check["reason"]

    def test_b2_fresh_coordinate_passes(self, tmp_path: Path):
        """B2 反向验证：页面没变时，新帧验证通过。"""
        from markforge.engine import Engine
        frame = tmp_path / "frame.png"
        img = Image.new("RGB", (600, 400), "white")
        d = ImageDraw.Draw(img)
        d.rectangle([100, 50, 300, 150], fill=(23, 105, 224))
        img.save(frame)

        eng = Engine(ocr_backend="none")
        els = eng.detect(frame)
        r = eng.ground("color:#1769e0", els)
        assert r.status == "GROUNDED", r.reason
        check = eng._validate_fresh(frame, r.chosen)  # 同一帧
        assert check["ok"] is True
        assert "fresh_idx" in check


class TestSafety:
    def test_dangerous_word_refused(self):
        from markforge.engine import Engine
        from markforge.types import GroundResult
        eng = Engine(ocr_backend="none")
        el = Element(idx=1, bbox=(0, 0, 10, 10), centroid=(5, 5),
                     text="删除全部数据", area=100)
        result = GroundResult(status="GROUNDED", query="text:删除", chosen=el)
        verdict = eng.click(result, dry_run=True)
        assert verdict["decision"] == "REFUSED"
        assert "危险词" in verdict["reason"]

    def test_dangerous_word_override(self):
        from markforge.engine import Engine
        from markforge.types import GroundResult
        eng = Engine(ocr_backend="none")
        el = Element(idx=1, bbox=(0, 0, 10, 10), centroid=(5, 5),
                     text="删除全部数据", area=100)
        result = GroundResult(status="GROUNDED", query="text:删除", chosen=el)
        verdict = eng.click(result, dry_run=True, override_danger=True)
        assert verdict["decision"] == "DRY_RUN"

    def test_non_grounded_refused(self):
        from markforge.engine import Engine
        from markforge.types import GroundResult
        eng = Engine(ocr_backend="none")
        result = GroundResult(status="AMBIGUOUS", query="text:x")
        verdict = eng.click(result, dry_run=True)
        assert verdict["decision"] == "REFUSED"
        assert "AMBIGUOUS" in verdict["reason"]

    def test_vlm_reject_blocks_click(self):
        from markforge.engine import Engine
        from markforge.types import GroundResult
        eng = Engine(ocr_backend="none",
                     vlm_command="""python3 -c "print('{\\"ok\\": false, \\"reason\\": \\"坐标不对\\"}')" """.replace("{idx}", "1"))
        el = Element(idx=1, bbox=(0, 0, 10, 10), centroid=(5, 5),
                     text="安全按钮", area=100)
        result = GroundResult(status="GROUNDED", query="#1", chosen=el)
        verdict = eng.click(result, marked_image="/tmp/x.png", dry_run=True)
        assert verdict["decision"] == "REFUSED"
        assert "VLM" in verdict["reason"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
