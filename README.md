# MarkForge

**Deterministic, auditable visual grounding for GUI agents — zero model downloads.**

MarkForge turns "click that button" into an explainable computation. Given only a
screenshot, it detects candidate interactive elements with classic CV
(connected components + color + geometry), renders **Set-of-Mark** numbered
badges on them, and grounds queries to pixel coordinates.

```bash
python3 -m markforge.cli ground shot.png "color:#1769e0"   # → GROUNDED, centroid (639, 495)
python3 -m markforge.cli ground shot.png "text:Submit"     # → GROUNDED (needs OCR)
python3 -m markforge.cli ground shot.png "#2"              # → SoM badge lookup
```

Unlike VLM-driven clicking (browser-use, Claude computer-use), coordinates here
are **deterministic and replayable** — same input, same output, every decision
carries a machine-readable reason. Unlike model-based parsers (OmniParser,
SeeClick, OS-Atlas), it downloads **nothing**: OpenCV + Pillow is the whole
stack, and an optional OCR backend degrades gracefully to `none`.

## Why

GUI agents that can only *see* (no DOM, no accessibility tree — desktop apps,
VNC, canvas/WebGL) have two mainstream options today, and both give up
auditability:

| Route | Examples | What you lose |
|---|---|---|
| VLM decides everything | browser-use, claude computer-use | Coordinates are unauditable; hallucinated clicks cost real money |
| Dedicated detection models | OmniParser, SeeClick, OS-Atlas | 1–9B weights to download; several repos are unmaintained |

MarkForge fills the empty cell: **classic CV, deterministic scoring, safety
gates**. It is the right tool when the element you want is a solid-color or
outlined control — which covers most buttons, toggles, badges and icons in
real software UIs — and when you need to answer *"why did the agent click
(639, 495)?"* with evidence.

It implements, in ~700 lines of dependency-light Python, the ideas that the
research community validated:

- **Set-of-Mark** ([arXiv:2310.11441](https://arxiv.org/abs/2310.11441)) —
  overlay numbered marks so grounding becomes *reference by number*.
- **GUI grounding as the core capability** ([SeeClick, arXiv:2401.10935](https://arxiv.org/abs/2401.10935)) —
  grounding is a separate, testable step, not a side effect of a big model.
- Field survey: [LLM-Brained GUI Agents, arXiv:2411.18279](https://arxiv.org/abs/2411.18279).

Full comparison table (stars/activity checked 2026-10-13 via GitHub API) and
design rationale: [docs/l4-theory.md](docs/l4-theory.md).

## The pipeline

```
screenshot ─→ ①detect ─→ ②ocr(optional) ─→ ③annotate ─→ ④ground ─→ ⑤verify ─→ click
               connected    3 backends,     SoM badges    #N / text: /     fresh-frame
               components   graceful        + JSON        color: / max_area  re-check
               + HSV +      degradation     manifest      deterministic
               Canny edges                 (audit)       scoring
```

### Quickstart

```bash
pip install --index-url https://pypi.org/simple opencv-python-headless pillow
git clone https://github.com/Lannistersyh/MarkForge.git && cd MarkForge

# annotate a screenshot you already have
python3 -m markforge.cli --ocr none annotate your-shot.png --out marked.png

# ground it
python3 -m markforge.cli --ocr auto ground your-shot.png "color:#1769e0"
```

Optional backends: `brew install tesseract` unlocks `text:` queries;
`pip install playwright && playwright install chromium` unlocks screenshot
capture and real clicking.

### Query language

| Query | Meaning | Needs |
|---|---|---|
| `#3` | Click SoM badge №3 (look at the marked image first) | — |
| `text:Submit` | OCR text containment (exact 1.0 / word-level 0.6–0.8) | OCR |
| `color:#1769e0` | Nearest dominant color, **distance cap 80** — absent colors return `NO_MATCH`, never a forced guess | — |
| `max_area` | Largest element (fallback) | — |

When the top two candidates are within 10%, the result is `AMBIGUOUS` and the
runner-up is returned — the caller (human or upper-layer agent) decides, never
a silent pick.

### Safety gates (built into `Engine.click`)

- `status != GROUNDED` → refused (`AMBIGUOUS` is for the caller to resolve)
- text hit contains a dangerous word (delete / submit / purchase / login /
  upload / send …) → refused, unless explicitly overridden *and recorded*
- optional VLM re-check fails → refused
- before every real click, a fresh screenshot is re-detected; if the target
  coordinate no longer falls on a candidate element → **stale-coordinate refusal**
- every decision lands in `eng.last_audit` with a reason string

### Library use

```python
from markforge.engine import Engine

eng = Engine(ocr_backend="auto")            # or "none" for zero-OCR environments
els = eng.detect("shot.png")                # candidate elements
marked = eng.annotate("shot.png", els, "marked.png")
r = eng.ground("color:#1769e0", els)        # GroundResult
if r.status == "GROUNDED":
    verdict = eng.click(r, marked_image=marked["path"], dry_run=True)
    assert verdict["decision"] == "DRY_RUN"
```

### Demo

| Original | SoM-annotated |
|---|---|
| ![raw screenshot](assets/demo-shot.png) | ![marked with numbered badges](assets/demo-marked.png) |

Reproduce: `bash scripts/demo.sh` (needs a local HTTP server + a capture
backend; the script falls back to raw CDP via `scripts/cdp-shot.mjs`).

## Tests

```bash
python3 -m pytest tests/ -v
# 29 passed, 2 skipped (browser-marked e2e need playwright chromium)
```

Includes 8 regression locks from a self-review that caught five real bugs —
color false-positives, stale-coordinate theater, OCR backend crashes, ghost
elements from duplicate OCR blocks, CLI dead-ends. Details in
[docs/l4-theory.md §6b](docs/l4-theory.md).

## Honest limitations

- Flat/outline-only widgets and icon fonts detect poorly (that is what
  OmniParser's models are for).
- No OCR backend → `text:` queries unavailable (color / badge / geometry still work).
- Photo-like backgrounds produce false candidates; the target scenario is
  software UI, not natural images.
- No ScreenSpot benchmark run yet (planned; see docs).
- Elements larger than 25% of the frame are filtered by design (banners and
  modal overlays are backgrounds, not targets).

## License

MIT — see [LICENSE](LICENSE).
