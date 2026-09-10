#!/usr/bin/env python3
"""DocAtlas local server.

Indexes Ericsson Alex (.alx) and ZTE eReader (.zed) packages with SQLite FTS5,
serves the local web interface, streams archive entries into the reader, and
connects the grounded AI panel to a provider selected by the user.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes
import html
import json
import mimetypes
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / ".docatlas"
DB_PATH = DATA_DIR / "docatlas.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
SECRETS_PATH = DATA_DIR / "secrets.json"
DEFAULT_LIBRARY_ROOT = Path(os.getenv("DOCATLAS_LIBRARY_ROOT", "F:/Library"))
DEFAULT_LIBRARIES = {
    "ericsson": DEFAULT_LIBRARY_ROOT / "Ericsson_Alex",
    "zte": DEFAULT_LIBRARY_ROOT / "ZTE_Alex",
}
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_TEXT_CHARS = 1_750_000
STATIC_CANDIDATES = [APP_DIR / "out", APP_DIR / "dist" / "client"]

AI_PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {
        "name": "Ollama local",
        "model": "qwen2.5:7b",
        "base_url": "http://127.0.0.1:11434",
        "requires_key": False,
        "credential_url": "https://ollama.com/download",
    },
    "openai": {
        "name": "OpenAI",
        "model": "gpt-5-mini",
        "base_url": "https://api.openai.com/v1",
        "requires_key": True,
        "credential_url": "https://platform.openai.com/api-keys",
    },
    "gemini": {
        "name": "Google Gemini",
        "model": "gemini-2.5-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "requires_key": True,
        "credential_url": "https://aistudio.google.com/apikey",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "model": "claude-sonnet-4-5-20250929",
        "base_url": "https://api.anthropic.com/v1",
        "requires_key": True,
        "credential_url": "https://console.anthropic.com/settings/keys",
    },
    "openai_compatible": {
        "name": "OpenAI-compatible",
        "model": "local-model",
        "base_url": "http://127.0.0.1:1234/v1",
        "requires_key": False,
        "credential_url": "",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db_connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS vendors (
                id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                format TEXT NOT NULL DEFAULT 'alex'
            );

            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY,
                vendor_id INTEGER NOT NULL REFERENCES vendors(id),
                path TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS packages (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                vendor_id INTEGER NOT NULL REFERENCES vendors(id),
                path TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                title TEXT NOT NULL,
                product TEXT NOT NULL,
                version TEXT,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                modified_at REAL NOT NULL DEFAULT 0,
                entry_count INTEGER NOT NULL DEFAULT 0,
                indexed_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued',
                error TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY,
                package_id INTEGER NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
                entry_name TEXT NOT NULL,
                title TEXT NOT NULL,
                docno TEXT,
                revision TEXT,
                body TEXT NOT NULL,
                UNIQUE(package_id, entry_name)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                title,
                docno,
                body,
                content='pages',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );

            CREATE INDEX IF NOT EXISTS idx_packages_vendor_product
            ON packages(vendor_id, product);

            CREATE INDEX IF NOT EXISTS idx_packages_status
            ON packages(status);

            CREATE INDEX IF NOT EXISTS idx_pages_package
            ON pages(package_id);

            INSERT OR IGNORE INTO vendors(slug, name, format)
            VALUES ('ericsson', 'Ericsson', 'alex');

            INSERT OR IGNORE INTO vendors(slug, name, format)
            VALUES ('zte', 'ZTE', 'zed');
            """
        )
        db.execute("PRAGMA optimize")


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.metadata: dict[str, str] = {}
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "svg", "noscript"}:
            self._skip_depth += 1
            return
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if lowered == "meta":
            key = (attr_map.get("name") or attr_map.get("property") or "").upper()
            value = attr_map.get("content", "").strip()
            if key and value:
                self.metadata[key] = value
        if lowered == "title":
            self._in_title = True
        if lowered in {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "td", "th"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "svg", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if lowered == "title":
            self._in_title = False
        if lowered in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data.strip():
            return
        cleaned = data.strip()
        self.parts.append(cleaned)
        self.parts.append(" ")
        if self._in_title:
            self.title_parts.append(cleaned)

    def result(self, fallback_title: str) -> tuple[str, str, str, str]:
        raw = "".join(self.parts)
        lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines()]
        body = "\n".join(line for line in lines if line)[:MAX_TEXT_CHARS]
        title = (
            self.metadata.get("TITLE")
            or " ".join(self.title_parts).strip()
            or fallback_title
        )
        return (
            html.unescape(title).strip(),
            self.metadata.get("DOCNO", "").strip(),
            self.metadata.get("REV", "").strip(),
            body,
        )


def decode_html(raw: bytes) -> str:
    head = raw[:2048].decode("ascii", errors="ignore")
    match = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", head, re.IGNORECASE)
    encodings = [match.group(1) if match else "", "utf-8", "cp1252", "latin-1"]
    for encoding in encodings:
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            pass
    return raw.decode("utf-8", errors="replace")


