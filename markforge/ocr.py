"""模块 2 — OCR（可选，三后端渐进降级）。

后端优先级：tesseract > easyocr > none。
没有 OCR 时引擎照常工作（颜色 + 几何 grounding），
文本查询会返回 NO_MATCH 并给出可读原因。

设计成纯函数：传入 PIL Image / numpy，返回 [(text, bbox, conf), ...]。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .types import Element


def ocr_available(backend: str | None = None) -> str | None:
    """探测可用后端，返回其名；都不可用返回 None。"""
    candidates = [backend] if backend else ["tesseract", "easyocr"]
    for b in candidates:
        if b == "tesseract":
            if shutil.which("tesseract"):
                return "tesseract"
        elif b == "easyocr":
            try:
                import easyocr  # noqa: F401
                return "easyocr"
            except ImportError:
                continue
    return None


def run_ocr(
    image,  # PIL.Image 或 numpy BGR
    backend: str = "auto",
    languages: str = "eng+chi_sim",
) -> list[dict]:
    """对图跑 OCR，返回 [{text, bbox:(x,y,w,h), conf}, ...]。

    backend: auto / tesseract / easyocr / none
    """
    chosen = backend
    if chosen == "auto":
        chosen = ocr_available() or "none"
    if chosen == "none":
        return []
    if chosen == "tesseract":
        return _ocr_tesseract(image, languages)
    if chosen == "easyocr":
        return _ocr_easyocr(image, languages)
    raise ValueError(f"unknown backend: {chosen}")


def _pil_to_tempfile(image) -> Path:
    import tempfile
    from PIL import Image
    if not isinstance(image, Image.Image):
        # 假设是 numpy BGR
        import cv2
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(image)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    image.save(tmp.name)
    return Path(tmp.name)


def _ocr_tesseract(image, languages: str) -> list[dict]:
    """tesseract TSV 输出 → 词块列表。"""
    import csv
    import io

    png = _pil_to_tempfile(image)
    try:
        proc = subprocess.run(
            ["tesseract", str(png), "stdout", "-l", languages, "tsv"],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            return []
        results: list[dict] = []
        reader = csv.DictReader(io.StringIO(proc.stdout), delimiter="\t")
        for row in reader:
            try:
                conf = float(row.get("conf", "-1"))
            except ValueError:
                continue
            if conf < 30:
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
            results.append({"text": text, "bbox": (x, y, w, h), "conf": conf})
        return results
    finally:
        png.unlink(missing_ok=True)


def _ocr_easyocr(image, languages: str) -> list[dict]:
    """easyocr（首次会下载模型，之后走缓存）。"""
    import numpy as np
    lang_codes = []
    if "chi_sim" in languages:
        lang_codes.append("ch_sim")
    lang_codes.append("en")

    import easyocr
    reader = easyocr.Reader(lang_codes, gpu=False, verbose=False)
    if hasattr(image, "save"):  # PIL
        import numpy as _np
        arr = _np.array(image.convert("RGB"))
    else:
        arr = image
    raw = reader.readtext(arr)
    results = []
    for bbox_pts, text, conf in raw:
        xs = [p[0] for p in bbox_pts]
        ys = [p[1] for p in bbox_pts]
        x, y = int(min(xs)), int(min(ys))
        w, h = int(max(xs) - x), int(max(ys) - y)
        if conf < 0.3:
            continue
        results.append({"text": text, "bbox": (x, y, w, h), "conf": float(conf)})
    return results


def attach_text_to_elements(
    elements: list[Element],
    ocr_results: list[dict],
    pad: int = 6,
    containment: float = 0.5,
) -> list[Element]:
    """把 OCR 词块贴到候选元素上（文本落元素框内即归属）。

    也会把没有对应元素的词块本身升格为 text 元素（常用于链接/标签）。
    """
    # 先去重：OCR 引擎可能对同一位置输出重复词块（同文本同 bbox），
    # 不去重会在同一视觉位置升格出幽灵元素，把 text 查询搅成假 AMBIGUOUS。
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for r in ocr_results:
        key = (r["text"], tuple(r["bbox"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    ocr_results = deduped

    used: set[int] = set()
    for e in elements:
        ex, ey, ew, eh = e.bbox
        best_j: int | None = None
        best_overlap = 0.0
        for j, r in enumerate(ocr_results):
            if j in used:
                continue
            tx, ty, tw, th = r["bbox"]
            # 文本框（略扩）与元素框的重叠
            ix1, iy1 = max(ex, tx - pad), max(ey, ty - pad)
            ix2, iy2 = min(ex + ew, tx + tw + pad), min(ey + eh, ty + th + pad)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            tarea = tw * th
            if tarea == 0:
                continue
            overlap = inter / tarea
            if overlap >= containment and overlap > best_overlap:
                best_j, best_overlap = j, overlap
        if best_j is not None:
            used.add(best_j)  # 用枚举索引，杜绝按值查找的错位
            e.text = ocr_results[best_j]["text"]
            e.kind = "text" if e.kind in ("region", "outline") else e.kind

    # 未归属的词块升格为独立文本元素
    extra: list[Element] = []
    next_idx = max((e.idx for e in elements), default=0)
    for i, r in enumerate(ocr_results):
        if i in used:
            continue
        x, y, w, h = r["bbox"]
        next_idx += 1
        extra.append(Element(
            idx=next_idx,
            bbox=(x, y, w, h),
            centroid=(x + w // 2, y + h // 2),
            kind="text",
            text=r["text"],
            area=w * h,
            aspect=round(w / h, 3) if h else 0.0,
            fill_ratio=1.0,
            source="ocr",
        ))
    return elements + extra
