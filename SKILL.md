---
name: markforge
description: Operates desktop interfaces with persistent observe/act sessions, semantic controls first, and cached screenshot grounding when needed. Use for continuous computer operation, GUI grounding, Set-of-Mark, or screenshot clicking.
license: MIT
---

# MarkForge continuous control

The directory containing this file is the project/skill root. Keep the entire
repository together: the helpers need both `scripts/` and `markforge/`.
Resolve that root once; do not assume a particular username or checkout path.

Prefer one observation, a short batch of known actions, and one final
verification. Choose semantic controls when available; use screenshot
grounding for controls that cannot be addressed semantically.

## Computer Use host (preferred when available)

Follow the host's Computer Use documentation to obtain an authorized App or
supported Tab first. Load `scripts/cua-control.mjs` from its resolved absolute
file URL in the persistent `cua_repl` runtime:

```javascript
var {createController} = await import('file:///absolute/path/to/MarkForge/scripts/cua-control.mjs');
var control = createController(app); // Supported native-input Tab: {kind:'tab'}
var observed = await control.observe();
```

Replace the example file URL with the actual path. The default observation
returns accessibility text. Use `{visual:true}` only when screenshot context is
needed. On a calculator that is ready for input, a batch can be:

```javascript
var result = await control.act([
  {type:'keypress', key:'1'},
  {type:'keypress', key:'7'},
  {type:'keypress', key:'shift+equal'},
  {type:'keypress', key:'2'},
  {type:'keypress', key:'5'},
  {type:'keypress', key:'Return'}
], {frame_id:observed.frame_id});
```

The final state is emitted by the host and returned in `result.observation`.
Use its new frame and accessibility indices for the next batch. `click` takes
`target` as a current index or screenshot coordinate `[x,y]`; `keypress`,
`paste`, and `scroll` are also supported. DOM-only tabs need their documented
Playwright interface, not this native-input adapter.

Coordinates require a visual observation. Later coordinate actions in the same
batch require `stableLayout:true`, based on the observed layout. Use
`visualAfter:true` when a final screenshot is useful; ordinary verification
can read accessibility state.

## Standalone controller

From the project/skill root:

```bash
bash scripts/control.sh start --session calc --app com.apple.calculator
bash scripts/control.sh observe --session calc --out /tmp/calc-before.png
```

Use the returned frame ID with `act --frame-id ID --actions JSON`. End with
`stop --session calc`. See [the controller guide](docs/continuous-control.md)
for the action schema and browser mode. Use `--channel msedge` for installed
Edge or `--channel chrome` for installed Chrome instead of bundled Chromium.
`MARKFORGE_PYTHON` selects an explicit
interpreter; `requirements-macos.txt` lists native dependencies.

The standalone native host needs Accessibility and Screen Recording permission.
Do not modify OS permissions automatically. If an already-authorized Computer
Use host is available, use that route.

CV candidates are requested with `observe --detect` and cached for that frame;
click queries support `color:#hex`, `#N`, and `text:` when Tesseract is
installed. OCR/numbered annotation are optional, not obligatory extra steps.
The legacy `python -m markforge.cli` offline interface remains available.

## Batch boundaries and results

- At most 16 actions per batch. Stop at scrolling, navigation, dialogs, or other
  layout changes, then observe before continuing.
- Frames expire after 60 seconds. The standalone controller also checks focus,
  window geometry, and page identity before input.
- Complete already-authorized ordinary operations directly; follow the host's
  rules for sensitive actions instead of adding a per-keystroke confirmation.
- `PARTIAL` means some actions already happened. Never replay the entire batch;
  observe and determine what remains.
- `OK` means dispatch completed. Verify the user's goal in the returned UI;
  the standalone `--expect-text` option reports `VERIFY_FAILED` on mismatch.
- Stop owned workers when done. Idle workers expire after ten minutes; stopping
  never quits the user's native application.

Live smoke tests cover the Computer Use adapter and standalone Python on the
macOS calculator, plus the installed Edge channel on Selenium's public web form.
Do not generalize those results to every application, browser, or site.
