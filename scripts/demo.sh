#!/usr/bin/env bash
# MarkForge 一键演示：截图 → 检测 → SoM 标记 → grounding → dry-run 点击
# 用法: bash scripts/demo.sh [url]
set -euo pipefail

if [ -n "${BASH_SOURCE[0]:-}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
elif [ -n "${ZSH_VERSION:-}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${(%):-%N}")" && pwd)"
else
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
fi
BASE="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BASE"

URL="${1:-http://127.0.0.1:8765/demo.html}"
OUT=artifacts/demo
mkdir -p "$OUT"

echo "═══ MarkForge 演示 ═══"
echo "目标: $URL"

# 1) 截图：先试引擎（playwright/CDP proxy 自动降级），失败则原生 CDP 直连
if ! python3 - << EOF
import sys; sys.path.insert(0, ".")
from markforge.engine import Engine
eng = Engine(ocr_backend="auto")
eng.capture("$URL", "$OUT/shot.png")
EOF
then
  echo "引擎截图后端不可用，改用原生 CDP 直连 (9333)..."
  node scripts/cdp-shot.mjs 9333 "$URL" "$OUT/shot.png"
fi
echo "截图: $OUT/shot.png"

# 2) 检测 + SoM 标记
python3 -m markforge.cli --ocr none annotate "$OUT/shot.png" --out "$OUT/marked.png" | head -6

# 3) grounding（蓝色按钮）
echo ""
echo "── grounding: color:#1769e0 ──"
python3 -m markforge.cli --ocr none ground "$OUT/shot.png" "color:#1769e0" | tail -3

echo ""
echo "产物:"
ls -la "$OUT/"
echo ""
echo "看 $OUT/marked.png — 每个候选元素都有编号徽章（Set-of-Mark）"