def parse_page(raw: bytes, entry_name: str) -> tuple[str, str, str, str]:
    parser = TextExtractor()
    try:
        parser.feed(decode_html(raw))
        parser.close()
    except Exception:
        pass
    fallback = PurePosixPath(entry_name).stem.replace("_", " ")
    return parser.result(fallback)


def classify_product(name: str, vendor_slug: str = "ericsson") -> str:
    lowered = name.lower()
    if vendor_slug == "zte":
        if "nb-iot" in lowered or "nbiot" in lowered:
            return "NB-IoT"
        if "maintenance" in lowered:
            return "Maintenance"
        if "hardware" in lowered:
            return "Hardware"
        if "feature" in lowered:
            return "Feature Guide"
        if any(token in lowered for token in ("nr20", "5g", "zxran")):
            return "NR / 5G"
        if any(token in lowered for token in ("lte", "uniran", "fdd")):
            return "LTE / UniRAN"
        return "ZTE Documents"
    if "eniq" in lowered or "network iq" in lowered:
        return "ENIQ-S"
    if re.search(r"\benm\b", lowered) or "network manager" in lowered:
        return "ENM"
    if "ombs" in lowered:
        return "OMBS"
    if "radio" in lowered or "core" in lowered:
        return "Radio & Core"
    return "Khác"


def infer_version(name: str) -> str:
    version = re.search(r"\bV\d+(?:\.\d+)+\b", name, re.IGNORECASE)
    if version:
        return version.group(0)
    matches = re.findall(r"(?<!\d)(\d{2}(?:\.\d+)?)(?!\d)", name)
    return matches[-1] if matches else ""


