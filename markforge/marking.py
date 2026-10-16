"""模块 3 — Set-of-Mark 渲染（arXiv:2310.11441 思想的轻量实现）。

给每个候选元素叠加：
  - 编号徽章（左上角，黑底黄字，1..N）
  - 半透明填充 + 边框

上层（VLM 或人）只需回答"点 N 号"，引擎即可换算回坐标——
这就是 SoM 论文"用标记把 grounding 变成引用"的核心收益。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .types import Element

# 徽章调色板（循环使用，保证相邻编号颜色可区分）
_PALETTE = [
    (255, 200, 0),    # 黄
    (0, 200, 255),    # 青
    (255, 120, 120),  # 粉
    (120, 255, 120),  # 绿
    (200, 120, 255),  # 紫
    (255, 160, 60),   # 橙
]

_BADGE_W = 34
_BADGE_H = 22
_STROKE = 3


def _load_font(size: int = 14) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def annotate(
    image_path: str | Path,
    elements: list[Element],
    out_path: str | Path,
    fill_alpha: int = 60,
) -> dict:
    """在截图上叠加编号标记，写出新图。

    Returns: {"path", "n_marks", "width", "height"}
    """
    base = Image.open(image_path).convert("RGB")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font()

    for e in elements:
        color = _PALETTE[(e.idx - 1) % len(_PALETTE)]
        x, y, w, h = e.bbox
        # 半透明填充
        draw.rectangle([x, y, x + w, y + h], fill=color + (fill_alpha,))
        # 边框
        draw.rectangle([x, y, x + w, y + h], outline=color + (255,), width=_STROKE)
        # 编号徽章（贴左上角，出框时内缩）
        bx = max(0, x - _BADGE_W // 2)
        by = max(0, y - _BADGE_H // 2)
        if bx + _BADGE_W > base.size[0]:
            bx = base.size[0] - _BADGE_W
        if by + _BADGE_H > base.size[1]:
            by = base.size[1] - _BADGE_H
        draw.rectangle([bx, by, bx + _BADGE_W, by + _BADGE_H],
                       fill=(0, 0, 0, 230))
        label = str(e.idx)
        tb = draw.textbbox((0, 0), label, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        draw.text(
            (bx + (_BADGE_W - tw) // 2, by + (_BADGE_H - th) // 2 - 2),
            label, fill=(255, 255, 0, 255), font=font,
        )

    merged = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.save(out_path)
    return {
        "path": str(out_path),
        "n_marks": len(elements),
        "width": base.size[0],
        "height": base.size[1],
    }
