# Afaq WhatsApp Bridge

Headless WhatsApp listener using Baileys. No browser, no API fees.
Writes incoming messages to `../messages.json` (append-friendly, read by Flask app).

## Setup (one time)

```bash
cd whatsapp-bridge
npm install
node bridge.js
```

Scan the QR code with your **WhatsApp Business phone**:
- Open WhatsApp → ⋮ More Options → Linked Devices → Link a Device

Session saved in `./session/` — no re-scan needed after first time.

## Running headless (background)

Windows — run once at startup via Task Scheduler or add to startup folder:
```
node C:\path\to\whatsapp-bridge\bridge.js
```

Or use PM2 for auto-restart:
```bash
npm install -g pm2
pm2 start bridge.js --name afaq-wa-bridge
pm2 save
pm2 startup
```

## What it does

- Listens for ALL incoming messages on your linked WhatsApp Business number
- Appends each message to `messages.json` (never overwrites)
- Flask attendance app reads this file and displays bubbles in the panel
- Panel polls every 8 seconds for new messages

## Filter specific senders

Edit `FILTER_SENDERS` in `bridge.js`:
```js
const FILTER_SENDERS = ['Saharjan', 'Management'];
```
Leave empty `[]` to show all messages.

## Rollback

Git tag `v1.1-overtime-stable` is the last stable point before this feature.
