"""MarkForge 公共数据类型。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Element:
    """一个候选可交互元素。"""
    idx: int                  # Set-of-Mark 编号（1 起）
    bbox: tuple[int, int, int, int]  # (x, y, w, h) 像素
    centroid: tuple[int, int]        # 点击坐标
    kind: str = "region"             # button-ish / icon / text / region
    color_hex: str | None = None     # 主色
    text: str | None = None          # OCR 文本（可选）
    area: int = 0
    aspect: float = 0.0              # w/h
    fill_ratio: float = 0.0          # 前景占比
    score: float = 0.0               # grounding 得分（命中后填）
    source: str = "contour"          # contour / ocr / merge

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["bbox"] = list(self.bbox)
        d["centroid"] = list(self.centroid)
        return d


@dataclass
class GroundResult:
    """一次 grounding 调用的结果。"""
    status: str                      # GROUNDED / NO_MATCH / AMBIGUOUS / REFUSED
    query: str = ""
    chosen: Element | None = None
    runner_up: Element | None = None
    n_candidates: int = 0
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "query": self.query,
            "chosen": self.chosen.to_dict() if self.chosen else None,
            "runner_up": self.runner_up.to_dict() if self.runner_up else None,
            "n_candidates": self.n_candidates,
            "reason": self.reason,
            **self.extra,
        }


@dataclass
class MarkedImage:
    """叠加 Set-of-Mark 标记后的图。"""
    path: str
    n_marks: int
    width: int
    height: int
