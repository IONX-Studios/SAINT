"""
modules/vision/ocr.py

Text on screen with Windows' own OCR engine (Windows.Media.Ocr) on Windows, and Tesseract
(`tesseract` CLI, TSV output) on Linux: local, fast, no model to download. It is the rung between UI Automation and a vision model in the observation ladder
(modules/agent/autonomy/observe.py): used only when an app doesn't expose its text through
accessibility, and never for anything Windows or an API can answer directly.

    recognize(png_bytes)          -> {"text": ..., "lines": [{"text", "words": [{"text", "rect"}]}]}
    find_text("Submit", png, at)  -> (x, y, w, h) on screen, or None
"""

import asyncio
import csv
import io
import logging
import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("saint.vision.ocr")

_engine = None
_checked = False


def _get_engine():
    global _engine, _checked
    if _checked:
        return _engine
    _checked = True
    try:
        import winrt.windows.media.ocr as ocr
        _engine = ocr.OcrEngine.try_create_from_user_profile_languages()
    except Exception as e:                       # package missing / not Windows 10+
        log.info("ocr.unavailable %s", e)
        _engine = None
    return _engine


def available() -> bool:
    if os.name != "nt":
        return shutil.which("tesseract") is not None
    return _get_engine() is not None


def parse_tesseract_tsv(tsv: str) -> List[Dict]:
    """Lines from ``tesseract ... tsv`` output, in the same shape as the Windows engine's."""
    lines: Dict[tuple, Dict] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text:
            continue
        try:
            conf = float(row.get("conf") or -1)
            rect = tuple(int(row[k]) for k in ("left", "top", "width", "height"))
        except (KeyError, ValueError):
            continue
        if conf < 30:                                   # noise / guesses
            continue
        key = (row["block_num"], row["par_num"], row["line_num"])
        lines.setdefault(key, {"words": []})["words"].append({"text": text, "rect": rect})
    out = []
    for key in sorted(lines, key=lambda k: tuple(int(x) for x in k)):
        words = lines[key]["words"]
        out.append({"text": " ".join(w["text"] for w in words), "words": words})
    return out


def _recognize_tesseract(data: bytes) -> Dict:
    exe = shutil.which("tesseract")
    if not exe or not data:
        return {"text": "", "lines": [], "available": bool(exe)}
    try:
        res = subprocess.run([exe, "stdin", "stdout", "--psm", "11", "tsv"], input=bytes(data),
                             capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("ocr.tesseract_failed %s", e)
        return {"text": "", "lines": [], "available": True}
    if res.returncode != 0:
        log.warning("ocr.tesseract_failed rc=%s %s", res.returncode, res.stderr[-300:])
        return {"text": "", "lines": [], "available": True}
    lines = parse_tesseract_tsv(res.stdout.decode("utf-8", "replace"))
    return {"text": "\n".join(l["text"] for l in lines), "lines": lines, "available": True}


async def _recognize_async(data: bytes):
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(data)
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    return await _get_engine().recognize_async(bitmap)


def recognize(data: bytes) -> Dict:
    """OCR an encoded image (PNG/JPEG bytes). Never raises: {"text": "", "lines": []} when it can't."""
    if os.name != "nt":
        return _recognize_tesseract(data)
    if not data or _get_engine() is None:
        return {"text": "", "lines": [], "available": _get_engine() is not None}
    try:
        result = asyncio.run(_recognize_async(bytes(data)))
    except RuntimeError:
        # Called from a thread that already runs an event loop: use a private one.
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(_recognize_async(bytes(data)))
        finally:
            loop.close()
    except Exception as e:
        log.warning("ocr.failed %s", e)
        return {"text": "", "lines": [], "available": True}
    lines: List[Dict] = []
    for line in result.lines:
        words = []
        for w in line.words:
            r = w.bounding_rect
            words.append({"text": w.text, "rect": (int(r.x), int(r.y), int(r.width), int(r.height))})
        lines.append({"text": line.text, "words": words})
    return {"text": "\n".join(l["text"] for l in lines), "lines": lines, "available": True}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (s or "").lower()).strip()


def find_text(target: str, data: bytes, origin: Tuple[int, int] = (0, 0)) -> Optional[Tuple[int, int, int, int]]:
    """Screen rectangle of ``target`` (a word or a short phrase) in the image, offset by ``origin``
    (the image's top-left on screen). Whole phrase on one line first, then the best single word."""
    want = _norm(target).split()
    if not want:
        return None
    ocr = recognize(data)
    ox, oy = origin
    best = None
    for line in ocr["lines"]:
        words = line["words"]
        toks = [_norm(w["text"]) for w in words]
        for i in range(len(toks) - len(want) + 1):
            if toks[i:i + len(want)] == want:
                rects = [words[j]["rect"] for j in range(i, i + len(want))]
                x0 = min(r[0] for r in rects)
                y0 = min(r[1] for r in rects)
                x1 = max(r[0] + r[2] for r in rects)
                y1 = max(r[1] + r[3] for r in rects)
                return (ox + x0, oy + y0, x1 - x0, y1 - y0)
        if best is None and len(want) == 1:
            for w, t in zip(words, toks):
                if t.startswith(want[0]) and len(want[0]) >= 3:
                    x, y, ww, hh = w["rect"]
                    best = (ox + x, oy + y, ww, hh)
                    break
    return best
