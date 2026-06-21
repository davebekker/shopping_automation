#!/usr/bin/env python3
"""NFC shopping-list webhook backed by Google Sheets.

Endpoints:
  GET /health
  GET /t/<tag_id>
  GET /add?item=milk&token=...
  GET /list?token=...
  POST/GET /cleanup?token=...
  POST/GET /admin/setup?token=...

The service is intentionally small and boring: NFC tags should normally point to
/t/<tag_id>, with tag IDs mapped to items in config/shopping_service.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus

from flask import Flask, Response, jsonify, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from zoneinfo import ZoneInfo

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
HEADERS = ["Done", "Item", "Count", "Category", "Added At", "Last Added At", "Source", "Remove After"]
DEFAULT_CONFIG_PATHS = [
    "config/shopping_service.json",
    "data/shopping_service_config.json",
    "shopping_service.json",
]

LOG = logging.getLogger("shopping_service")


def now_iso(tz_name: str) -> str:
    return dt.datetime.now(ZoneInfo(tz_name)).replace(microsecond=0).isoformat()


def parse_iso(value: str) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def row_age_days(row: "SheetRow", tz_name: str) -> Optional[int]:
    parsed = parse_iso(row.added_at)
    if not parsed:
        return None
    now = dt.datetime.now(ZoneInfo(tz_name))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
    return max(0, (now.date() - parsed.astimezone(ZoneInfo(tz_name)).date()).days)


def age_label(days: Optional[int]) -> str:
    if days is None:
        return ""
    if days == 0:
        return "added today"
    if days == 1:
        return "added yesterday"
    return f"added {days} days ago"


def normalize_item(value: str) -> str:
    value = value.strip().lower().replace("-", " ").replace("_", " ")
    value = re.sub(r"\s+", " ", value)
    return value


def display_item(value: str) -> str:
    value = value.strip().replace("-", " ").replace("_", " ")
    value = re.sub(r"\s+", " ", value)
    return value[:1].upper() + value[1:] if value else value


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().upper() in {"TRUE", "YES", "Y", "1", "CHECKED"}


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def parse_quantity(value: Any, default: int = 1) -> int:
    try:
        qty = int(str(value).strip())
    except Exception:
        return default
    return max(1, min(qty, 99))


@dataclass(frozen=True)
class Config:
    spreadsheet_id: str
    sheet_name: str
    service_account_file: str
    host: str
    port: int
    public_base_url: str
    shared_token: str
    timezone: str
    cleanup_checked_after_hours: int
    cleanup_interval_minutes: int
    sqlite_path: str
    max_list_items: int
    age_warning_days: int
    tag_mappings: Dict[str, Dict[str, str]]

    @staticmethod
    def load(path: Optional[str] = None) -> "Config":
        candidates = [path] if path else DEFAULT_CONFIG_PATHS
        loaded_path: Optional[Path] = None
        data: Dict[str, Any] = {}
        for candidate in candidates:
            if not candidate:
                continue
            p = Path(candidate)
            if p.exists():
                loaded_path = p
                data = json.loads(p.read_text(encoding="utf-8"))
                break
        if loaded_path is None:
            raise SystemExit(
                "No shopping service config found. Create config/shopping_service.json "
                "from config/shopping_service.example.json."
            )

        required = ["spreadsheet_id", "service_account_file", "shared_token"]
        missing = [k for k in required if not data.get(k)]
        if missing:
            raise SystemExit(f"Missing required config keys in {loaded_path}: {', '.join(missing)}")

        return Config(
            spreadsheet_id=str(data["spreadsheet_id"]),
            sheet_name=str(data.get("sheet_name", "Shopping")),
            service_account_file=str(data["service_account_file"]),
            host=str(data.get("host", "0.0.0.0")),
            port=int(data.get("port", 8090)),
            public_base_url=str(data.get("public_base_url", "http://localhost:8090")),
            shared_token=str(data["shared_token"]),
            timezone=str(data.get("timezone", "Europe/London")),
            cleanup_checked_after_hours=int(data.get("cleanup_checked_after_hours", 18)),
            cleanup_interval_minutes=int(data.get("cleanup_interval_minutes", 60)),
            sqlite_path=str(data.get("sqlite_path", "data/shopping_service.sqlite")),
            max_list_items=int(data.get("max_list_items", 50)),
            age_warning_days=int(data.get("age_warning_days", 3)),
            tag_mappings=dict(data.get("tags", {})),
        )


@dataclass
class SheetRow:
    row_number: int
    done: bool
    item: str
    count: int
    category: str
    added_at: str
    last_added_at: str
    source: str
    remove_after: str

    @property
    def normalized_item(self) -> str:
        return normalize_item(self.item)


class PendingStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_adds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item TEXT NOT NULL,
                    category TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    item TEXT,
                    category TEXT,
                    source TEXT,
                    created_at TEXT NOT NULL,
                    details TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def audit(self, event_type: str, item: str = "", category: str = "", source: str = "", details: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_events(event_type, item, category, source, created_at, details) VALUES (?, ?, ?, ?, ?, ?)",
                (event_type, item, category, source, dt.datetime.now(dt.timezone.utc).isoformat(), details[:2000]),
            )

    def queue_add(self, item: str, category: str, source: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO pending_adds(item, category, source, created_at, attempts, last_error) VALUES (?, ?, ?, ?, 0, ?)",
                (item, category, source, dt.datetime.now(dt.timezone.utc).isoformat(), error[:2000]),
            )
        self.audit("queued_add", item, category, source, error)

    def pending_count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM pending_adds").fetchone()[0])

    def iter_pending(self, limit: int = 25) -> List[Tuple[int, str, str, str]]:
        with self._connect() as conn:
            return [
                (int(row[0]), str(row[1]), str(row[2]), str(row[3]))
                for row in conn.execute(
                    "SELECT id, item, category, source FROM pending_adds ORDER BY id LIMIT ?", (limit,)
                ).fetchall()
            ]

    def mark_done(self, pending_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM pending_adds WHERE id = ?", (pending_id,))

    def mark_failed(self, pending_id: int, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE pending_adds SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                (error[:2000], pending_id),
            )


class ShoppingSheet:
    def __init__(self, config: Config) -> None:
        self.config = config
        creds = service_account.Credentials.from_service_account_file(
            config.service_account_file, scopes=SCOPES
        )
        self.service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    def _range(self, a1: str) -> str:
        return f"{self.config.sheet_name}!{a1}"

    def _values(self) -> Any:
        return self.service.spreadsheets().values()

    def get_sheet_id(self) -> int:
        meta = self.service.spreadsheets().get(spreadsheetId=self.config.spreadsheet_id).execute()
        for sheet in meta.get("sheets", []):
            props = sheet.get("properties", {})
            if props.get("title") == self.config.sheet_name:
                return int(props["sheetId"])
        raise ValueError(f"Sheet tab not found: {self.config.sheet_name!r}")

    def setup_sheet(self) -> None:
        """Write headers and apply checkbox validation to A2:A1000."""
        self._values().update(
            spreadsheetId=self.config.spreadsheet_id,
            range=self._range("A1:H1"),
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()
        sheet_id = self.get_sheet_id()
        requests = [
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 1000,
                        "startColumnIndex": 0,
                        "endColumnIndex": 1,
                    },
                    "rule": {
                        "condition": {"type": "BOOLEAN"},
                        "showCustomUi": True,
                        "strict": True,
                    },
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 8,
                    },
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            },
        ]
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.config.spreadsheet_id, body={"requests": requests}
        ).execute()

    def _read_values(self, end_row: int = 2000) -> List[List[Any]]:
        result = self._values().get(
            spreadsheetId=self.config.spreadsheet_id,
            range=self._range(f"A2:H{end_row}"),
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        return result.get("values", [])

    def read_rows(self) -> List[SheetRow]:
        values = self._read_values()
        rows: List[SheetRow] = []
        for idx, raw in enumerate(values, start=2):
            padded = list(raw) + [""] * (8 - len(raw))
            # Column B is the source of truth. Pre-created checkboxes in column A
            # should not make a row count as occupied.
            item = str(padded[1]).strip()
            if not item:
                continue
            rows.append(
                SheetRow(
                    row_number=idx,
                    done=parse_bool(padded[0]),
                    item=item,
                    count=parse_int(padded[2], 1),
                    category=str(padded[3]).strip(),
                    added_at=str(padded[4]).strip(),
                    last_added_at=str(padded[5]).strip(),
                    source=str(padded[6]).strip(),
                    remove_after=str(padded[7]).strip(),
                )
            )
        return rows

    def first_empty_item_row(self) -> int:
        values = self._read_values()
        for idx, raw in enumerate(values, start=2):
            padded = list(raw) + [""] * (8 - len(raw))
            if not str(padded[1]).strip():
                return idx
        return max(2, len(values) + 2)

    def update_row(self, row_number: int, values: List[Any]) -> None:
        self._values().update(
            spreadsheetId=self.config.spreadsheet_id,
            range=self._range(f"A{row_number}:H{row_number}"),
            valueInputOption="USER_ENTERED",
            body={"values": [values]},
        ).execute()

    def write_new_row(self, values: List[Any]) -> int:
        row_number = self.first_empty_item_row()
        self.update_row(row_number, values)
        return row_number

    def clear_rows(self, start_row: int = 2, end_row: int = 2000) -> None:
        self._values().clear(
            spreadsheetId=self.config.spreadsheet_id,
            range=self._range(f"A{start_row}:H{end_row}"),
            body={},
        ).execute()

    def delete_rows(self, row_numbers: Iterable[int]) -> int:
        sheet_id = self.get_sheet_id()
        requests = []
        for row_number in sorted(set(row_numbers), reverse=True):
            requests.append(
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": row_number - 1,
                            "endIndex": row_number,
                        }
                    }
                }
            )
        if requests:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.config.spreadsheet_id, body={"requests": requests}
            ).execute()
        return len(requests)

    def add_item(self, item: str, category: str = "", source: str = "web", quantity: int = 1) -> Tuple[str, SheetRow | None]:
        item_display = display_item(item)
        item_norm = normalize_item(item_display)
        quantity = parse_quantity(quantity, 1)
        if not item_norm:
            raise ValueError("Item is empty")
        timestamp = now_iso(self.config.timezone)
        rows = self.read_rows()
        for row in rows:
            if row.normalized_item == item_norm:
                new_count = max(row.count, 0) + quantity
                new_category = category or row.category
                values = [False, row.item, new_count, new_category, row.added_at or timestamp, timestamp, row.source or source, ""]
                self.update_row(row.row_number, values)
                updated = SheetRow(row.row_number, False, row.item, new_count, new_category, row.added_at or timestamp, timestamp, row.source or source, "")
                return ("reactivated" if row.done else "updated", updated)
        values = [False, item_display, quantity, category, timestamp, timestamp, source, ""]
        row_number = self.write_new_row(values)
        return "added", SheetRow(row_number, False, item_display, quantity, category, timestamp, timestamp, source, "")

    def migrate_legacy_layout(self) -> Dict[str, int]:
        """Migrate rows from the original layout to the mobile-friendly layout.

        Original layout:
          A Done, B Item, C Category, D Added At, E Source, F Count, G Last Added At, H Remove After

        New layout:
          A Done, B Item, C Count, D Category, E Added At, F Last Added At, G Source, H Remove After
        """
        old_values = self._read_values()
        new_values = []
        migrated = 0
        for raw in old_values:
            padded = list(raw) + [""] * (8 - len(raw))
            item = str(padded[1]).strip()
            if not item:
                continue
            # If column C already looks numeric and column F does not, this row may
            # already be in the new layout. Preserve it as-is.
            col_c = str(padded[2]).strip()
            col_f = str(padded[5]).strip()
            c_is_int = col_c.isdigit()
            f_is_int = col_f.isdigit()
            if c_is_int and not f_is_int:
                new_values.append(padded[:8])
            else:
                new_values.append([
                    parse_bool(padded[0]),
                    item,
                    parse_int(padded[5], 1),
                    str(padded[2]).strip(),
                    str(padded[3]).strip(),
                    str(padded[6]).strip(),
                    str(padded[4]).strip(),
                    str(padded[7]).strip(),
                ])
            migrated += 1
        self.clear_rows()
        if new_values:
            self._values().update(
                spreadsheetId=self.config.spreadsheet_id,
                range=self._range(f"A2:H{len(new_values) + 1}"),
                valueInputOption="USER_ENTERED",
                body={"values": new_values},
            ).execute()
        return {"migrated": migrated}

    def compact(self) -> Dict[str, int]:
        rows = self.read_rows()
        merged: Dict[str, SheetRow] = {}
        order: List[str] = []
        for row in rows:
            key = row.normalized_item
            if not key:
                continue
            if key not in merged:
                merged[key] = row
                order.append(key)
            else:
                current = merged[key]
                merged[key] = SheetRow(
                    row_number=current.row_number,
                    done=current.done and row.done,
                    item=current.item,
                    count=max(current.count, 0) + max(row.count, 0),
                    category=current.category or row.category,
                    added_at=current.added_at or row.added_at,
                    last_added_at=max(current.last_added_at, row.last_added_at),
                    source=current.source or row.source,
                    remove_after="" if not (current.done and row.done) else (current.remove_after or row.remove_after),
                )
        compact_values = []
        for key in order:
            r = merged[key]
            compact_values.append([r.done, r.item, r.count, r.category, r.added_at, r.last_added_at, r.source, r.remove_after])
        self.clear_rows()
        if compact_values:
            self._values().update(
                spreadsheetId=self.config.spreadsheet_id,
                range=self._range(f"A2:H{len(compact_values) + 1}"),
                valueInputOption="USER_ENTERED",
                body={"values": compact_values},
            ).execute()
        return {"before": len(rows), "after": len(compact_values), "merged": max(0, len(rows) - len(compact_values))}

    def cleanup(self) -> Dict[str, int]:
        now = dt.datetime.now(ZoneInfo(self.config.timezone)).replace(microsecond=0)
        remove_after_dt = now + dt.timedelta(hours=self.config.cleanup_checked_after_hours)
        marked = 0
        to_delete: List[int] = []
        for row in self.read_rows():
            if not row.done:
                continue
            if row.remove_after:
                parsed = parse_iso(row.remove_after)
                if parsed and parsed <= now:
                    to_delete.append(row.row_number)
            else:
                self.update_row(
                    row.row_number,
                    [True, row.item, row.count, row.category, row.added_at, row.last_added_at, row.source, remove_after_dt.isoformat()],
                )
                marked += 1
        deleted = self.delete_rows(to_delete)
        return {"marked": marked, "deleted": deleted}

    def current_items(self, include_done: bool = False) -> List[SheetRow]:
        rows = self.read_rows()
        if not include_done:
            rows = [r for r in rows if not r.done]
        return rows


class ShoppingService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.sheet = ShoppingSheet(config)
        self.store = PendingStore(config.sqlite_path)
        self._last_cleanup_attempt = 0.0

    def flush_pending(self, limit: int = 25) -> int:
        flushed = 0
        for pending_id, item, category, source in self.store.iter_pending(limit):
            try:
                self.sheet.add_item(item, category, source=f"queued:{source}")
            except Exception as exc:  # keep queue if Google is unavailable
                self.store.mark_failed(pending_id, str(exc))
                LOG.warning("Pending item still failed: %s (%s)", item, exc)
                continue
            self.store.mark_done(pending_id)
            self.store.audit("flushed_add", item, category, source)
            flushed += 1
        return flushed

    def maybe_cleanup(self) -> None:
        if self.config.cleanup_interval_minutes <= 0:
            return
        now = time.time()
        if now - self._last_cleanup_attempt < self.config.cleanup_interval_minutes * 60:
            return
        self._last_cleanup_attempt = now
        try:
            result = self.sheet.cleanup()
            if result["marked"] or result["deleted"]:
                LOG.info("Cleanup result: %s", result)
        except Exception as exc:
            LOG.warning("Cleanup skipped/failed: %s", exc)

    def add_item(self, item: str, category: str, source: str, quantity: int = 1) -> Tuple[bool, str, Optional[str]]:
        quantity = parse_quantity(quantity, 1)
        try:
            self.flush_pending(limit=10)
            status, _ = self.sheet.add_item(item, category, source, quantity=quantity)
            self.store.audit(status, item, category, source, details=f"quantity={quantity}")
            self.maybe_cleanup()
            return True, status, None
        except Exception as exc:
            LOG.exception("Failed to add item; queueing locally")
            self.store.queue_add(item, category, source, str(exc))
            return False, "queued", str(exc)


def wants_json() -> bool:
    return "application/json" in request.headers.get("Accept", "") or request.args.get("format") == "json"


def html_page(title: str, body: str, status: int = 200) -> Response:
    document = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; line-height: 1.4; }}
    .card {{ max-width: 36rem; padding: 1.25rem; border: 1px solid #ddd; border-radius: 12px; }}
    ul {{ padding-left: 1.2rem; }}
    code {{ background: #f5f5f5; padding: 0.1rem 0.25rem; border-radius: 4px; }}
    label {{ display: block; margin: 0.8rem 0 0.25rem; font-weight: 600; }}
    input, select, button {{ font: inherit; padding: 0.7rem; width: 100%; box-sizing: border-box; border: 1px solid #ccc; border-radius: 8px; }}
    button {{ margin-top: 1rem; border: 0; background: #111; color: white; font-weight: 700; }}
  </style>
</head>
<body><main class=\"card\">{body}</main></body>
</html>"""
    return Response(document, status=status, mimetype="text/html")