def package_title(file_path: Path, vendor_slug: str) -> str:
    title = file_path.stem.replace("_", " ")
    if vendor_slug == "zte":
        title = re.sub(r"^Lib\d+-", "", title, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", title).strip()


def sanitize_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError("Thư mục không tồn tại hoặc không thể đọc.")
    return path


def get_or_create_source(path: Path, vendor_slug: str = "ericsson") -> int:
    with db_connect() as db:
        vendor = db.execute(
            "SELECT id FROM vendors WHERE slug = ?", (vendor_slug,)
        ).fetchone()
        if not vendor:
            raise ValueError("Vendor chưa được hỗ trợ.")
        db.execute(
            "INSERT OR IGNORE INTO sources(vendor_id, path, enabled, created_at) VALUES (?, ?, 1, ?)",
            (vendor["id"], str(path), utc_now()),
        )
        row = db.execute("SELECT id FROM sources WHERE path = ?", (str(path),)).fetchone()
        assert row
        return int(row["id"])


class IndexCoordinator:
    def __init__(self) -> None:
        self.jobs: queue.Queue[int] = queue.Queue()
        self.pending: set[int] = set()
        self.lock = threading.Lock()
        self.worker = threading.Thread(target=self._run, daemon=True, name="docatlas-indexer")
        self.worker.start()

    def enqueue(self, package_id: int) -> None:
        with self.lock:
            if package_id in self.pending:
                return
            self.pending.add(package_id)
            self.jobs.put(package_id)

    def _run(self) -> None:
        while True:
            package_id = self.jobs.get()
            try:
                self.index_package(package_id)
            except Exception as error:
                with db_connect() as db:
                    db.execute(
                        "UPDATE packages SET status='error', error=?, updated_at=? WHERE id=?",
                        (str(error)[:1000], utc_now(), package_id),
                    )
                traceback.print_exc()
            finally:
                with self.lock:
                    self.pending.discard(package_id)
                self.jobs.task_done()

    def index_package(self, package_id: int) -> None:
        with db_connect() as db:
            package = db.execute(
                """
                SELECT pk.*, v.slug vendor_slug
                FROM packages pk JOIN vendors v ON v.id=pk.vendor_id
                WHERE pk.id=?
                """,
                (package_id,),
            ).fetchone()
            if not package:
                return
            db.execute(
                "UPDATE packages SET status='indexing', indexed_count=0, error=NULL, updated_at=? WHERE id=?",
                (utc_now(), package_id),
            )

        package_path = Path(package["path"])
        parsed: list[tuple[str, str, str, str, str]] = []
        with zipfile.ZipFile(package_path, "r") as archive:
            skip_names = {
                "index.html", "alex.html", "alexmain.html", "elexmain.html",
                "alextbar.html", "alextbar2.html", "log_all.html",
            }
            html_entries = [
                entry
                for entry in archive.infolist()
                if not entry.is_dir()
                and entry.filename.lower().endswith((".html", ".htm"))
                and PurePosixPath(entry.filename).name.lower() not in skip_names
                and not (
                    package["vendor_slug"] == "zte"
                    and entry.filename.lower().startswith("documents/nodes/")
                )
            ]
            total = len(html_entries)
            with db_connect() as db:
                db.execute(
                    "UPDATE packages SET entry_count=?, updated_at=? WHERE id=?",
                    (total, utc_now(), package_id),
                )
                old_rows = db.execute(
                    "SELECT id FROM pages WHERE package_id=?", (package_id,)
                ).fetchall()
                if old_rows:
                    db.executemany(
                        "DELETE FROM pages_fts WHERE rowid=?",
                        [(row["id"],) for row in old_rows],
                    )
                db.execute("DELETE FROM pages WHERE package_id=?", (package_id,))

            for index, entry in enumerate(html_entries, start=1):
                try:
                    if entry.file_size > MAX_INDEX_BYTES:
                        raw = archive.open(entry).read(MAX_INDEX_BYTES)
                    else:
                        raw = archive.read(entry)
                    title, docno, revision, body = parse_page(raw, entry.filename)
                    if body:
                        parsed.append((entry.filename, title, docno, revision, body))
                except Exception:
                    continue

                if len(parsed) >= 20 or index == total:
                    with db_connect() as db:
                        for entry_name, title, docno, revision, body in parsed:
                            cursor = db.execute(
                                "INSERT INTO pages(package_id, entry_name, title, docno, revision, body) VALUES (?, ?, ?, ?, ?, ?)",
                                (package_id, entry_name, title, docno, revision, body),
                            )
                            row_id = cursor.lastrowid
                            db.execute(
                                "INSERT INTO pages_fts(rowid, title, docno, body) VALUES (?, ?, ?, ?)",
                                (row_id, title, docno, body),
                            )
                        db.execute(
                            "UPDATE packages SET indexed_count=?, updated_at=? WHERE id=?",
                            (index, utc_now(), package_id),
                        )
                    parsed.clear()

        with db_connect() as db:
            db.execute(
                "UPDATE packages SET status='ready', indexed_count=entry_count, error=NULL, updated_at=? WHERE id=?",
                (utc_now(), package_id),
            )
            db.execute("PRAGMA optimize")


INDEXER: IndexCoordinator | None = None


def scan_source(source_id: int, force: bool = False, max_packages: int | None = None) -> dict[str, int]:
    assert INDEXER
    with db_connect() as db:
        source = db.execute(
            "SELECT s.*, v.id vendor_id, v.slug vendor_slug FROM sources s JOIN vendors v ON v.id=s.vendor_id WHERE s.id=?",
            (source_id,),
        ).fetchone()
    if not source:
        raise ValueError("Không tìm thấy nguồn tài liệu.")

    source_path = sanitize_path(source["path"])
    found = updated = queued = 0
    # Small packages become searchable first while large multi-gigabyte bundles
    # continue indexing in the background.
    extensions = {"ericsson": {".alx"}, "zte": {".zed"}}.get(source["vendor_slug"], set())
    files = sorted(
        (item for item in source_path.iterdir() if item.is_file() and item.suffix.lower() in extensions),
        key=lambda item: (item.stat().st_size, item.name.lower()),
    )
    if max_packages is not None:
        files = files[:max(0, max_packages)]
    for file_path in files:
        found += 1
        stat = file_path.stat()
        title = package_title(file_path, source["vendor_slug"])
        product = classify_product(title, source["vendor_slug"])
        version = infer_version(title)
        with db_connect() as db:
            existing = db.execute(
                "SELECT id, modified_at, size_bytes, status FROM packages WHERE path=?",
                (str(file_path.resolve()),),
            ).fetchone()
            needs_index = (
                force
                or not existing
                or float(existing["modified_at"]) != stat.st_mtime
                or int(existing["size_bytes"]) != stat.st_size
                or existing["status"] in {"queued", "indexing", "error"}
            )
            if existing:
                package_id = int(existing["id"])
                db.execute(
                    """
                    UPDATE packages SET source_id=?, vendor_id=?, filename=?, title=?, product=?, version=?,
                    size_bytes=?, modified_at=?, status=CASE WHEN ? THEN 'queued' ELSE status END,
                    updated_at=? WHERE id=?
                    """,
                    (
                        source_id,
                        source["vendor_id"],
                        file_path.name,
                        title,
                        product,
                        version,
                        stat.st_size,
                        stat.st_mtime,
                        int(needs_index),
                        utc_now(),
                        package_id,
                    ),
                )
                if needs_index:
                    updated += 1
            else:
                cursor = db.execute(
                    """
                    INSERT INTO packages(source_id, vendor_id, path, filename, title, product, version,
                    size_bytes, modified_at, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                    """,
                    (
                        source_id,
                        source["vendor_id"],
                        str(file_path.resolve()),
                        file_path.name,
                        title,
                        product,
                        version,
                        stat.st_size,
                        stat.st_mtime,
                        utc_now(),
                    ),
                )
                package_id = int(cursor.lastrowid)
                updated += 1
        if needs_index:
            INDEXER.enqueue(package_id)
            queued += 1
    return {"found": found, "updated": updated, "queued": queued}


def load_settings() -> dict[str, str]:
    defaults = {
        "provider": "ollama",
        "model": str(AI_PROVIDERS["ollama"]["model"]),
        "base_url": str(AI_PROVIDERS["ollama"]["base_url"]),
    }
    if SETTINGS_PATH.exists():
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            defaults.update({key: str(value) for key, value in loaded.items() if key in defaults})
        except (OSError, json.JSONDecodeError):
            pass
    return defaults


def save_settings(value: dict[str, Any]) -> dict[str, str]:
    current = load_settings()
    provider = str(value.get("provider", current["provider"])).strip().lower()
    if provider not in AI_PROVIDERS:
        raise ValueError("Nhà cung cấp AI chưa được hỗ trợ.")
    model = str(value.get("model", current["model"])).strip()
    base_url = str(value.get("base_url", current["base_url"])).strip().rstrip("/")
    if not model or not base_url.startswith(("http://", "https://")):
        raise ValueError("Cấu hình AI không hợp lệ.")
    saved = {"provider": provider, "model": model, "base_url": base_url}
    SETTINGS_PATH.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    api_key = str(value.get("api_key", "")).strip()
    if api_key:
        save_provider_key(provider, api_key)
    if bool(value.get("clear_api_key", False)):
        save_provider_key(provider, "")
    return saved


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def protect_secret(value: str) -> str:
    raw = value.encode("utf-8")
    if os.name != "nt":
        return "plain:" + base64.b64encode(raw).decode("ascii")
    buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "DocAtlas", None, None, None, 0, ctypes.byref(target)
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(target.pbData, target.cbData)
        return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def unprotect_secret(value: str) -> str:
    prefix, _, encoded = value.partition(":")
    raw = base64.b64decode(encoded)
    if prefix == "plain":
        return raw.decode("utf-8")
    if prefix != "dpapi" or os.name != "nt":
        return ""
    buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def load_provider_keys() -> dict[str, str]:
    if not SECRETS_PATH.exists():
        return {}
    try:
        encrypted = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        if not isinstance(encrypted, dict):
            return {}
        return {
            provider: unprotect_secret(str(value))
            for provider, value in encrypted.items()
            if provider in AI_PROVIDERS and value
        }
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def save_provider_key(provider: str, api_key: str) -> None:
    encrypted: dict[str, str] = {}
    if SECRETS_PATH.exists():
        try:
            loaded = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                encrypted = {str(key): str(value) for key, value in loaded.items()}
        except (OSError, json.JSONDecodeError):
            pass
    if api_key:
        encrypted[provider] = protect_secret(api_key)
    else:
        encrypted.pop(provider, None)
    SECRETS_PATH.write_text(json.dumps(encrypted, indent=2), encoding="utf-8")
    try:
        SECRETS_PATH.chmod(0o600)
    except OSError:
        pass


def provider_api_key(provider: str) -> str:
    environment_keys = {
        "openai": ("OPENAI_API_KEY", "DOCATLAS_AI_KEY"),
        "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY", "DOCATLAS_AI_KEY"),
        "anthropic": ("ANTHROPIC_API_KEY", "DOCATLAS_AI_KEY"),
        "openai_compatible": ("DOCATLAS_AI_KEY",),
    }
    for name in environment_keys.get(provider, ()):
        if os.getenv(name):
            return str(os.getenv(name))
    return load_provider_keys().get(provider, "")


