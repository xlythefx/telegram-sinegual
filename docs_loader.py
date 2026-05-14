"""
Loads brand/lifestyle docs from docs/ as plain-text context for the LLM.

Layout:
  docs/*.pdf            - brand philosophy, archetypes, foundation material (rarely changes)
  docs/*.md, *.txt      - same, in markdown/text form
  docs/daily.md         - 'today's focus' (always loaded, you keep editing this)
  docs/notes/YYYY-MM-DD.md - dated archive of past daily notes

Extraction is cached to docs/.cache.json keyed by file mtime; PDFs are only
re-extracted when changed.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

_DOCS_DIR = Path(__file__).parent / "docs"
_NOTES_DIR = _DOCS_DIR / "notes"
_CACHE_FILE = _DOCS_DIR / ".cache.json"


def _read_cache() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(cache: dict) -> None:
    try:
        _DOCS_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception:
        pass


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts = []
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if t.strip():
            parts.append(t.strip())
    return "\n\n".join(parts)


def _extract_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".pdf":
        return _extract_pdf(path)
    if suf in (".md", ".txt"):
        return _extract_text(path)
    return ""


def _load_with_cache(path: Path, cache: dict) -> str:
    key = str(path.relative_to(_DOCS_DIR))
    mtime = path.stat().st_mtime
    entry = cache.get(key)
    if entry and entry.get("mtime") == mtime and "text" in entry:
        return entry["text"]
    text = _extract(path)
    cache[key] = {"mtime": mtime, "text": text}
    return text


def _list_brand_files() -> list[Path]:
    """Top-level brand docs (excludes notes/ and daily.md)."""
    if not _DOCS_DIR.exists():
        return []
    out = []
    for p in sorted(_DOCS_DIR.iterdir()):
        if p.is_dir():
            continue
        if p.name.startswith("."):
            continue
        if p.name.lower() == "daily.md":
            continue
        if p.suffix.lower() in (".pdf", ".md", ".txt"):
            out.append(p)
    return out


def load_brand_context(max_chars: int = 12000) -> str:
    """All brand-philosophy docs joined into a single string, capped at max_chars.

    Cached aggressively: PDFs are only re-extracted when their mtime changes.
    """
    cache = _read_cache()
    chunks = []
    for path in _list_brand_files():
        text = _load_with_cache(path, cache)
        if not text.strip():
            continue
        chunks.append(f"--- {path.stem} ---\n{text.strip()}")
    _write_cache(cache)

    full = "\n\n".join(chunks)
    if len(full) > max_chars:
        full = full[:max_chars] + "\n\n[...truncated...]"
    return full


def load_daily_focus() -> str:
    """Contents of docs/daily.md, or empty string. Always re-read (never cached)."""
    p = _DOCS_DIR / "daily.md"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="ignore").strip()


def load_recent_notes(days: int = 7) -> list[tuple[str, str]]:
    """Return [(filename, text), ...] for dated notes within the last N days."""
    if not _NOTES_DIR.exists():
        return []
    cutoff = date.today() - timedelta(days=days)
    out = []
    for p in sorted(_NOTES_DIR.iterdir()):
        if p.suffix.lower() not in (".md", ".txt"):
            continue
        # Expect YYYY-MM-DD prefix
        try:
            d = date.fromisoformat(p.stem[:10])
        except ValueError:
            continue
        if d < cutoff:
            continue
        out.append((p.name, p.read_text(encoding="utf-8", errors="ignore").strip()))
    return out


def list_loaded_docs() -> list[dict]:
    """For the /docs admin command. Returns metadata, not full text."""
    cache = _read_cache()
    out = []
    for path in _list_brand_files():
        key = str(path.relative_to(_DOCS_DIR))
        entry = cache.get(key, {})
        text_len = len(entry.get("text", ""))
        out.append({"name": path.name, "kind": "brand", "chars": text_len,
                    "size_bytes": path.stat().st_size})
    if (_DOCS_DIR / "daily.md").exists():
        t = load_daily_focus()
        out.append({"name": "daily.md", "kind": "daily", "chars": len(t),
                    "size_bytes": (_DOCS_DIR / "daily.md").stat().st_size})
    for name, text in load_recent_notes(days=30):
        out.append({"name": f"notes/{name}", "kind": "note", "chars": len(text),
                    "size_bytes": (_NOTES_DIR / name).stat().st_size})
    return out
