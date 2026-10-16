"""Persistent observe/act loop. Backends own one UI; a batch returns one new frame."""
from __future__ import annotations

import math
import time
import uuid
from pathlib import Path

import cv2
from .engine import Engine, DANGEROUS_PATTERNS


class ControlError(ValueError):
    pass


class Session:
    MAX_ACTIONS = 16

    def __init__(self, backend, *, max_frame_age=60):
        self.backend = backend
        self.engine = Engine(ocr_backend="none")
        self.max_frame_age = max_frame_age
        self.frame = None
        self._elements = {}
        self.observations = 0

    def observe(self, *, out=None, detect=False, ocr=False, marked_out=None):
        started = time.perf_counter()
        # A backend must return the same surface whose geometry is in context.
        image, context = self.backend.capture()
        self.frame = {"id": uuid.uuid4().hex, "image": image,
                      "context": context, "time": time.monotonic()}
        self._elements = {}
        self.observations += 1
        result = {"status": "OK", "frame_id": self.frame["id"],
                  "width": int(image.shape[1]), "height": int(image.shape[0]),
                  "context": context, "observation_count": self.observations,
                  "state": self.backend.describe()}
        if out:
            result["screenshot"] = self._save(image, out)
        if detect or marked_out:
            elements = self._detect(ocr)
            result["elements"] = [e.to_dict() for e in elements]
            if marked_out:
                # Annotate the cached frame; no recapture and no second detection.
                from .marking import annotate
                import tempfile
                with tempfile.TemporaryDirectory(prefix="markforge-mark-") as temp:
                    source = Path(temp) / "frame.png"
                    self._save(image, source)
                    dest = Path(marked_out).expanduser().resolve()
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    result["marked"] = annotate(source, elements, dest)
        result["elapsed_ms"] = round((time.perf_counter()-started)*1000, 2)
        return result

    @staticmethod
    def _save(image, out):
        path = Path(out).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise ControlError("PNG encoding failed")
        # Screenshots are private even when the parent directory is shared.
        import os
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(encoded.tobytes())
        return str(path)

    def _detect(self, ocr=False):
        if ocr not in self._elements:
            if ocr and self.engine.ocr_active is None:
                from .ocr import ocr_available
                # Do not implicitly import/download heavyweight OCR models.
                self.engine.ocr_active = ocr_available("tesseract")
                if not self.engine.ocr_active:
                    raise ControlError("OCR requested but tesseract is unavailable")
            self._elements[ocr] = self.engine.detect(self.frame["image"], ocr=ocr)
        return self._elements[ocr]

    @staticmethod
    def _number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ControlError("coordinates/deltas must be finite numbers")
        return value

    def _prepare(self, actions, stable_layout):
        if not isinstance(actions, list) or not 1 <= len(actions) <= self.MAX_ACTIONS:
            raise ControlError(f"actions must contain 1..{self.MAX_ACTIONS} items")
        prepared = []
        width, height = self.frame["image"].shape[1], self.frame["image"].shape[0]
        for action in actions:
            if not isinstance(action, dict):
                raise ControlError("each action must be an object")
            a = dict(action)
            kind = a.get("type")
            if kind in ("click", "move"):
                has_xy = "x" in a or "y" in a
                if sum((has_xy, "query" in a, "label" in a)) != 1:
                    raise ControlError("click/move needs exactly one of x+y, query, label")
                if "query" in a:
                    query = a["query"]
                    if not isinstance(query, str):
                        raise ControlError("query must be text")
                    r = self.engine.ground(query, self._detect(query.startswith("text:")))
                    if r.status != "GROUNDED":
                        raise ControlError(f"{r.status}: {r.reason}")
                    a["x"], a["y"] = r.chosen.centroid
                    a["target_text"] = r.chosen.text or ""
                if "label" in a:
                    if kind != "click" or not isinstance(a["label"], str) or not a["label"]:
                        raise ControlError("label requires a non-empty semantic click")
                else:
                    x, y = self._number(a.get("x")), self._number(a.get("y"))
                    if not (0 <= x < width and 0 <= y < height):
                        raise ControlError("coordinate outside the observed image")
            elif kind in ("type", "fill"):
                if not isinstance(a.get("text"), str) or len(a["text"]) > 4000:
                    raise ControlError("text must be a string of at most 4000 characters")
                if kind == "fill" and not a.get("label"):
                    raise ControlError("fill requires a semantic label")
            elif kind == "keypress":
                keys = a.get("keys")
                if not isinstance(keys, list) or not 1 <= len(keys) <= 5 or not all(isinstance(k, str) and k for k in keys):
                    raise ControlError("keys must be a list of 1..5 key names")
            elif kind == "scroll":
                a["dx"] = self._number(a.get("dx", 0))
                a["dy"] = self._number(a.get("dy", 0))
                if max(abs(a["dx"]), abs(a["dy"])) > 10000:
                    raise ControlError("scroll delta too large")
            else:
                raise ControlError(f"unknown action type: {kind}")
            text = str(a.get("label", "")) + " " + str(a.get("target_text", ""))
            if any(word.lower() in text.lower() for word in DANGEROUS_PATTERNS) and not a.get("confirmed"):
                raise ControlError("sensitive target needs confirmed=true after user authorization")
            # Validate backend capability/key names before executing any action.
            self.backend.validate(a)
            prepared.append(a)
        # Coordinate reuse is valid only when the caller has observed a stable layout.
        coord_indices = [i for i, a in enumerate(prepared) if "x" in a]
        if any(i > 0 for i in coord_indices) and not stable_layout:
            raise ControlError("later coordinate actions require stable_layout=true; otherwise observe again")
        if any(a["type"] in ("scroll", "fill") or a.get("changes_layout") for a in prepared[:-1]):
            raise ControlError("layout-changing action must end the batch; observe before continuing")
        return prepared

    def _guard(self):
        if time.monotonic() - self.frame["time"] > self.max_frame_age:
            raise ControlError("frame expired; observe again")
        current = self.backend.context()
        if not current.get("focused", True):
            raise ControlError("target lost focus; no input sent")
        if current != self.frame["context"]:
            raise ControlError("window, geometry or page changed; observe again")

    def act(self, *, frame_id, actions, stable_layout=False, out=None,
            expect_text=None, timeout_ms=1500, dry_run=False):
        started = time.perf_counter()
        completed = []
        try:
            if not self.frame or frame_id != self.frame["id"]:
                raise ControlError("unknown/stale frame_id; observe again")
            if not isinstance(timeout_ms, int) or not 0 <= timeout_ms <= 5000:
                raise ControlError("timeout_ms must be 0..5000")
            if expect_text is not None and (not isinstance(expect_text, str) or not expect_text):
                raise ControlError("expect_text must be a non-empty string")
            prepared = self._prepare(actions, stable_layout)
            self._guard()
            if dry_run:
                return {"status": "DRY_RUN", "planned": len(prepared), "frame_id": frame_id}
            for action in prepared:
                self._guard()  # cheap focus/window checks, not screenshots/OCR
                self.backend.execute(action, self.frame["image"].shape)
                item = {"type": action["type"]}
                # Never log typed contents, clipboard data or credentials.
                if "text" in action:
                    item["characters"] = len(action["text"])
                completed.append(item)
            verified = None
            if expect_text is not None:
                deadline = time.monotonic() + timeout_ms / 1000
                while True:
                    state = self.backend.describe()
                    verified = expect_text in state.get("text", "")
                    if verified or time.monotonic() >= deadline:
                        break
                    time.sleep(0.02)
            observation = self.observe(out=out)
            return {"status": "OK" if verified is not False else "VERIFY_FAILED",
                    "completed": len(completed), "actions": completed, "verified": verified,
                    "observation": observation,
                    "elapsed_ms": round((time.perf_counter()-started)*1000, 2)}
        except Exception as exc:
            # Do not automatically replay a partially executed batch.
            self.frame = None
            self._elements = {}
            return {"status": "PARTIAL" if completed else "REFUSED",
                    "completed": len(completed), "reason": str(exc), "needs_observation": True}

    def close(self):
        self.backend.close()