def public_settings() -> dict[str, Any]:
    settings = load_settings()
    provider = settings["provider"]
    metadata = AI_PROVIDERS[provider]
    return {
        **settings,
        "provider_name": metadata["name"],
        "requires_api_key": bool(metadata["requires_key"]),
        "api_key_available": bool(provider_api_key(provider)),
        "credential_url": metadata["credential_url"],
    }


def fts_query(value: str) -> str:
    stopwords = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "with",
        "các", "cái", "cho", "có", "của", "đây", "được", "gì", "là", "này", "những", "tài", "liệu", "thế", "trong", "và", "về",
    }
    terms = [
        term for term in re.findall(r"[\wÀ-ỹ]+", value, flags=re.UNICODE)
        if term.lower() not in stopwords
    ][:10]
    return " OR ".join(f'"{term.replace(chr(34), "")}"*' for term in terms)


def search_pages(query_value: str, package_id: int | None = None, limit: int = 40) -> list[dict[str, Any]]:
    query_value = query_value.strip()
    if not query_value:
        return []
    expression = fts_query(query_value)
    if not expression:
        return []
    sql = """
        SELECT p.id page_id, p.package_id, p.entry_name, p.title, p.docno, p.revision,
               pk.title package_title, pk.product, v.name vendor,
               snippet(pages_fts, 2, '<mark>', '</mark>', ' … ', 28) snippet,
               bm25(pages_fts, 6.0, 4.0, 1.0) score
        FROM pages_fts
        JOIN pages p ON p.id = pages_fts.rowid
        JOIN packages pk ON pk.id = p.package_id
        JOIN vendors v ON v.id = pk.vendor_id
        WHERE pages_fts MATCH ?
    """
    params: list[Any] = [expression]
    if package_id is not None:
        sql += " AND p.package_id = ?"
        params.append(package_id)
    sql += " ORDER BY score LIMIT ?"
    params.append(max(1, min(limit, 100)))
    with db_connect() as db:
        rows = db.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def package_list() -> list[dict[str, Any]]:
    with db_connect() as db:
        rows = db.execute(
            """
            SELECT pk.id, pk.filename, pk.title, pk.product, pk.version, pk.size_bytes,
                   pk.entry_count, pk.indexed_count, pk.status, pk.error, pk.updated_at,
                   v.name vendor, v.slug vendor_slug, s.path source_path,
                   (SELECT p.entry_name FROM pages p WHERE p.package_id=pk.id ORDER BY p.id LIMIT 1) first_entry
            FROM packages pk
            JOIN vendors v ON v.id=pk.vendor_id
            JOIN sources s ON s.id=pk.source_id
            ORDER BY v.name, pk.product, pk.title
            """
        ).fetchall()
    return [dict(row) for row in rows]


