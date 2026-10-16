"""模块 4 — Grounding：把查询解析成坐标（确定性评分）。

查询语法（由上层 agent 或人给出，全部无歧义）：
  #3          → 点编号 3 的元素（Set-of-Mark 引用）
  text:提交     → 文本包含"提交"的元素（OCR 需可用）
  color:#1769e0 → 主色最接近该 hex 的元素
  max_area     → 面积最大的元素

评分可解释：每个候选给出 score 与命中原因，亚军一并返回，
上层可据此判断是否需要人工确认（AMBIGUOUS 阈值）。
"""
from __future__ import annotations

import re

from .types import Element, GroundResult

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _hex_dist(h1: str, h2: str) -> float:
    """两个 hex 颜色的欧氏距离（0..441）。"""
    def rgb(h: str) -> tuple[int, int, int]:
        s = h.lstrip("#")
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    r1, g1, b1 = rgb(h1)
    r2, g2, b2 = rgb(h2)
    return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5


def ground(
    query: str,
    elements: list[Element],
    ambiguous_ratio: float = 0.90,
    max_color_dist: float = 80.0,
) -> GroundResult:
    """主入口：查询 + 候选列表 → GroundResult。

    Args:
        ambiguous_ratio: 前两名得分/距离之比达到该值判 AMBIGUOUS。
        max_color_dist: 颜色查询的距离上限（RGB 欧氏距离 0..441）。
            最近候选超过该距离 → NO_MATCH（防止"查白色命中灰色"的假阳性）。
    """
    if not elements:
        return GroundResult(status="NO_MATCH", query=query, reason="无候选元素")

    q = query.strip()

    # ---- 1) Set-of-Mark 编号引用 ----
    m = re.fullmatch(r"#(\d+)", q)
    if m:
        want = int(m.group(1))
        for e in elements:
            if e.idx == want:
                e.score = 1.0
                return GroundResult(
                    status="GROUNDED", query=q, chosen=e,
                    n_candidates=len(elements), reason="编号命中",
                )
        return GroundResult(
            status="NO_MATCH", query=q, n_candidates=len(elements),
            reason=f"编号 {want} 不存在（共 {len(elements)} 个标记）",
        )

    # ---- 2) 文本包含 ----
    if q.startswith("text:"):
        needle = q[len("text:"):].strip().lower()
        if not needle:
            return GroundResult(status="NO_MATCH", query=q, reason="空文本查询")
        scored = [
            (e, _text_score(needle, e)) for e in elements
        ]
        scored = [(e, s) for e, s in scored if s > 0]
        if not scored:
            return GroundResult(
                status="NO_MATCH", query=q, n_candidates=len(elements),
                reason="OCR 不可用或无文本命中（可改用 color:/#N 查询）",
            )
        scored.sort(key=lambda t: t[1], reverse=True)
        top, second = scored[0], scored[1] if len(scored) > 1 else None
        top[0].score = round(top[1], 4)
        if second and second[1] >= ambiguous_ratio * top[1]:
            second[0].score = round(second[1], 4)
            return GroundResult(
                status="AMBIGUOUS", query=q, chosen=top[0], runner_up=second[0],
                n_candidates=len(elements),
                reason=f"两个候选得分接近（{top[1]:.2f} vs {second[1]:.2f}）",
            )
        return GroundResult(
            status="GROUNDED", query=q, chosen=top[0],
            n_candidates=len(elements), reason="文本命中",
        )

    # ---- 3) 颜色最近邻 ----
    if q.startswith("color:") or HEX_RE.match(q):
        hexs = q[len("color:"):] if q.startswith("color:") else q
        if not HEX_RE.match(hexs):
            return GroundResult(status="NO_MATCH", query=q, reason="非法 hex")
        target = hexs if hexs.startswith("#") else f"#{hexs}"
        colored = [e for e in elements if e.color_hex]
        if not colored:
            return GroundResult(
                status="NO_MATCH", query=q, n_candidates=len(elements),
                reason="候选均无主色信息",
            )
        scored = sorted(
            ((_hex_dist(target, e.color_hex), e) for e in colored),
            key=lambda t: t[0],
        )
        best_d, best_e = scored[0]
        # 距离上限：最近候选都离得太远 = 页面上没有这种颜色
        if best_d > max_color_dist:
            return GroundResult(
                status="NO_MATCH", query=q, n_candidates=len(elements),
                reason=(f"最近颜色距离 {best_d:.0f} 超过上限 {max_color_dist:.0f}"
                        f"（最近候选 {best_e.color_hex}）"),
                extra={"best_dist": round(best_d, 1),
                       "max_color_dist": max_color_dist},
            )
        second_d, second_e = scored[1] if len(scored) > 1 else (None, None)
        best_e.score = round(1.0 - min(best_d / 441.0, 1.0), 4)
        if second_e is not None and second_d <= ambiguous_ratio * best_d:
            second_e.score = round(1.0 - min(second_d / 441.0, 1.0), 4)
            return GroundResult(
                status="AMBIGUOUS", query=q, chosen=best_e, runner_up=second_e,
                n_candidates=len(elements),
                reason=f"两个颜色距离接近（{best_d:.0f} vs {second_d:.0f}）",
            )
        return GroundResult(
            status="GROUNDED", query=q, chosen=best_e,
            n_candidates=len(elements), reason=f"颜色距离 {best_d:.0f}",
        )

    # ---- 4) max_area ----
    if q == "max_area":
        best = max(elements, key=lambda e: e.area)
        best.score = 1.0
        return GroundResult(
            status="GROUNDED", query=q, chosen=best,
            n_candidates=len(elements), reason="面积最大",
        )

    return GroundResult(
        status="NO_MATCH", query=q, n_candidates=len(elements),
        reason="无法解析查询（支持 #N / text:xxx / color:#hex / max_area）",
    )


def _text_score(needle: str, e: Element) -> float:
    """文本匹配得分：完全包含=1.0，词级部分=比例。"""
    if not e.text:
        return 0.0
    hay = e.text.lower()
    if needle in hay:
        # 越短的命中越可信（"提交" vs "提交并保存"）
        return 1.0 if len(hay) <= len(needle) * 1.6 else 0.8
    # 词级部分匹配
    words = [w for w in re.split(r"\s+", needle) if w]
    if words and all(w in hay for w in words):
        return 0.6
    hits = sum(1 for w in words if w in hay)
    return 0.3 * hits / len(words) if words else 0.0