def create_app(config: Config) -> Flask:
    app = Flask(__name__)
    service = ShoppingService(config)

    def require_token() -> Optional[Response]:
        token = request.values.get("token") or request.headers.get("X-Shopping-Token", "")
        if token != config.shared_token:
            if wants_json():
                return jsonify({"ok": False, "error": "unauthorized"}), 401
            return html_page("Unauthorized", "<h1>Unauthorized</h1><p>Bad or missing token.</p>", 401)
        return None

    @app.get("/health")
    def health() -> Response:
        return jsonify({"ok": True, "pending": service.store.pending_count()})

    @app.route("/t/<tag_id>", methods=["GET", "POST"])
    def tag_add(tag_id: str) -> Response:
        mapping = config.tag_mappings.get(tag_id)
        if not mapping:
            LOG.warning("Unknown tag scanned: %s", tag_id)
            return html_page("Unknown tag", f"<h1>Unknown tag</h1><p>Tag <code>{html.escape(tag_id)}</code> is not configured.</p>", 404)
        item = mapping.get("item", "")
        category = mapping.get("category", "")
        quantity = parse_quantity(mapping.get("quantity", 1), 1)
        ok, status, error = service.add_item(item, category, f"tag:{tag_id}", quantity=quantity)
        pending = service.store.pending_count()
        if wants_json():
            return jsonify({"ok": ok, "status": status, "item": item, "category": category, "pending": pending, "error": error})
        safe_item = html.escape(display_item(item))
        if ok:
            return html_page("Added", f"<h1>✅ Added {safe_item}</h1><p>Status: {html.escape(status)}.</p><p>Pending queued items: {pending}</p>")
        return html_page("Queued", f"<h1>🟡 Queued {safe_item}</h1><p>Google Sheets was unavailable, so this item was saved locally and will retry later.</p><p>Pending queued items: {pending}</p>", 202)

    @app.route("/add", methods=["GET", "POST"])
    def add() -> Response:
        auth = require_token()
        if auth:
            return auth
        item = (request.values.get("item") or "").strip()
        category = (request.values.get("category") or "").strip()
        source = (request.values.get("source") or "manual").strip()[:80]
        quantity = parse_quantity(request.values.get("amount") or request.values.get("count") or request.values.get("quantity") or 1, 1)
        if not item:
            return html_page("Missing item", "<h1>Missing item</h1><p>Use <code>/add?item=milk&amp;token=...</code>.</p>", 400)
        ok, status, error = service.add_item(item, category, source, quantity=quantity)
        pending = service.store.pending_count()
        if wants_json():
            return jsonify({"ok": ok, "status": status, "item": item, "category": category, "pending": pending, "error": error})
        safe_item = html.escape(display_item(item))
        if ok:
            return html_page("Added", f"<h1>✅ Added {safe_item}</h1><p>Status: {html.escape(status)}.</p><p>Pending queued items: {pending}</p>")
        return html_page("Queued", f"<h1>🟡 Queued {safe_item}</h1><p>Google Sheets was unavailable, so this item was saved locally and will retry later.</p><p>Pending queued items: {pending}</p>", 202)


    @app.route("/form", methods=["GET", "POST"])
    def generic_form() -> Response:
        auth = require_token()
        if auth:
            return auth
        if request.method == "POST":
            item = (request.form.get("item") or "").strip()
            category = (request.form.get("category") or "").strip()
            quantity = parse_quantity(request.form.get("amount") or 1, 1)
            if not item:
                return html_page("Missing item", "<h1>Missing item</h1><p>Please enter an item.</p>", 400)
            ok, status, error = service.add_item(item, category, "generic-form", quantity=quantity)
            pending = service.store.pending_count()
            safe_item = html.escape(display_item(item))
            if ok:
                return html_page("Added", f"<h1>✅ Added {safe_item}</h1><p>Amount: {quantity}</p><p>Status: {html.escape(status)}.</p><p><a href=\"/form?token={html.escape(config.shared_token)}\">Add another item</a></p><p>Pending queued items: {pending}</p>")
            return html_page("Queued", f"<h1>🟡 Queued {safe_item}</h1><p>Google Sheets was unavailable, so this item was saved locally and will retry later.</p><p><a href=\"/form?token={html.escape(config.shared_token)}\">Add another item</a></p><p>Pending queued items: {pending}</p><p>{html.escape(error or '')}</p>", 202)

        token = html.escape(config.shared_token)
        body = f"""
        <h1>🛒 Add shopping item</h1>
        <form method=\"post\" action=\"/form?token={token}\">
          <input type=\"hidden\" name=\"token\" value=\"{token}\">
          <label for=\"item\">Item</label>
          <input id=\"item\" name=\"item\" autocomplete=\"off\" autofocus required placeholder=\"e.g. Milk\">
          <label for=\"amount\">Amount</label>
          <input id=\"amount\" name=\"amount\" type=\"number\" min=\"1\" max=\"99\" value=\"1\">
          <label for=\"category\">Category</label>
          <input id=\"category\" name=\"category\" placeholder=\"e.g. General\">
          <button type=\"submit\">Add to shopping list</button>
        </form>
        """
        return html_page("Add shopping item", body)

    @app.get("/list")
    def list_items() -> Response:
        auth = require_token()
        if auth:
            return auth
        try:
            service.flush_pending(limit=25)
            rows = service.sheet.current_items(include_done=False)[: config.max_list_items]
        except Exception as exc:
            return html_page("List unavailable", f"<h1>List unavailable</h1><p>{html.escape(str(exc))}</p>", 503)
        enriched = []
        for r in rows:
            days = row_age_days(r, config.timezone)
            row_dict = dict(r.__dict__)
            row_dict["age_days"] = days
            row_dict["age_label"] = age_label(days)
            row_dict["urgent"] = days is not None and days >= config.age_warning_days
            enriched.append(row_dict)
        if wants_json():
            return jsonify({"ok": True, "items": enriched, "pending": service.store.pending_count()})
        item_bits = []
        for r, e in zip(rows, enriched):
            qty = f" × {r.count}" if r.count > 1 else ""
            age = f" <small>({html.escape(e['age_label'])})</small>" if e["urgent"] else ""
            item_bits.append(f"<li>{html.escape(r.item)}{qty}{age}</li>")
        items = "".join(item_bits)
        if not items:
            items = "<li>No current shopping items.</li>"
        return html_page("Shopping list", f"<h1>🛒 Shopping list</h1><ul>{items}</ul><p>Pending queued items: {service.store.pending_count()}</p>")

    @app.route("/cleanup", methods=["GET", "POST"])
    def cleanup() -> Response:
        auth = require_token()
        if auth:
            return auth
        result = service.sheet.cleanup()
        service.store.audit("cleanup", details=json.dumps(result))
        if wants_json():
            return jsonify({"ok": True, **result})
        return html_page("Cleanup", f"<h1>🧹 Cleanup complete</h1><p>Marked: {result['marked']}</p><p>Deleted: {result['deleted']}</p>")

    @app.route("/flush", methods=["GET", "POST"])
    def flush() -> Response:
        auth = require_token()
        if auth:
            return auth
        flushed = service.flush_pending(limit=100)
        if wants_json():
            return jsonify({"ok": True, "flushed": flushed, "pending": service.store.pending_count()})
        return html_page("Flush", f"<h1>🔁 Queue flush complete</h1><p>Flushed: {flushed}</p><p>Pending: {service.store.pending_count()}</p>")


    @app.route("/admin/compact", methods=["GET", "POST"])
    def compact() -> Response:
        auth = require_token()
        if auth:
            return auth
        result = service.sheet.compact()
        service.store.audit("compact", details=json.dumps(result))
        if wants_json():
            return jsonify({"ok": True, **result})
        return html_page("Compact", f"<h1>🧽 Sheet compacted</h1><p>Before: {result['before']}</p><p>After: {result['after']}</p><p>Merged: {result['merged']}</p>")

    @app.route("/admin/migrate-layout", methods=["GET", "POST"])
    def migrate_layout() -> Response:
        auth = require_token()
        if auth:
            return auth
        result = service.sheet.migrate_legacy_layout()
        service.sheet.setup_sheet()
        service.store.audit("migrate_layout", details=json.dumps(result))
        if wants_json():
            return jsonify({"ok": True, **result})
        return html_page("Layout migrated", f"<h1>✅ Sheet layout migrated</h1><p>Migrated rows: {result['migrated']}</p>")

    @app.route("/admin/setup", methods=["GET", "POST"])
    def setup() -> Response:
        auth = require_token()
        if auth:
            return auth
        service.sheet.setup_sheet()
        if wants_json():
            return jsonify({"ok": True, "message": "sheet headers and checkbox validation applied"})
        return html_page("Setup", "<h1>✅ Sheet setup complete</h1><p>Headers and checkbox validation were applied.</p>")

    @app.get("/")
    def index() -> Response:
        body = f"""
        <h1>🛒 Shopping service</h1>
        <p>Use NFC tags like <code>{html.escape(config.public_base_url)}/t/&lt;tag_id&gt;</code>.</p>
        <p>Generic add form: <code>/form?token=...</code></p>
        <p>Manual add: <code>/add?item=milk&amp;token=...</code></p>
        <p>Health: <code>/health</code></p>
        """
        return html_page("Shopping service", body)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="NFC shopping list webhook backed by Google Sheets")
    parser.add_argument("--config", help="Path to config JSON")
    parser.add_argument("--setup-sheet", action="store_true", help="Apply sheet headers and checkbox validation, then exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    config = Config.load(args.config)

    if args.setup_sheet:
        ShoppingSheet(config).setup_sheet()
        print("Sheet setup complete")
        return

    app = create_app(config)
    app.run(host=config.host, port=config.port)


if __name__ == "__main__":
    main()