def retrieve_context(question: str, package_id: int | None) -> tuple[str, list[dict[str, Any]]]:
    results = search_pages(question, package_id, limit=6)
    if not results and package_id is not None:
        with db_connect() as db:
            rows = db.execute(
                """
                SELECT p.id page_id, p.package_id, p.entry_name, p.title, p.docno,
                       substr(p.body, 1, 2600) snippet, pk.title package_title
                FROM pages p JOIN packages pk ON pk.id=p.package_id
                WHERE p.package_id=? ORDER BY p.id LIMIT 4
                """,
                (package_id,),
            ).fetchall()
        results = [dict(row) for row in rows]
    blocks: list[str] = []
    citations: list[dict[str, Any]] = []
    with db_connect() as db:
        for index, item in enumerate(results, start=1):
            page = db.execute(
                "SELECT body FROM pages WHERE id=?", (item["page_id"],)
            ).fetchone()
            excerpt = (page["body"] if page else re.sub("<[^>]+>", "", item.get("snippet", "")))[:3500]
            label = f"Nguồn {index}: {item['title']}"
            if item.get("docno"):
                label += f" — {item['docno']}"
            blocks.append(f"[{label}]\n{excerpt}")
            citations.append(
                {
                    "page_id": item["page_id"],
                    "package_id": item["package_id"],
                    "entry_name": item["entry_name"],
                    "title": item["title"],
                    "docno": item.get("docno", ""),
                    "package_title": item.get("package_title", ""),
                }
            )
    return "\n\n".join(blocks), citations


def request_json(request: urllib.request.Request, timeout: int = 120) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
            if not isinstance(value, dict):
                raise RuntimeError("AI trả về dữ liệu không hợp lệ.")
            return value
    except urllib.error.HTTPError as error:
        body = error.read(4096).decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
            message = detail.get("error", {}).get("message") or detail.get("message") or body
        except json.JSONDecodeError:
            message = body
        raise RuntimeError(f"AI trả lỗi HTTP {error.code}: {str(message)[:500]}") from error


def call_provider(settings: dict[str, str], system: str, question: str) -> str:
    provider = settings["provider"]
    base_url = settings["base_url"].rstrip("/")
    model = settings["model"]
    api_key = provider_api_key(provider)
    if AI_PROVIDERS[provider]["requires_key"] and not api_key:
        raise RuntimeError("Chưa kết nối API key cho nhà cung cấp đã chọn.")

    if provider == "ollama":
        request = urllib.request.Request(
            base_url + "/api/chat",
            data=json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
                "stream": False,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        data = request_json(request)
        return str(data.get("message", {}).get("content", "")).strip()

    if provider in {"openai", "openai_compatible"}:
        endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
            }).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        data = request_json(request)
        return str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()

    if provider == "gemini":
        endpoint = f"{base_url}/models/{urllib.parse.quote(model, safe='')}:generateContent"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps({
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": question}]}],
            }).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            method="POST",
        )
        data = request_json(request)
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return "\n".join(str(part.get("text", "")) for part in parts).strip()

    if provider == "anthropic":
        endpoint = base_url if base_url.endswith("/messages") else base_url + "/messages"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps({
                "model": model,
                "max_tokens": 1600,
                "system": system,
                "messages": [{"role": "user", "content": question}],
            }).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        data = request_json(request)
        return "\n".join(
            str(block.get("text", ""))
            for block in data.get("content", [])
            if block.get("type") == "text"
        ).strip()
    raise RuntimeError("Nhà cung cấp AI chưa được hỗ trợ.")


