"""模块 1 — 候选元素检测（确定性，零模型）。

把截图变成"候选可交互元素列表"：
  a) 连通域分析（cv2.connectedComponentsWithStats）抓实心色块（按钮/图标）；
  b) 轮廓 + 外接矩形抓边缘清晰的控件；
  c) 颜色聚类（每元素主色）；
  d) 尺寸/宽高比/实心度过滤掉背景噪声。

输出全部是确定性计算，同图同结果。
"""
from __future__ import annotations

import cv2
import numpy as np

from .types import Element

# 常见 UI 主色的 HSV 范围（可扩展）
HSV_PRESETS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    # name: (lower, upper)  H:0-180 S:0-255 V:0-255
    "red":    ((0, 90, 90),   (10, 255, 255)),
    "red2":   ((170, 90, 90), (180, 255, 255)),
    "green":  ((35, 60, 60),  (90, 255, 255)),
    "blue":   ((95, 70, 60),  (130, 255, 255)),
    "orange": ((11, 90, 90),  (34, 255, 255)),
    "purple": ((131, 50, 50), (169, 255, 255)),
    "cyan":   ((86, 60, 60),  (100, 255, 255)),
    "yellow": ((20, 90, 90),  (34, 255, 255)),
}


def _mask_to_elements(
    mask: np.ndarray,
    kind: str,
    source: str,
    min_area: int,
    max_area: int,
    min_aspect: float,
    max_aspect: float,
    min_fill: float,
) -> list[Element]:
    """对二值 mask 做连通域分析 → Element 列表。"""
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    out: list[Element] = []
    for i in range(1, n):  # 0 是背景
        x, y, w, h, area = stats[i]
        if area < min_area or area > max_area:
            continue
        aspect = w / h if h else 0.0
        if aspect < min_aspect or aspect > max_aspect:
            continue
        fill = area / (w * h) if w * h else 0.0
        if fill < min_fill:
            continue
        ys, xs = np.where(labels == i)
        if len(xs) == 0:
            continue
        out.append(Element(
            idx=0,  # 由上层统一编号
            bbox=(int(x), int(y), int(w), int(h)),
            centroid=(int(centroids[i][0]), int(centroids[i][1])),
            kind=kind,
            area=int(area),
            aspect=round(aspect, 3),
            fill_ratio=round(fill, 3),
            source=source,
        ))
    return out


def detect_elements(
    image_bgr: np.ndarray,
    hsv_presets: dict[str, tuple[tuple, tuple]] | None = None,
    min_area: int = 200,
    max_area: int | None = None,
    min_aspect: float = 0.15,
    max_aspect: float = 8.0,
    min_fill: float = 0.35,
    include_gray_edges: bool = True,
) -> list[Element]:
    """主入口：BGR 图 → 候选 Element 列表。

    Args:
        image_bgr: OpenCV BGR numpy 数组。
        hsv_presets: 颜色预设；None 用默认 HSV_PRESETS。
        min_area: 最小像素面积（过滤噪点）。
        max_area: 最大像素面积；None = 图的 25%。
        min_aspect/max_aspect: 宽高比范围（过滤整条横幅/竖线）。
        min_fill: 实心度下限（矩形框内前景占比）。
        include_gray_edges: 是否补 Canny 边缘检测（抓非彩色的线框控件）。
    """
    h_img, w_img = image_bgr.shape[:2]
    if max_area is None:
        max_area = int(0.25 * h_img * w_img)

    presets = hsv_presets or HSV_PRESETS
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)

    elements: list[Element] = []

    for name, (lo, hi) in presets.items():
        mask = cv2.inRange(hsv, np.array(lo), np.array(hi))
        # 开运算去毛刺
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        found = _mask_to_elements(
            mask, kind="button-ish", source="color",
            min_area=min_area, max_area=max_area,
            min_aspect=min_aspect, max_aspect=max_aspect, min_fill=min_fill,
        )
        for e in found:
            e.color_hex = _dominant_color_hex(image_bgr, e.bbox)
        elements.extend(found)

    if include_gray_edges:
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 60, 150)
        kernel = np.ones((3, 3), np.uint8)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
        found = _mask_to_elements(
            edges, kind="outline", source="edge",
            min_area=min_area, max_area=max_area,
            min_aspect=min_aspect, max_aspect=max_aspect,
            min_fill=0.10,  # 线框控件前景占比低
        )
        for e in found:
            e.color_hex = _dominant_color_hex(image_bgr, e.bbox)
        elements.extend(found)

    # 去重：IoU > 0.6 的重叠元素保留面积更大的
    elements = _dedupe_iou(elements, iou_thresh=0.6)
    # 排序：面积降序，稳定
    elements.sort(key=lambda e: e.area, reverse=True)
    # 统一 Set-of-Mark 编号
    for i, e in enumerate(elements, start=1):
        e.idx = i
    return elements


def _dominant_color_hex(image_bgr: np.ndarray, bbox: tuple[int, int, int, int]) -> str:
    """取 bbox 中位 BGR → hex。"""
    x, y, w, h = bbox
    roi = image_bgr[y:y + h, x:x + w]
    if roi.size == 0:
        return "#000000"
    flat = roi.reshape(-1, 3)
    med = np.median(flat, axis=0)
    b, g, r = med
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _dedupe_iou(elements: list[Element], iou_thresh: float = 0.6) -> list[Element]:
    """贪心去重：按面积降序保留，去掉与已保留元素 IoU 超阈值的。"""
    elements.sort(key=lambda e: e.area, reverse=True)
    kept: list[Element] = []
    for e in elements:
        if all(_iou(e.bbox, k.bbox) < iou_thresh for k in kept):
            kept.append(e)
    return kept
