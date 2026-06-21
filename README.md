# Shopping Automation

A small home automation service for maintaining a shared household shopping list in Google Sheets.

It supports three ways of adding and viewing shopping items:

- NFC tags stuck to cupboards, shelves, appliances, or product locations.
- A generic mobile web form for ad-hoc items.
- A Signal group bridge for `/shop`, `/add`, and related household commands.

The Google Sheet is the source of truth. Phones, NFC tags, and Signal all update the same list.

---

## Architecture

```text
NFC tag
  -> http://<home-host>:8090/t/<tag-id>
  -> shopping_service.py
  -> Google Sheets

Generic NFC tag / browser
  -> http://<home-host>:8090/form?token=<token>
  -> shopping_service.py
  -> Google Sheets

Signal Shopping group
  -> signal-cli-rest-api websocket
  -> shopping_signal_bridge.py
  -> shopping_service.py
  -> Google Sheets
```

The project is intentionally split into two services:

| Service | Purpose |
|---|---|
| `shopping_service.py` | HTTP service for NFC tags, web form, Google Sheets updates, list cleanup, and pending queue flushes. |
| `shopping_signal_bridge.py` | Signal group listener that turns group commands into calls to `shopping_service.py`. Also sends daily age warnings. |

This keeps the Google Sheets logic in one place and makes Signal just another interface.

---

## Repository layout

```text
shopping_automation/
├── shopping_service.py
├── shopping_signal_bridge.py
├── requirements-shopping.txt
├── README.md
├── .gitignore
├── config/
│   ├── shopping_service.example.json
│   └── shopping_signal_bridge.example.json
└── data/                         # runtime only, ignored by git
```

Local-only files that should not be committed:

```text
config/shopping_service.json
config/shopping_signal_bridge.json
config/google-sheets-service-account.json
data/
.venv/
```

---

## Google Sheet layout

The sheet tab should be named:

```text
Shopping
```

The active columns are:

| Column | Name | Purpose |
|---|---|---|
| A | Done | Checkbox. Tick when bought. |
| B | Item | Human-readable item name. |
| C | Count | Quantity or scan count. |
| D | Category | Optional grouping such as Pantry, Frozen, Cleaning, DIY. |
| E | Added At | First time the item was added. |
| F | Last Added At | Most recent scan/add time. |
| G | Source | NFC tag, Signal, form, etc. |
| H | Remove After | Cleanup timestamp for checked items. |

For mobile use, the most useful view is usually columns A-C:

```text
Done | Item | Count
```

Columns D-H can be hidden on mobile if desired.

---

## Behaviour

### Adding an item

When an item is added through NFC, form, or Signal:

- If the item does not exist, a new unchecked row is created.
- If the item already exists and is unchecked, the count is incremented/updated and `Last Added At` is refreshed.
- If the item exists but is checked, it is restored to unchecked and treated as needed again.

### Checked item cleanup

Checked items are not deleted immediately. Cleanup is staged:

1. You tick the checkbox in Google Sheets.
2. Cleanup sees `Done = TRUE`.
3. If `Remove After` is blank, cleanup writes a future timestamp.
4. On a later cleanup run, once that timestamp has passed, the row is removed.

This gives a grace period in case an item was checked by mistake.

### Pending queue

`Pending` means an add request could not be synced to Google Sheets at the time it arrived.

Typical causes:

- Internet outage.
- Google Sheets API issue.
- Service account permission problem.
- Spreadsheet ID or tab name mismatch.

Pending items are stored locally in SQLite and can be retried with `/flush` or the `/flush` HTTP endpoint.

### Age warnings

The Signal bridge can proactively send a daily morning message if items have been on the shopping list for too long.

Example:

```text
🛒 Shopping list ageing

These have been on the list for a while:

• Peanut butter — added 4 days ago ⚠️
• Dishwasher tablets — added 3 days ago ⚠️

Full list: /shop
```

---

## Python setup

Use a separate virtual environment for this project.

```bash
cd /home/<user>/Projects/shopping_automation
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-shopping.txt
```

---

## Google Sheets setup

1. Create a Google Cloud project.
2. Enable the Google Sheets API.
3. Create a service account.
4. Download a JSON key locally.
5. Store it outside git, for example:

```text
config/google-sheets-service-account.json
```

6. Share the Google Sheet with the service account email as **Editor**.

The service account does not need broad Google Cloud roles for this project. Spreadsheet-level Editor access is the important permission.

---

## Configuration

Create real config files from examples:

```bash
cp config/shopping_service.example.json config/shopping_service.json
cp config/shopping_signal_bridge.example.json config/shopping_signal_bridge.json
```

### `config/shopping_service.json`

Important fields:

```json
{
  "host": "0.0.0.0",
  "port": 8090,
  "spreadsheet_id": "<google-sheet-id>",
  "sheet_name": "Shopping",
  "service_account_file": "config/google-sheets-service-account.json",
  "admin_token": "change-this-token",
  "cleanup_checked_after_hours": 18,
  "age_warning_days": 3,
  "tags": {
    "corn-flakes": {
      "item": "Corn Flakes",
      "category": "Pantry"
    }
  }
}
```

### `config/shopping_signal_bridge.json`

Important fields:

```json
{
  "signal_api_base": "http://localhost:8080",
  "signal_number": "+00000000000",
  "shopping_service_base": "http://127.0.0.1:8090",
  "shopping_token": "same-token-as-shopping-service",
  "group_internal_id": "<signal-group-internal-id>",
  "group_recipient": "group.<signal-group-recipient>",
  "form_url_base": "http://<home-host>:8090",
  "allow_plain_adds": false,
  "max_list_items": 30,
  "state_file": "data/shopping_signal_bridge_state.json",
  "proactive_age_alerts_enabled": true,
  "age_alert_hour": 8,
  "age_alert_minute": 30,
  "age_alert_min_days": 3,
  "age_alert_repeat_hours": 24,
  "age_alert_max_items": 10
}
```

Use a strong token before exposing the service outside a trusted LAN or VPN.

---

## One-time sheet setup

After config is in place, initialise the sheet headers and checkbox validation:

```bash
cd /home/<user>/Projects/shopping_automation
source .venv/bin/activate
python shopping_service.py --config config/shopping_service.json --setup-sheet
```

Or while the service is running:

```bash
curl "http://127.0.0.1:8090/admin/setup?token=<token>"
```

If migrating from an older column order, use:

```bash
curl "http://127.0.0.1:8090/admin/migrate-layout?token=<token>"
```

---

## Running manually

Start the shopping HTTP service:

```bash
cd /home/<user>/Projects/shopping_automation
source .venv/bin/activate
python shopping_service.py --config config/shopping_service.json
```

In another terminal, start the Signal bridge:

```bash
cd /home/<user>/Projects/shopping_automation
source .venv/bin/activate
python shopping_signal_bridge.py --config config/shopping_signal_bridge.json
```

---

## HTTP endpoints

| Endpoint | Purpose |
|---|---|
| `/health` | Health check. |
| `/t/<tag_id>` | NFC tag endpoint. Adds the configured item. |
| `/form?token=<token>` | Generic mobile form for ad-hoc items. |
| `/add?item=<item>&token=<token>` | Manual/debug add endpoint. |
| `/list?token=<token>` | Returns current active shopping list. |
| `/cleanup?token=<token>` | Processes checked items for delayed removal. |
| `/flush?token=<token>` | Retries pending queued adds. |
| `/admin/setup?token=<token>` | Writes headers and checkbox validation. |
| `/admin/compact?token=<token>` | Compacts/merges duplicate rows. |
| `/admin/migrate-layout?token=<token>` | Migrates older sheet column layout. |

Example:

```bash
curl "http://127.0.0.1:8090/health"
curl "http://127.0.0.1:8090/t/corn-flakes"
curl "http://127.0.0.1:8090/list?token=<token>"
```

---

## NFC tags

Write URLs to NFC tags using an Android app such as NFC Tools.

Example item-specific tag:

```text
http://<home-host>:8090/t/corn-flakes
```

Example generic form tag:

```text
http://<home-host>:8090/form?token=<token>
```

Item-specific tags are safer because they only perform one configured action and do not expose the admin token.

The generic form tag is more powerful because it includes the token. Treat that tag as privileged.

---

## Signal group commands

Send these in the configured Shopping Signal group:

```text
/help
/shop
/list
/add milk
/add milk x2
/add 2 milk
/buy milk
/form
/cleanup
/flush
```

`/shop` and `/list` show current items, including age warnings for older items.

---

## systemd user services

The project normally runs as two user services.

### `shopping-service.service`

Create:

```bash
nano ~/.config/systemd/user/shopping-service.service
```

Contents:

```ini
[Unit]
Description=Shopping NFC Webhook Service

[Service]
Type=simple
WorkingDirectory=/home/<user>/Projects/shopping_automation
ExecStart=/home/<user>/Projects/shopping_automation/.venv/bin/python /home/<user>/Projects/shopping_automation/shopping_service.py --config /home/<user>/Projects/shopping_automation/config/shopping_service.json
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

### `shopping-signal-bridge.service`

Create:

```bash
nano ~/.config/systemd/user/shopping-signal-bridge.service
```

Contents:

```ini
[Unit]
Description=Shopping Signal Group Bridge
After=shopping-service.service
Wants=shopping-service.service

[Service]
Type=simple
WorkingDirectory=/home/<user>/Projects/shopping_automation

ExecStartPre=/bin/sh -c 'for i in $(seq 1 60); do curl -fs http://127.0.0.1:8090/health >/dev/null && exit 0; sleep 2; done; exit 1'
ExecStartPre=/bin/sh -c 'for i in $(seq 1 60); do curl -fs http://127.0.0.1:8080/v1/about >/dev/null && exit 0; sleep 2; done; exit 0'

ExecStart=/home/<user>/Projects/shopping_automation/.venv/bin/python /home/<user>/Projects/shopping_automation/shopping_signal_bridge.py --config /home/<user>/Projects/shopping_automation/config/shopping_signal_bridge.json
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

The bridge waits for the shopping service to become healthy. It also tries to wait for the Signal API container, but does not hard-fail if the `/v1/about` endpoint is unavailable in the installed Signal API version.

### Enable and start

```bash
systemctl --user daemon-reload
systemctl --user enable shopping-service.service
systemctl --user enable shopping-signal-bridge.service
systemctl --user start shopping-service.service
systemctl --user start shopping-signal-bridge.service
```

### Check status

```bash
systemctl --user --no-pager status shopping-service.service
systemctl --user --no-pager status shopping-signal-bridge.service
```

### Logs

```bash
journalctl --user -u shopping-service.service -f
journalctl --user -u shopping-signal-bridge.service -f
```

Recent logs:

```bash
journalctl --user -u shopping-service.service -n 100 --no-pager
journalctl --user -u shopping-signal-bridge.service -n 100 --no-pager
```

### Restart after config/code changes

```bash
systemctl --user restart shopping-service.service
systemctl --user restart shopping-signal-bridge.service
```

If only Signal group behaviour changed:

```bash
systemctl --user restart shopping-signal-bridge.service
```

If only NFC/Sheits/web-form behaviour changed:

```bash
systemctl --user restart shopping-service.service
```

---

## Reboot startup

Ensure lingering is enabled:

```bash
loginctl show-user <user> | grep Linger
```

Expected:

```text
Linger=yes
```

If not enabled:

```bash
sudo loginctl enable-linger <user>
```

After reboot:

```bash
systemctl --user --no-pager status shopping-service.service
systemctl --user --no-pager status shopping-signal-bridge.service
```

---

## Git safety

Do not commit real config, credentials, state, or local virtualenv files.

Before committing:

```bash
git status --short | grep -Ei 'service-account|credentials|scripted-|token|secret|config/.*\.json|\.sqlite|\.db|data/|\.venv'
```

Acceptable output should only include example config files, such as:

```text
A  config/shopping_service.example.json
A  config/shopping_signal_bridge.example.json
```

If a service account JSON is ever committed locally, remove it from history before pushing and rotate the key in Google Cloud.

---

## Backup

Back up the local-only files somewhere safe, for example Google Drive or another private backup location:

```text
config/shopping_service.json
config/shopping_signal_bridge.json
config/google-sheets-service-account.json
data/
```

These are ignored by git but required to restore the running service.

---

## Common troubleshooting

### Sheet says range cannot be parsed

Check the sheet tab is named exactly:

```text
Shopping
```

or update `sheet_name` in `config/shopping_service.json`.

### Items appear at the bottom of the sheet

Run:

```bash
curl "http://127.0.0.1:8090/admin/compact?token=<token>"
```

The service uses column B (`Item`) as the source of truth and writes into the first empty item row.

### Form says bad or missing token

Open the form with:

```text
http://<home-host>:8090/form?token=<token>
```

If using an NFC tag, rewrite the tag with the full form URL.

### Signal bridge starts before shopping service

The bridge systemd unit should include the `ExecStartPre` health check against:

```text
http://127.0.0.1:8090/health
```

The first curl can fail during startup, then succeed on the retry. That is normal.

### Signal bridge cannot connect

Check the Signal API container:

```bash
docker ps | grep signal-api
```

or, if using Snap Docker:

```bash
/snap/bin/docker ps | grep signal-api
```

Check the bridge logs:

```bash
journalctl --user -u shopping-signal-bridge.service -n 100 --no-pager
```