def test_ai_connection() -> dict[str, Any]:
    settings = load_settings()
    answer = call_provider(
        settings,
        "Bạn đang kiểm tra kết nối. Chỉ trả lời đúng một từ: OK",
        "Trả lời OK",
    )
    return {
        "ok": bool(answer),
        "provider": settings["provider"],
        "model": settings["model"],
        "message": "Kết nối thành công." if answer else "AI không trả về nội dung.",
    }


def call_ai(question: str, package_id: int | None) -> dict[str, Any]:
    context, citations = retrieve_context(question, package_id)
    if not context:
        return {
            "answer": "Chưa có nội dung đã lập chỉ mục phù hợp. Hãy đợi quá trình quét hoàn tất hoặc thử một câu hỏi khác.",
            "citations": [],
            "mode": "no-context",
        }
    settings = load_settings()
    system = (
        "Bạn là trợ lý tài liệu kỹ thuật viễn thông. Chỉ trả lời từ phần NGUỒN được cung cấp. "
        "Trả lời bằng tiếng Việt rõ ràng, giữ nguyên lệnh và thuật ngữ kỹ thuật tiếng Anh. "
        "Mỗi kết luận quan trọng phải có trích dẫn dạng [Nguồn N]. Nếu nguồn không đủ, nói rõ điều đó.\n\n"
        f"NGUỒN:\n{context}"
    )
    try:
        answer = call_provider(settings, system, question)
        if answer:
            return {"answer": answer, "citations": citations, "mode": settings["provider"]}
        reason = "Mô hình không trả về nội dung."
    except Exception as error:
        reason = str(error)

    extracts = []
    for index, citation in enumerate(citations[:4], start=1):
        extracts.append(f"- [{index}] {citation['title']}{' — ' + citation['docno'] if citation.get('docno') else ''}")
    return {
        "answer": (
            "Chưa kết nối được mô hình AI, nhưng DocAtlas đã tìm các phần tài liệu gần nhất với câu hỏi:\n\n"
            + "\n".join(extracts)
            + f"\n\nChi tiết kết nối: {reason}"
        ),
        "citations": citations[:4],
        "mode": "extractive",
    }


def safe_archive_name(value: str) -> str:
    decoded = urllib.parse.unquote(value).replace("\\", "/")
    path = PurePosixPath(decoded)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Đường dẫn bên trong gói không hợp lệ.")
    return str(path)


