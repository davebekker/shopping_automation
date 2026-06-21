#!/usr/bin/env python3
"""Signal group bridge for the Shopping Google Sheets service.

This service listens to the Signal REST API receive websocket, filters messages
from one configured Signal group, forwards shopping commands to the local
shopping_service.py HTTP API, and can send a once-daily morning warning when
shopping items have been on the list for several days.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import quote

import aiohttp

LOG = logging.getLogger("shopping_signal_bridge")


@dataclass(frozen=True)
class BridgeConfig:
    signal_api_base: str
    signal_number: str
    shopping_service_base: str
    shopping_token: str
    group_internal_id: str
    group_recipient: str
    form_url_base: str
    allow_plain_adds: bool
    plain_add_min_words: int
    max_list_items: int
    proactive_age_alerts_enabled: bool
    age_alert_hour: int
    age_alert_minute: int
    age_alert_min_days: int
    age_alert_repeat_hours: int
    age_alert_max_items: int
    state_file: Path

    @property
    def ws_url(self) -> str:
        return f"{self.signal_api_base}/v1/receive/{self.signal_number}"

    @property
    def send_url(self) -> str:
        return f"{self.signal_api_base}/v2/send"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def load_config(path: str | Path) -> BridgeConfig:
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    def get(name: str, default: Any = None) -> Any:
        env_name = f"SHOPPING_SIGNAL_{name.upper()}"
        return os.getenv(env_name, raw.get(name, default))

    required = [
        "signal_number",
        "shopping_token",
        "group_internal_id",
        "group_recipient",
    ]
    missing = [name for name in required if not get(name)]
    if missing:
        raise SystemExit(f"Missing required config value(s): {', '.join(missing)}")

    service_base = str(get("shopping_service_base", "http://127.0.0.1:8090")).rstrip("/")
    form_url_base = str(get("form_url_base", service_base)).rstrip("/")
    state_file = Path(str(get("state_file", "data/shopping_signal_bridge_state.json")))
    if not state_file.is_absolute():
        state_file = path.parent.parent / state_file

    return BridgeConfig(
        signal_api_base=str(get("signal_api_base", "http://localhost:8080")).rstrip("/"),
        signal_number=str(get("signal_number")),
        shopping_service_base=service_base,
        shopping_token=str(get("shopping_token")),
        group_internal_id=str(get("group_internal_id")),
        group_recipient=str(get("group_recipient")),
        form_url_base=form_url_base,
        allow_plain_adds=_as_bool(get("allow_plain_adds", False)),
        plain_add_min_words=int(get("plain_add_min_words", 1)),
        max_list_items=int(get("max_list_items", 30)),
        proactive_age_alerts_enabled=_as_bool(get("proactive_age_alerts_enabled", False)),
        age_alert_hour=int(get("age_alert_hour", 8)),
        age_alert_minute=int(get("age_alert_minute", 30)),
        age_alert_min_days=int(get("age_alert_min_days", 3)),
        age_alert_repeat_hours=int(get("age_alert_repeat_hours", 24)),
        age_alert_max_items=int(get("age_alert_max_items", 10)),
        state_file=state_file,
    )


def load_state(path: Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        LOG.exception("Could not read state file %s", path)
        return {}


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dict(state), f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def parse_signal_items(raw_data: str) -> list[Mapping[str, Any]]:
    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError:
        LOG.warning("Ignoring non-JSON websocket message")
        return []
    data_list = payload if isinstance(payload, list) else [payload]
    return [x for x in data_list if isinstance(x, Mapping)]


def extract_envelope_and_message(item: Mapping[str, Any]) -> tuple[Mapping[str, Any], Optional[Mapping[str, Any]]]:
    envelope = item.get("envelope", {})
    if not isinstance(envelope, Mapping):
        return {}, None
    data_msg = envelope.get("dataMessage")
    sync_msg = envelope.get("syncMessage", {})
    if isinstance(sync_msg, Mapping):
        sync_msg = sync_msg.get("sentMessage")
    target_msg = data_msg or sync_msg
    if not isinstance(target_msg, Mapping):
        return envelope, None
    return envelope, target_msg


def internal_id_for(envelope: Mapping[str, Any], target_msg: Mapping[str, Any]) -> Optional[str]:
    group_info = target_msg.get("groupInfo", {})
    if isinstance(group_info, Mapping) and group_info.get("groupId"):
        return str(group_info["groupId"])
    source = envelope.get("source")
    return str(source) if source else None


async def send_signal(session: aiohttp.ClientSession, config: BridgeConfig, message: str) -> None:
    payload = {
        "message": message,
        "number": config.signal_number,
        "recipients": [config.group_recipient],
        "text_mode": "styled",
    }
    async with session.post(config.send_url, json=payload) as resp:
        if resp.status >= 300:
            body = await resp.text()
            LOG.error("Signal send failed HTTP %s: %s", resp.status, body[:500])


def parse_add_command(text: str) -> tuple[str, int]:
    text = text.strip()
    for prefix in ("/add", "/buy"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):].strip()
            break

    quantity = 1
    # Supports: /add 2 milk, /add milk x2, /add milk *2
    m = re.match(r"^(\d{1,2})\s+(.+)$", text)
    if m:
        quantity = max(1, int(m.group(1)))
        text = m.group(2).strip()
    else:
        m = re.match(r"^(.+?)\s+[x*](\d{1,2})$", text, re.IGNORECASE)
        if m:
            text = m.group(1).strip()
            quantity = max(1, int(m.group(2)))
    return text, quantity


async def shopping_add(session: aiohttp.ClientSession, config: BridgeConfig, item: str, quantity: int, source: str = "signal") -> str:
    params = {
        "token": config.shopping_token,
        "item": item,
        "amount": str(quantity),
        "source": source,
    }
    async with session.get(f"{config.shopping_service_base}/add", params=params, headers={"Accept": "application/json"}) as resp:
        data = await resp.json(content_type=None)
        if resp.status >= 300 or not data.get("ok"):
            err = data.get("error") or data.get("status") or f"HTTP {resp.status}"
            return f"⚠️ Could not add {item}: {err}"
        status = data.get("status", "added")
        suffix = f" ×{quantity}" if quantity > 1 else ""
        if data.get("pending"):
            return f"🟡 Queued {item}{suffix}. Sheets unavailable; pending items: {data.get('pending')}"
        return f"✅ {status.capitalize()}: {item}{suffix}"


async def fetch_shopping_items(session: aiohttp.ClientSession, config: BridgeConfig) -> tuple[list[Mapping[str, Any]], int, Optional[str]]:
    params = {"token": config.shopping_token}
    try:
        async with session.get(f"{config.shopping_service_base}/list", params=params, headers={"Accept": "application/json"}) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 300 or not data.get("ok"):
                return [], 0, str(data.get("error") or f"HTTP {resp.status}")
            items = data.get("items") or []
            if not isinstance(items, list):
                items = []
            pending = int(data.get("pending") or 0)
            return [x for x in items if isinstance(x, Mapping)], pending, None
    except Exception as exc:
        LOG.exception("Shopping list fetch failed")
        return [], 0, str(exc)


async def shopping_list(session: aiohttp.ClientSession, config: BridgeConfig) -> str:
    items, pending, error = await fetch_shopping_items(session, config)
    if error:
        return f"⚠️ Shopping list unavailable: {error}"
    if not items:
        extra = f"\nPending queued adds: {pending}" if pending else ""
        return f"🛒 Shopping list is empty.{extra}"

    lines = [f"🛒 Shopping list ({len(items)})"]
    for idx, item in enumerate(items[: config.max_list_items], start=1):
        name = item.get("item", "")
        count = int(item.get("count") or 1)
        count_part = f" ×{count}" if count > 1 else ""
        age = item.get("age_label") or ""
        urgent = bool(item.get("urgent"))
        age_part = f" — {age} ⚠️" if urgent and age else ""
        lines.append(f"{idx}. {name}{count_part}{age_part}")
    if len(items) > config.max_list_items:
        lines.append(f"…and {len(items) - config.max_list_items} more")
    if pending:
        lines.append(f"\nPending queued adds: {pending}. Try /flush")
    return "\n".join(lines)


async def shopping_cleanup(session: aiohttp.ClientSession, config: BridgeConfig) -> str:
    async with session.get(
        f"{config.shopping_service_base}/cleanup",
        params={"token": config.shopping_token},
        headers={"Accept": "application/json"},
    ) as resp:
        data = await resp.json(content_type=None)
        if resp.status >= 300 or not data.get("ok"):
            return f"⚠️ Cleanup failed: {data.get('error') or 'HTTP ' + str(resp.status)}"
        marked = data.get("marked_for_removal", data.get("marked", 0))
        deleted = data.get("deleted", 0)
        return f"🧹 Cleanup complete. Marked: {marked}, deleted: {deleted}."


async def shopping_flush(session: aiohttp.ClientSession, config: BridgeConfig) -> str:
    async with session.get(
        f"{config.shopping_service_base}/flush",
        params={"token": config.shopping_token},
        headers={"Accept": "application/json"},
    ) as resp:
        data = await resp.json(content_type=None)
        if resp.status >= 300 or not data.get("ok"):
            return f"⚠️ Flush failed: {data.get('error') or 'HTTP ' + str(resp.status)}"
        return f"🔁 Flush complete. Flushed: {data.get('flushed', 0)}, pending: {data.get('pending', 0)}."


def help_text(config: BridgeConfig) -> str:
    form_url = f"{config.form_url_base}/form?token={quote(config.shopping_token)}"
    lines = [
        "🛒 Shopping commands",
        "",
        "/shop or /list - show current list",
        "/add milk - add an item",
        "/add 2 milk - add quantity 2",
        "/add milk x2 - add quantity 2",
        "/buy milk - same as /add milk",
        "/form - get the generic add-item form",
        "/cleanup - process checked-off items",
        "/flush - retry queued adds",
        "/help - show this help",
    ]
    if config.proactive_age_alerts_enabled:
        lines.append(
            f"\nMorning age alerts: on at {config.age_alert_hour:02d}:{config.age_alert_minute:02d} "
            f"for items {config.age_alert_min_days}+ days old."
        )
    if config.allow_plain_adds:
        lines.append("\nPlain messages are also added as shopping items.")
    lines.append(f"\nGeneric form: {form_url}")
    return "\n".join(lines)


async def handle_text(session: aiohttp.ClientSession, config: BridgeConfig, text: str) -> Optional[str]:
    text = text.strip()
    if not text:
        return None

    lower = text.lower()
    if lower in {"/help", "help", "/shopping help", "/shop help"}:
        return help_text(config)
    if lower in {"/shop", "/list", "list"}:
        return await shopping_list(session, config)
    if lower in {"/form"}:
        return f"📝 Add an item here:\n{config.form_url_base}/form?token={quote(config.shopping_token)}"
    if lower in {"/cleanup", "/shop cleanup"}:
        return await shopping_cleanup(session, config)
    if lower in {"/flush", "/shop flush"}:
        return await shopping_flush(session, config)
    if lower.startswith("/add") or lower.startswith("/buy"):
        item, quantity = parse_add_command(text)
        if not item:
            return "Use /add milk or /add 2 milk"
        return await shopping_add(session, config, item, quantity, source="signal-group")

    # Keep plain add opt-in to avoid processing the bridge's own replies or chatty group messages.
    if config.allow_plain_adds and not text.startswith("/"):
        words = [w for w in re.split(r"\s+", text) if w]
        if len(words) >= config.plain_add_min_words:
            return await shopping_add(session, config, text, 1, source="signal-plain")

    return None


async def handle_signal_item(session: aiohttp.ClientSession, config: BridgeConfig, item: Mapping[str, Any]) -> None:
    envelope, target_msg = extract_envelope_and_message(item)
    if not target_msg:
        return
    internal_id = internal_id_for(envelope, target_msg)
    if internal_id != config.group_internal_id:
        return

    text = str(target_msg.get("message") or "").strip()
    if not text:
        return
    LOG.info("Shopping group message: %r", text[:120])
    reply = await handle_text(session, config, text)
    if reply:
        await send_signal(session, config, reply)


def _item_age_days(item: Mapping[str, Any]) -> Optional[int]:
    value = item.get("age_days")
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def build_age_alert_message(items: list[Mapping[str, Any]], min_days: int, max_items: int) -> Optional[str]:
    old_items = []
    for item in items:
        days = _item_age_days(item)
        if days is not None and days >= min_days:
            old_items.append((days, item))
    if not old_items:
        return None

    old_items.sort(key=lambda pair: pair[0], reverse=True)
    lines = [
        "🛒 Shopping list ageing",
        "",
        "These have been on the list for a while:",
        "",
    ]
    for days, item in old_items[:max_items]:
        name = str(item.get("item") or "").strip() or "Unnamed item"
        count = int(item.get("count") or 1)
        count_part = f" ×{count}" if count > 1 else ""
        age = item.get("age_label") or f"{days} days ago"
        lines.append(f"• {name}{count_part} — {age} ⚠️")
    if len(old_items) > max_items:
        lines.append(f"• …and {len(old_items) - max_items} more")
    lines.append("\nFull list: /shop")
    return "\n".join(lines)


def should_run_age_check(config: BridgeConfig, state: Mapping[str, Any], now: datetime) -> bool:
    target = time(config.age_alert_hour, config.age_alert_minute)
    if now.time() < target:
        return False
    today = now.date().isoformat()
    if state.get("last_age_check_date") == today:
        return False
    return True


async def age_alert_loop(session: aiohttp.ClientSession, config: BridgeConfig) -> None:
    if not config.proactive_age_alerts_enabled:
        LOG.info("Shopping proactive age alerts disabled")
        return

    LOG.info(
        "Shopping proactive age alerts enabled at %02d:%02d for %s+ day old items",
        config.age_alert_hour,
        config.age_alert_minute,
        config.age_alert_min_days,
    )
    while True:
        try:
            now = datetime.now()
            state = load_state(config.state_file)
            if should_run_age_check(config, state, now):
                items, pending, error = await fetch_shopping_items(session, config)
                state["last_age_check_date"] = now.date().isoformat()
                state["last_age_check_at"] = now.isoformat(timespec="seconds")
                if error:
                    LOG.warning("Age alert list check failed: %s", error)
                    state["last_age_check_error"] = error
                else:
                    message = build_age_alert_message(items, config.age_alert_min_days, config.age_alert_max_items)
                    if message:
                        await send_signal(session, config, message)
                        state["last_age_alert_date"] = now.date().isoformat()
                        state["last_age_alert_at"] = now.isoformat(timespec="seconds")
                        state["last_age_alert_item_count"] = message.count("\n• ")
                        LOG.info("Sent shopping age alert")
                    else:
                        LOG.info("Shopping age check complete: no old items")
                    state.pop("last_age_check_error", None)
                save_state(config.state_file, state)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("Shopping age alert loop failed")
        await asyncio.sleep(60)


async def websocket_loop(session: aiohttp.ClientSession, config: BridgeConfig) -> None:
    backoff = 2
    LOG.info("Shopping Signal bridge listening on %s", config.ws_url)
    while True:
        try:
            async with session.ws_connect(config.ws_url, heartbeat=30) as ws:
                LOG.info("Signal websocket connected")
                backoff = 2
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        for payload_item in parse_signal_items(msg.data):
                            try:
                                await handle_signal_item(session, config, payload_item)
                            except Exception:
                                LOG.exception("Failed to handle Signal message")
                    elif msg.type in {aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE}:
                        LOG.warning("Signal websocket closed/error: %s", ws.exception())
                        break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.error("Signal websocket connection failed: %s", exc)
        LOG.info("Reconnecting in %s seconds", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


async def run(config: BridgeConfig) -> None:
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=None)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [asyncio.create_task(websocket_loop(session, config), name="signal-websocket")]
        if config.proactive_age_alerts_enabled:
            tasks.append(asyncio.create_task(age_alert_loop(session, config), name="age-alerts"))
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc:
                raise exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Signal group bridge for shopping_service.py")
    parser.add_argument("--config", default="config/shopping_signal_bridge.json")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
