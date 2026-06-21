# NFC Shopping Service

A small Flask webhook service for NFC tags that add household shopping items to a shared Google Sheet.

The normal scan flow is:

```text
NFC tag -> /t/<tag_id> -> shopping_service.py -> Google Sheets
```

The service deduplicates items, can reactivate a checked-off item, and can remove checked rows after a grace period.

## Sheet layout

Create a tab named `Shopping` with these columns:

| Column | Header |
|---|---|
| A | Done |
| B | Item |
| C | Category |
| D | Added At |
| E | Source |
| F | Count |
| G | Last Added At |
| H | Remove After |

The service can write the headers and apply checkbox validation to `A2:A1000` with:

```bash
python shopping_service.py --config config/shopping_service.json --setup-sheet
```

or, once the service is running:

```bash
curl "http://localhost:8090/admin/setup?token=<shared_token>"
```

## Google setup

1. Create a Google Cloud project.
2. Enable the Google Sheets API.
3. Create a service account.
4. Download the service account JSON to `config/google-sheets-service-account.json`.
5. Share the spreadsheet with the service account email address as an editor.

## Install

```bash
cd /home/<user>/Projects/signal_automation
source .venv/bin/activate
pip install -r requirements-shopping.txt
cp config/shopping_service.example.json config/shopping_service.json
nano config/shopping_service.json
python shopping_service.py --config config/shopping_service.json --setup-sheet
python shopping_service.py --config config/shopping_service.json
```

## Example endpoints

Add via mapped NFC tag:

```text
http://192.168.1.205:8090/t/dishwasher-tablets-demo
```

Manual/debug add:

```text
http://192.168.1.205:8090/add?item=milk&category=Fridge&token=<shared_token>
```

List current unchecked items:

```text
http://192.168.1.205:8090/list?token=<shared_token>
```

Force cleanup of checked rows:

```text
http://192.168.1.205:8090/cleanup?token=<shared_token>
```

## Cleanup behaviour

When a row is checked in the sheet:

1. The next cleanup pass sets `Remove After` to `now + cleanup_checked_after_hours`.
2. A later cleanup pass deletes the row once `Remove After` is in the past.

This avoids deleting an accidentally checked item immediately.

## NFC tag strategy

Prefer tag IDs instead of putting the real item and shared token onto the tag:

```text
http://192.168.1.205:8090/t/coffee-demo
```

Then map the tag ID in config:

```json
"tags": {
  "coffee-demo": {
    "item": "Coffee",
    "category": "Pantry"
  }
}
```

This lets you rename items later without rewriting the NFC tag.

## Systemd user service

Copy the example service:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/shopping-service.service.example ~/.config/systemd/user/shopping-service.service
nano ~/.config/systemd/user/shopping-service.service
systemctl --user daemon-reload
systemctl --user enable shopping-service.service
systemctl --user start shopping-service.service
journalctl --user -u shopping-service.service -f
```

## Security notes

This service is intentionally simple. Start with LAN/VPN access only. Do not expose it publicly unless you add HTTPS, rate limiting, and a long random token. Avoid writing the shared token onto NFC tags; use `/t/<tag_id>` instead.
