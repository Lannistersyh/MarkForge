"""MarkForge CLI — 命令行入口。

子命令：
  capture   截图
  detect    检测候选元素（JSON）
  annotate  叠加 Set-of-Mark 编号（出 marked 图）
  ground    查询 → 坐标（JSON，含审计字段）
  click     grounding + 安全检查 + 实际点击
  audit     打印上一次 click 的审计记录

所有输出遵守四层实验室的 ===JSON=== 约定（单行 JSON 在标记后）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import Engine


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("===JSON===")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="markforge",
        description="MarkForge — 确定性 L4 视觉 grounding 引擎",
    )
    parser.add_argument("--ocr", default="auto",
                        choices=["auto", "tesseract", "easyocr", "none"])
    parser.add_argument("--vlm", default=None,
                        help="VLM 复核命令模板（{image} {idx} {x} {y} {question}）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # capture
    p_cap = sub.add_parser("capture", help="截图")
    p_cap.add_argument("--url", required=True)
    p_cap.add_argument("--out", default=None)

    # detect
    p_det = sub.add_parser("detect", help="检测候选元素")
    p_det.add_argument("image")
    p_det.add_argument("--no-ocr", action="store_true")

    # annotate
    p_ann = sub.add_parser("annotate", help="叠加 Set-of-Mark 编号")
    p_ann.add_argument("image")
    p_ann.add_argument("--out", default=None)

    # ground
    p_gr = sub.add_parser("ground", help="查询 → 坐标")
    p_gr.add_argument("image")
    p_gr.add_argument("query", help="#N / text:xxx / color:#hex / max_area")
    p_gr.add_argument("--no-ocr", action="store_true")

    # click
    p_clk = sub.add_parser("click", help="grounding + 点击")
    p_clk.add_argument("image")
    p_clk.add_argument("query")
    p_clk.add_argument("--url", default=None,
                       help="用于实际点击的页面 URL；缺省时仅做安全检查的 dry-run")
    p_clk.add_argument("--dry-run", action="store_true")
    p_clk.add_argument("--override-danger", action="store_true")
    p_clk.add_argument("--no-ocr", action="store_true")

    # audit
    p_aud = sub.add_parser("audit", help="打印上次审计（同进程内无效，供库调用演示）")

    args = parser.parse_args()
    eng = Engine(ocr_backend=args.ocr, vlm_command=args.vlm)

    if args.cmd == "capture":
        out = eng.capture(args.url, args.out)
        _emit({"status": "OK", "path": str(out)})

    elif args.cmd == "detect":
        els = eng.detect(args.image, ocr=not args.no_ocr)
        _emit({
            "status": "OK",
            "n": len(els),
            "ocr_backend": eng.ocr_active or "none",
            "elements": [e.to_dict() for e in els],
        })

    elif args.cmd == "annotate":
        els = eng.detect(args.image)
        marked = eng.annotate(args.image, els, args.out)
        _emit({"status": "OK", **marked})

    elif args.cmd == "ground":
        els = eng.detect(args.image, ocr=not args.no_ocr)
        result = eng.ground(args.query, els)
        _emit(result.to_dict())

    elif args.cmd == "click":
        els = eng.detect(args.image, ocr=not args.no_ocr)
        result = eng.ground(args.query, els)
        # 先出标记图供复核
        marked = eng.annotate(args.image, els)
        if args.url is None and not args.dry_run:
            # --url 缺省 = 只想走安全检查，不实际点击 → 自动 dry-run
            args.dry_run = True
        verdict = eng.click(
            result, marked_image=marked["path"],
            url_for_playwright=args.url,
            dry_run=args.dry_run,
            override_danger=args.override_danger,
        )
        if args.url is None and verdict.get("decision") == "DRY_RUN":
            verdict["note"] = "未提供 --url，自动 dry-run（加 --url 可实际点击）"
        _emit({
            "grounding": result.to_dict(),
            "marked_image": marked["path"],
            "click": verdict,
        })
        if verdict.get("decision") not in ("CLICKED", "DRY_RUN"):
            return 3
        return 0

    elif args.cmd == "audit":
        _emit(eng.last_audit or {"status": "EMPTY", "reason": "本进程尚无 click 记录"})

    return 0


if __name__ == "__main__":
    sys.exit(main())