def rewrite_document_html(raw: bytes, package_id: int) -> bytes:
    text = decode_html(raw)
    base = f"/api/packages/{package_id}/files/"

    def replace_edw(match: re.Match[str]) -> str:
        value = html.unescape(match.group(1))
        params = urllib.parse.parse_qs(value.split("#", 1)[0])
        name = (params.get("fn") or params.get("FN") or [""])[0]
        if not name:
            return "#"
        return base + urllib.parse.quote(name.replace("\\", "/"), safe="/")

    text = re.sub(r"edw:/alex\?([^\"'<>\s]+)", replace_edw, text, flags=re.IGNORECASE)
    text = re.sub(
        r"((?:href|src)\s*=\s*[\"'])([^\"']+)",
        lambda match: match.group(1) + (
            match.group(2)
            if re.match(r"^(?:[a-z]+:|#|/)", match.group(2), re.IGNORECASE)
            else match.group(2).replace("\\", "/")
        ),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\s+on\w+\s*=\s*(?:\"[^\"]*\"|'[^']*')", "", text, flags=re.IGNORECASE)
    reader_style = """
    <style id="docatlas-reader-style">
      html{background:#fff!important;color:#26364e!important} body{max-width:1100px;margin:0 auto!important;padding:34px 46px 90px!important;font-family:Inter,Segoe UI,Arial,sans-serif!important;font-size:16px!important;line-height:1.65!important;color:#36465c!important}
      #header-container{border-radius:8px!important;background:#eef4ff!important;border:1px solid #d4e1fa!important;padding:12px 16px!important;margin-bottom:24px!important}
      h1,h2,h3,h4,.DOCTITLE{color:#17345e!important;line-height:1.28!important}.DOCTITLE{font-size:2rem!important;font-weight:750!important;margin:18px 0 8px!important}.SUBTITLE{color:#61718a!important}
      a{color:#1759c9!important} table{max-width:100%!important;border-collapse:collapse!important} th{background:#edf3ff!important} td,th{padding:8px 10px!important;border-color:#cfd9e8!important}
      img{max-width:100%!important;height:auto!important}.note_table,.note{background:#f3f7ff!important}.body-content{max-width:none!important}
      pre,code{font-family:Consolas,monospace!important} pre{overflow:auto!important;padding:14px!important;background:#f4f6f9!important;border-radius:7px!important}
    </style>
    """
    if re.search(r"</head\s*>", text, re.IGNORECASE):
        text = re.sub(r"</head\s*>", reader_style + "</head>", text, count=1, flags=re.IGNORECASE)
    else:
        text = reader_style + text
    return text.encode("utf-8")


class DocAtlasHandler(SimpleHTTPRequestHandler):
    server_version = "DocAtlas/0.1"

    def __init__(self, *args: Any, static_dir: Path, **kwargs: Any) -> None:
        self.static_dir = static_dir
        super().__init__(*args, directory=str(static_dir), **kwargs)

    def log_message(self, format_string: str, *args: Any) -> None:
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), format_string % args))

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.path.startswith("/api/"):
            origin = self.headers.get("Origin", "")
            host = urllib.parse.urlparse(origin).hostname
            if host in {"127.0.0.1", "localhost", "::1"}:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("Yêu cầu quá lớn.")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Dữ liệu gửi lên không hợp lệ.")
        return value

    def send_json(self, value: Any, status: int = 200) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_error_json(self, error: Exception, status: int = 400) -> None:
        self.send_json({"error": str(error)}, status)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            try:
                self.handle_api_get(parsed)
            except (ValueError, KeyError, sqlite3.Error, zipfile.BadZipFile) as error:
                self.send_error_json(error)
            except Exception as error:
                traceback.print_exc()
                self.send_error_json(error, 500)
            return

        if parsed.path == "/health":
            self.send_json({"ok": True})
            return

        target = (self.static_dir / parsed.path.lstrip("/")).resolve()
        try:
            target.relative_to(self.static_dir.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if parsed.path != "/" and not target.exists():
            index = self.static_dir / "index.html"
            if index.exists():
                self.path = "/index.html"
        super().do_GET()

    def handle_api_get(self, parsed: urllib.parse.ParseResult) -> None:
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        if path == "/api/status":
            with db_connect() as db:
                row = db.execute(
                    """
                    SELECT count(*) packages,
                           sum(CASE WHEN status='ready' THEN 1 ELSE 0 END) ready,
                           sum(CASE WHEN status='indexing' THEN 1 ELSE 0 END) indexing,
                           sum(CASE WHEN status='queued' THEN 1 ELSE 0 END) queued,
                           sum(indexed_count) indexed_pages,
                           sum(entry_count) total_pages
                    FROM packages
                    """
                ).fetchone()
            self.send_json(dict(row))
            return
        if path == "/api/vendors":
            with db_connect() as db:
                rows = db.execute("SELECT * FROM vendors ORDER BY name").fetchall()
            self.send_json([dict(row) for row in rows])
            return
        if path == "/api/sources":
            with db_connect() as db:
                rows = db.execute(
                    """
                    SELECT s.id, s.path, s.enabled, s.created_at, v.name vendor, v.slug vendor_slug,
                           count(pk.id) package_count
                    FROM sources s JOIN vendors v ON v.id=s.vendor_id
                    LEFT JOIN packages pk ON pk.source_id=s.id
                    GROUP BY s.id ORDER BY v.name, s.path
                    """
                ).fetchall()
            self.send_json([dict(row) for row in rows])
            return
        if path == "/api/packages":
            self.send_json(package_list())
            return
        if path == "/api/search":
            query_value = (params.get("q") or [""])[0]
            package_value = (params.get("package_id") or [""])[0]
            package_id = int(package_value) if package_value else None
            limit = int((params.get("limit") or ["40"])[0])
            self.send_json(search_pages(query_value, package_id, limit))
            return
        if path == "/api/settings":
            self.send_json(public_settings())
            return

        download_match = re.fullmatch(r"/api/packages/(\d+)/download", path)
        if download_match:
            package_id = int(download_match.group(1))
            with db_connect() as db:
                package = db.execute(
                    "SELECT path, filename FROM packages WHERE id=?", (package_id,)
                ).fetchone()
            if not package:
                raise ValueError("Không tìm thấy gói tài liệu.")
            file_path = Path(package["path"]).resolve()
            if not file_path.exists() or not file_path.is_file():
                raise ValueError("File tài liệu gốc không còn tồn tại.")
            encoded_name = urllib.parse.quote(package["filename"])
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(file_path.stat().st_size))
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{encoded_name}")
            self.send_header("Cache-Control", "private, no-store")
            self.end_headers()
            with file_path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    self.wfile.write(chunk)
            return

        page_match = re.fullmatch(r"/api/packages/(\d+)/pages", path)
        if page_match:
            package_id = int(page_match.group(1))
            query_value = (params.get("q") or [""])[0].strip()
            with db_connect() as db:
                if query_value:
                    expression = fts_query(query_value)
                    rows = db.execute(
                        """
                        SELECT p.id, p.entry_name, p.title, p.docno, p.revision,
                               snippet(pages_fts, 2, '<mark>', '</mark>', ' … ', 20) snippet
                        FROM pages_fts JOIN pages p ON p.id=pages_fts.rowid
                        WHERE p.package_id=? AND pages_fts MATCH ? ORDER BY bm25(pages_fts) LIMIT 120
                        """,
                        (package_id, expression),
                    ).fetchall()
                else:
                    rows = db.execute(
                        "SELECT id, entry_name, title, docno, revision, '' snippet FROM pages WHERE package_id=? ORDER BY length(body) DESC, title LIMIT 500",
                        (package_id,),
                    ).fetchall()
            self.send_json([dict(row) for row in rows])
            return

        file_match = re.fullmatch(r"/api/packages/(\d+)/files/(.+)", path)
        if file_match:
            package_id = int(file_match.group(1))
            entry_name = safe_archive_name(file_match.group(2))
            with db_connect() as db:
                package = db.execute("SELECT path FROM packages WHERE id=?", (package_id,)).fetchone()
            if not package:
                raise ValueError("Không tìm thấy gói tài liệu.")
            with zipfile.ZipFile(package["path"], "r") as archive:
                try:
                    info = archive.getinfo(entry_name)
                except KeyError:
                    lowered = entry_name.lower()
                    info = next((item for item in archive.infolist() if item.filename.lower() == lowered), None)
                    if info is None:
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                raw = archive.read(info)
            content_type = mimetypes.guess_type(info.filename)[0] or "application/octet-stream"
            if info.filename.lower().endswith((".html", ".htm")):
                raw = rewrite_document_html(raw, package_id)
                content_type = "text/html; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_error_json(ValueError("API không tồn tại."), 404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            value = self.read_json()
            if parsed.path == "/api/sources":
                path = sanitize_path(str(value.get("path", "")))
                source_id = get_or_create_source(path, str(value.get("vendor", "ericsson")))
                result = scan_source(source_id)
                self.send_json({"source_id": source_id, **result}, 201)
                return
            if parsed.path == "/api/scan":
                source_id = int(value.get("source_id", 0))
                result = scan_source(source_id, bool(value.get("force", False)))
                self.send_json(result)
                return
            if parsed.path == "/api/settings":
                save_settings(value)
                self.send_json(public_settings())
                return
            if parsed.path == "/api/settings/test":
                self.send_json(test_ai_connection())
                return
            if parsed.path == "/api/ai/chat":
                question = str(value.get("question", "")).strip()
                if len(question) < 2:
                    raise ValueError("Câu hỏi quá ngắn.")
                package_id = int(value["package_id"]) if value.get("package_id") else None
                self.send_json(call_ai(question, package_id))
                return
            self.send_error_json(ValueError("API không tồn tại."), 404)
        except (ValueError, KeyError, json.JSONDecodeError, sqlite3.Error) as error:
            self.send_error_json(error)
        except Exception as error:
            traceback.print_exc()
            self.send_error_json(error, 500)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        match = re.fullmatch(r"/api/packages/(\d+)", parsed.path)
        if not match:
            self.send_error_json(ValueError("API không tồn tại."), 404)
            return
        package_id = int(match.group(1))
        try:
            with db_connect() as db:
                rows = db.execute("SELECT id FROM pages WHERE package_id=?", (package_id,)).fetchall()
                if rows:
                    db.executemany(
                        "DELETE FROM pages_fts WHERE rowid=?",
                        [(row["id"],) for row in rows],
                    )
                db.execute("DELETE FROM packages WHERE id=?", (package_id,))
            self.send_json({"deleted": package_id, "disk_file_removed": False})
        except sqlite3.Error as error:
            self.send_error_json(error)


