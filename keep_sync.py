#!/usr/bin/env python3
"""Two-way Google Keep checklist sync for shopping_service.py.

Google Sheets remains the source of truth. A single pinned Google Keep checklist
note (e.g. '🛒 Shopping') is kept in sync so items can be viewed and ticked off
on a Pixel Watch / Wear OS device or shared Keep app.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Tuple

import gkeepapi

if TYPE_CHECKING:
    from shopping_service import Config, SheetRow, ShoppingSheet

LOG = logging.getLogger("shopping_keep_sync")

DEFAULT_ENV_PATHS = (
    Path("/home/dave-bekker/Projects/signal_automation/.env"),
    Path("../signal_automation/.env"),
    Path(".env"),
)


def load_dotenv_fallback(paths: Iterable[Path] = DEFAULT_ENV_PATHS) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for path in paths:
        try:
            if not path.exists():
                continue
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                values.setdefault(k.strip(), v.strip().strip("\"'"))
        except Exception as exc:
            LOG.debug("Could not read env file %s: %s", path, exc)
    return values


def format_keep_item_text(item: str, count: int = 1) -> str:
    clean = " ".join(item.strip().replace("_", " ").split())
    if clean:
        clean = clean[:1].upper() + clean[1:]
    if count > 1:
        return f"{clean} ×{count}"
    return clean


def parse_keep_item_text(raw_text: str) -> Tuple[str, int]:
    text = " ".join((raw_text or "").strip().split())
    if not text:
        return "", 1

    # Match trailing '×2', 'x2', '*2', '(2)'
    m = re.match(r"^(.+?)\s*(?:[×x*]|\()\s*(\d{1,2})\)?$", text, flags=re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        qty = max(1, min(99, int(m.group(2))))
        return name, qty

    # Match leading '2 milk' or '2x milk'
    m = re.match(r"^(\d{1,2})[x×*]?\s+(.+)$", text, flags=re.IGNORECASE)
    if m:
        qty = max(1, min(99, int(m.group(1))))
        name = m.group(2).strip()
        return name, qty

    return text, 1


class KeepSyncManager:
    def __init__(
        self,
        config: "Config",
        sheet: "ShoppingSheet",
        notify_callback: Callable[[str], None],
        normalize_fn: Callable[[str], str],
        display_fn: Callable[[str], str],
    ) -> None:
        self.config = config
        self.sheet = sheet
        self.notify_signal = notify_callback
        self.normalize_item = normalize_fn
        self.display_item = display_fn

        self.db_path = Path(config.sqlite_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.db_path.parent / "keep_cache.json"

        self._lock = threading.Lock()
        self._keep: Optional[gkeepapi.Keep] = None
        self._authenticated = False
        self._wake_event = threading.Event()
        self._clear_checked_requested = False

        self._init_db()

    @property
    def enabled(self) -> bool:
        return bool(
            self.config.keep_enabled
            and self.config.keep_username
            and self.config.keep_master_token
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS keep_item_state (
                    normalized_item TEXT PRIMARY KEY,
                    display_item TEXT NOT NULL,
                    last_sheet_done INTEGER NOT NULL,
                    last_sheet_count INTEGER NOT NULL,
                    last_sheet_added_at TEXT NOT NULL,
                    last_keep_checked INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS keep_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def _get_meta(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM keep_meta WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else default

    def _set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO keep_meta(key, value) VALUES (?, ?)",
                (key, value),
            )

    def get_collaborators(self) -> List[str]:
        extra_raw = self._get_meta("collaborators", "[]")
        try:
            extra = json.loads(extra_raw)
            if not isinstance(extra, list):
                extra = []
        except Exception:
            extra = []
        merged: List[str] = []
        seen: set[str] = set()
        for email in list(self.config.keep_collaborators) + [str(x) for x in extra]:
            clean = email.strip().lower()
            if clean and "@" in clean and clean not in seen and clean != self.config.keep_username.strip().lower():
                seen.add(clean)
                merged.append(clean)
        return merged

    def add_collaborator(self, email: str) -> Tuple[bool, str]:
        clean = (email or "").strip().lower()
        if not clean or "@" not in clean or "." not in clean.split("@", 1)[-1]:
            return False, "Please provide a valid email address."
        current = self.get_collaborators()
        if clean not in current:
            current.append(clean)
            self._set_meta("collaborators", json.dumps(current))
        result = self.sync_now()
        if not result.get("ok"):
            return False, f"Saved {clean}, but Keep sync failed: {result.get('error')}"
        return True, f"Shared Keep note '{self.config.keep_note_title}' with {clean}."

    def _load_item_states(self) -> Dict[str, Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM keep_item_state").fetchall()
            return {
                str(r["normalized_item"]): {
                    "normalized_item": str(r["normalized_item"]),
                    "display_item": str(r["display_item"]),
                    "last_sheet_done": bool(r["last_sheet_done"]),
                    "last_sheet_count": int(r["last_sheet_count"]),
                    "last_sheet_added_at": str(r["last_sheet_added_at"]),
                    "last_keep_checked": bool(r["last_keep_checked"]),
                }
                for r in rows
            }

    def _save_item_states(self, states: Dict[str, Dict[str, Any]]) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM keep_item_state")
            for norm, st in states.items():
                conn.execute(
                    """
                    INSERT INTO keep_item_state(
                        normalized_item, display_item, last_sheet_done,
                        last_sheet_count, last_sheet_added_at, last_keep_checked, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        norm,
                        st["display_item"],
                        1 if st["last_sheet_done"] else 0,
                        int(st["last_sheet_count"]),
                        str(st["last_sheet_added_at"]),
                        1 if st["last_keep_checked"] else 0,
                        now,
                    ),
                )

    def _ensure_keep_synced(self) -> gkeepapi.Keep:
        if self._keep is None:
            self._keep = gkeepapi.Keep()
        if not self._authenticated:
            cached_state = None
            if self.cache_path.exists():
                try:
                    cached_state = json.loads(self.cache_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    LOG.warning("Ignoring corrupt Keep cache %s: %s", self.cache_path, exc)
                    cached_state = None
            try:
                self._keep.authenticate(
                    self.config.keep_username,
                    self.config.keep_master_token,
                    state=cached_state,
                )
            except Exception:
                if cached_state is not None:
                    LOG.info("Retrying Keep authentication without cached state")
                    self._keep = gkeepapi.Keep()
                    self._keep.authenticate(
                        self.config.keep_username,
                        self.config.keep_master_token,
                        state=None,
                    )
                else:
                    raise
            self._authenticated = True
        else:
            self._keep.sync()
        return self._keep

    def _save_keep_cache(self, keep: gkeepapi.Keep) -> None:
        try:
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(keep.dump()), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except Exception as exc:
            LOG.debug("Could not write Keep state cache: %s", exc)

    def _get_or_create_shopping_list(self, keep: gkeepapi.Keep) -> Tuple[gkeepapi.node.List, bool]:
        dirty = False
        saved_id = self.config.keep_note_id or self._get_meta("note_id", "")
        target_note: Optional[gkeepapi.node.List] = None

        if saved_id:
            candidate = keep.get(saved_id)
            if isinstance(candidate, gkeepapi.node.List) and not candidate.trashed:
                target_note = candidate

        if target_note is None:
            wanted_title = self.config.keep_note_title.strip().lower()
            for node in keep.all():
                if isinstance(node, gkeepapi.node.List) and not node.trashed:
                    title_lower = (node.title or "").strip().lower()
                    if title_lower == wanted_title or title_lower in {"shopping", "shopping list", "🛒 shopping", "🛒 shopping list"}:
                        target_note = node
                        break

        if target_note is None:
            LOG.info("Creating new Google Keep checklist note %r", self.config.keep_note_title)
            target_note = keep.createList(self.config.keep_note_title, [])
            target_note.pinned = True
            dirty = True

        if target_note.archived:
            target_note.archived = False
            dirty = True
        if not target_note.pinned:
            target_note.pinned = True
            dirty = True
        if (target_note.title or "").strip() != self.config.keep_note_title.strip():
            target_note.title = self.config.keep_note_title
            dirty = True

        existing_collabs = {str(c).strip().lower() for c in target_note.collaborators.all()}
        for email in self.get_collaborators():
            if email.lower() not in existing_collabs:
                LOG.info("Sharing Google Keep shopping note with collaborator: %s", email)
                target_note.collaborators.add(email)
                dirty = True

        if self._get_meta("note_id", "") != str(target_note.id):
            self._set_meta("note_id", str(target_note.id))
        return target_note, dirty

    def sync_now(
        self,
        clear_checked_in_keep: bool = False,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "Google Keep sync is disabled or missing credentials"}

        with self._lock:
            try:
                was_authenticated = self._authenticated
                keep = self._ensure_keep_synced()
                note, keep_dirty = self._get_or_create_shopping_list(keep)
                prev_states = self._load_item_states()

                sheet_rows = self.sheet.read_rows()
                sheet_by_norm: Dict[str, "SheetRow"] = {}
                for row in sheet_rows:
                    norm = row.normalized_item
                    if norm:
                        sheet_by_norm[norm] = row

                # Group Keep items by normalized item name and remove accidental duplicates
                keep_by_norm: Dict[str, Tuple[gkeepapi.node.ListItem, str, int]] = {}
                for li in list(note.items):
                    parsed_name, parsed_qty = parse_keep_item_text(li.text)
                    norm = self.normalize_item(parsed_name)
                    if not norm:
                        li.delete()
                        keep_dirty = True
                        continue
                    if norm in keep_by_norm:
                        existing_li, existing_name, existing_qty = keep_by_norm[norm]
                        # Prefer unchecked if either is unchecked
                        if existing_li.checked and not li.checked:
                            existing_li. checked = False
                        merged_qty = max(existing_qty, parsed_qty)
                        wanted_text = format_keep_item_text(existing_name, merged_qty)
                        if existing_li.text != wanted_text:
                            existing_li.text = wanted_text
                        li.delete()
                        keep_by_norm[norm] = (existing_li, existing_name, merged_qty)
                        keep_dirty = True
                    else:
                        keep_by_norm[norm] = (li, parsed_name, parsed_qty)

                newly_ticked_labels: List[str] = []
                newly_unticked_norms: List[str] = []
                newly_added_from_keep: List[str] = []
                pushed_to_keep: List[str] = []
                norms_to_mark_done_in_sheet: List[str] = []

                all_norms = sorted(set(sheet_by_norm.keys()) | set(keep_by_norm.keys()))

                for norm in all_norms:
                    row = sheet_by_norm.get(norm)
                    keep_entry = keep_by_norm.get(norm)
                    prev = prev_states.get(norm)

                    if row is not None and keep_entry is not None:
                        li, parsed_name, parsed_qty = keep_entry
                        wanted_text = format_keep_item_text(row.item, row.count)

                        sheet_readded_or_changed = (
                            prev is None
                            or (not row.done and prev["last_sheet_done"])
                            or (row.last_added_at != prev["last_sheet_added_at"])
                            or (row.count != prev["last_sheet_count"])
                        )
                        sheet_marked_done = row.done and (prev is None or not prev["last_sheet_done"])
                        keep_just_checked = li.checked and (prev is not None and not prev["last_keep_checked"])
                        keep_just_unchecked = (not li.checked) and (prev is not None and prev["last_keep_checked"])

                        if not row.done and sheet_readded_or_changed and not keep_just_checked:
                            # Sheet added/updated/reopened this item -> ensure Keep is unchecked and up to date
                            if li.checked:
                                li.checked = False
                                keep_dirty = True
                            if li.text != wanted_text:
                                li.text = wanted_text
                                keep_dirty = True
                                pushed_to_keep.append(wanted_text)
                        elif not row.done and li.checked:
                            # Ticked off on watch / in Keep -> mark Done in Google Sheets!
                            norms_to_mark_done_in_sheet.append(norm)
                            newly_ticked_labels.append(wanted_text)
                            row.done = True
                            if clear_checked_in_keep:
                                li.delete()
                                keep_by_norm.pop(norm, None)
                                keep_dirty = True
                        elif row.done and not li.checked:
                            if keep_just_unchecked and not sheet_marked_done:
                                # Unticked on watch -> reopen in Google Sheets!
                                self.sheet.set_item_count(row.item, row.count, row.category, source="keep-untick")
                                row.done = False
                                newly_unticked_norms.append(row.item)
                            else:
                                # Marked done in Google Sheets -> mark checked in Keep
                                if clear_checked_in_keep:
                                    li.delete()
                                    keep_by_norm.pop(norm, None)
                                else:
                                    li.checked = True
                                keep_dirty = True
                        else:
                            # Both agree on done/checked status
                            if not row.done and li.text != wanted_text:
                                li.text = wanted_text
                                keep_dirty = True
                            if li.checked and clear_checked_in_keep:
                                li.delete()
                                keep_by_norm.pop(norm, None)
                                keep_dirty = True

                    elif row is not None and keep_entry is None:
                        if not row.done:
                            # Active in Sheets, missing in Keep -> add to Keep checklist
                            wanted_text = format_keep_item_text(row.item, row.count)
                            new_li = note.add(
                                wanted_text,
                                False,
                                gkeepapi.node.NewListItemPlacementValue.Bottom,
                            )
                            keep_by_norm[norm] = (new_li, row.item, row.count)
                            pushed_to_keep.append(wanted_text)
                            keep_dirty = True

                    elif row is None and keep_entry is not None:
                        li, parsed_name, parsed_qty = keep_entry
                        if not li.checked:
                            if prev is not None and not prev["last_sheet_done"]:
                                # Was previously an active Sheet item, and row was deleted from Sheet -> remove from Keep
                                li.delete()
                                keep_by_norm.pop(norm, None)
                                keep_dirty = True
                            else:
                                # Added directly in Google Keep (e.g. voice on watch or typed in Keep) -> add to Sheet!
                                status, new_row = self.sheet.add_item(
                                    parsed_name,
                                    category="",
                                    source="keep",
                                    quantity=parsed_qty,
                                )
                                try:
                                    self.sheet.append_event("add", parsed_name, parsed_qty, "", "keep", status)
                                    self.sheet.maybe_track_manual_item(parsed_name, "", "keep")
                                except Exception:
                                    pass
                                if new_row is not None:
                                    sheet_by_norm[norm] = new_row
                                    wanted_text = format_keep_item_text(new_row.item, new_row.count)
                                    if li.text != wanted_text:
                                        li.text = wanted_text
                                        keep_dirty = True
                                    newly_added_from_keep.append(wanted_text)
                        else:
                            # Checked in Keep and no longer in Sheets (e.g. cleaned up after grace period) -> delete from Keep
                            li.delete()
                            keep_by_norm.pop(norm, None)
                            keep_dirty = True

                if norms_to_mark_done_in_sheet:
                    self.sheet.mark_items_done(norms_to_mark_done_in_sheet)
                    for norm in norms_to_mark_done_in_sheet:
                        r = sheet_by_norm.get(norm)
                        if r:
                            try:
                                self.sheet.append_event("done", r.item, r.count, r.category, "keep", "checked_in_keep")
                            except Exception:
                                pass

                if keep_dirty:
                    keep.sync()

                # Build updated state snapshot and only write to disk if state actually changed
                new_states: Dict[str, Dict[str, Any]] = {}
                for norm in set(sheet_by_norm.keys()) | set(keep_by_norm.keys()):
                    r = sheet_by_norm.get(norm)
                    k_entry = keep_by_norm.get(norm)
                    if r is None and k_entry is None:
                        continue
                    display = r.item if r is not None else self.display_item(k_entry[1])
                    sheet_done = r.done if r is not None else True
                    sheet_count = r.count if r is not None else k_entry[2]
                    sheet_added = r.last_added_at if r is not None else ""
                    keep_checked = k_entry[0].checked if k_entry is not None else sheet_done
                    new_states[norm] = {
                        "normalized_item": norm,
                        "display_item": display,
                        "last_sheet_done": sheet_done,
                        "last_sheet_count": sheet_count,
                        "last_sheet_added_at": sheet_added,
                        "last_keep_checked": keep_checked,
                    }

                states_changed = (new_states != prev_states)
                any_changes = bool(
                    not was_authenticated
                    or keep_dirty
                    or states_changed
                    or newly_ticked_labels
                    or newly_unticked_norms
                    or newly_added_from_keep
                    or pushed_to_keep
                )

                if any_changes:
                    self._save_keep_cache(keep)
                if states_changed:
                    self._save_item_states(new_states)

                if newly_added_from_keep:
                    added_str = ", ".join(newly_added_from_keep)
                    self.notify_signal(f"📝 Keep note added: {added_str}")

                active_remaining = [r for r in sheet_by_norm.values() if not r.done]
                now_str = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                if any_changes or self._get_meta("last_sync_error", ""):
                    self._set_meta("last_sync_at", now_str)
                    self._set_meta("last_sync_error", "")

                return {
                    "ok": True,
                    "note_id": str(note.id),
                    "note_title": note.title,
                    "collaborators": self.get_collaborators(),
                    "active_items": len(active_remaining),
                    "pushed_to_keep": pushed_to_keep,
                    "ticked_in_keep": newly_ticked_labels,
                    "added_from_keep": newly_added_from_keep,
                    "unticked_in_keep": newly_unticked_norms,
                    "last_sync_at": now_str,
                }
            except Exception as exc:
                LOG.exception("Google Keep sync error")
                self._authenticated = False
                self._set_meta("last_sync_error", str(exc))
                return {"ok": False, "error": str(exc)}

    def trigger_sync(self, clear_checked: bool = False) -> None:
        if not self.enabled:
            return
        if clear_checked:
            self._clear_checked_requested = True
        self._wake_event.set()

    def start_background_loop(self) -> None:
        if not self.enabled:
            LOG.info("Google Keep background sync disabled (missing credentials or disabled in config)")
            return
        thread = threading.Thread(
            target=self._background_loop,
            name="shopping-keep-sync",
            daemon=True,
        )
        thread.start()
        LOG.info(
            "Google Keep background sync started (note=%r, interval=%ss, collaborators=%s)",
            self.config.keep_note_title,
            self.config.keep_sync_interval_seconds,
            self.get_collaborators(),
        )

    def _background_loop(self) -> None:
        # Initial sync shortly after startup
        time.sleep(2)
        while True:
            clear_checked = self._clear_checked_requested
            self._clear_checked_requested = False
            self._wake_event.clear()

            try:
                self.sync_now(
                    clear_checked_in_keep=clear_checked,
                )
            except Exception:
                LOG.exception("Unexpected error in Keep background sync loop")

            self._wake_event.wait(timeout=max(15, self.config.keep_sync_interval_seconds))