def find_static_dir() -> Path:
    for candidate in STATIC_CANDIDATES:
        if (candidate / "index.html").exists():
            return candidate
    return APP_DIR / "public"


def bootstrap_default_sources(max_packages: int | None = None) -> None:
    overrides = {
        "ericsson": os.getenv("DOCATLAS_ERICSSON_PATH") or os.getenv("DOCATLAS_LIBRARY_PATH"),
        "zte": os.getenv("DOCATLAS_ZTE_PATH"),
    }
    for vendor_slug, default_path in DEFAULT_LIBRARIES.items():
        try:
            library_path = sanitize_path(overrides[vendor_slug] or str(default_path))
        except ValueError:
            continue
        source_id = get_or_create_source(library_path, vendor_slug)
        scan_source(source_id, max_packages=max_packages)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Run DocAtlas locally")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-auto-scan", action="store_true")
    parser.add_argument(
        "--sample-one",
        action="store_true",
        help="Index only the smallest package for each vendor for a fast functional check",
    )
    args = parser.parse_args()

    initialize_database()
    global INDEXER
    INDEXER = IndexCoordinator()
    if not args.no_auto_scan:
        bootstrap_default_sources(max_packages=1 if args.sample_one else None)

    static_dir = find_static_dir()
    handler = lambda *handler_args, **handler_kwargs: DocAtlasHandler(  # noqa: E731
        *handler_args, static_dir=static_dir, **handler_kwargs
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"DocAtlas đang chạy tại http://{args.host}:{args.port}")
    print("Thư viện mặc định: " + ", ".join(str(path) for path in DEFAULT_LIBRARIES.values()))
    print(f"Giao diện: {static_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
