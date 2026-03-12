/**
 * AFAQ ATTENDANCE — WhatsApp Bridge
 * - Writes QR code as base64 image to qr_state.json (Flask reads + shows in browser)
 * - Appends messages to messages.json (never overwrites)
 * - Auto-reconnects on disconnect
 */

const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  isJidGroup,
} = require('@whiskeysockets/baileys');

const QRCode = require('qrcode');
const pino   = require('pino');
const fs     = require('fs');
const path   = require('path');

// ── PATHS ─────────────────────────────────────────────────────────────────────
const BASE_DIR      = path.join(__dirname, '..');
const MESSAGES_FILE = path.join(BASE_DIR, 'messages.json');
const QR_STATE_FILE = path.join(BASE_DIR, 'qr_state.json');
const SESSION_DIR   = path.join(__dirname, 'session');

// ── QR STATE ──────────────────────────────────────────────────────────────────
function writeQRState(state, qrDataURL = null) {
  fs.writeFileSync(QR_STATE_FILE, JSON.stringify({
    state,        // "waiting_scan" | "connected" | "disconnected"
    qr: qrDataURL,
    updated: new Date().toISOString()
  }, null, 2));
}

function clearQRState() {
  writeQRState('connected', null);
}

// ── APPEND-ONLY MESSAGE SAVE ──────────────────────────────────────────────────
function saveMessage(entry) {
  let msgs = [];
  if (fs.existsSync(MESSAGES_FILE)) {
    try { msgs = JSON.parse(fs.readFileSync(MESSAGES_FILE, 'utf8')); }
    catch { msgs = []; }
  }
  msgs.push(entry);
  fs.writeFileSync(MESSAGES_FILE, JSON.stringify(msgs.slice(-200), null, 2));
  console.log(`  [+] ${entry.sender}: ${entry.body.substring(0, 60)}`);
}

// ── MAIN ──────────────────────────────────────────────────────────────────────
async function connectToWhatsApp() {
  if (!fs.existsSync(SESSION_DIR)) fs.mkdirSync(SESSION_DIR, { recursive: true });

  writeQRState('waiting_scan', null);

  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  const { version }          = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth:   state,
    logger: pino({ level: 'silent' }),
    printQRInTerminal: false,
  });

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    // ── QR received — convert to base64 image for browser ──
    if (qr) {
      try {
        const dataURL = await QRCode.toDataURL(qr, {
          errorCorrectionLevel: 'M',
          width: 280,
          margin: 2,
          color: { dark: '#111111', light: '#ffffff' }
        });
        writeQRState('waiting_scan', dataURL);
        console.log('  [Bridge] QR ready — open the app in browser to scan.');
      } catch (e) {
        console.error('  [Bridge] QR generation error:', e.message);
      }
    }

    if (connection === 'open') {
      clearQRState();
      console.log('  [Bridge] ✅ WhatsApp connected. Listening...');
    }

    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = code !== DisconnectReason.loggedOut;
      writeQRState('disconnected');
      console.log(`  [Bridge] Disconnected (${code}). Reconnect: ${shouldReconnect}`);
      if (shouldReconnect) {
        setTimeout(connectToWhatsApp, 4000);
      } else {
        console.log('  [Bridge] Logged out. Delete whatsapp-bridge/session/ and restart.');
        writeQRState('logged_out');
      }
    }
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('messages.upsert', ({ messages, type }) => {
    if (type !== 'notify') return;
    for (const msg of messages) {
      try {
        if (msg.key.remoteJid === 'status@broadcast') continue;
        if (!msg.message) continue;

        const body =
          msg.message?.conversation ||
          msg.message?.extendedTextMessage?.text ||
          msg.message?.imageMessage?.caption ||
          msg.message?.videoMessage?.caption ||
          (msg.message?.documentMessage ? '[Document]'     : null) ||
          (msg.message?.audioMessage    ? '[Voice Message]': null) ||
          (msg.message?.imageMessage    ? '[Image]'        : null) ||
          (msg.message?.videoMessage    ? '[Video]'        : null) ||
          '[Message]';

        saveMessage({
          id:        msg.key.id,
          date:      new Date().toISOString().slice(0, 10),
          timestamp: new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }),
          sender:    msg.pushName || 'Unknown',
          from_me:   msg.key.fromMe,
          is_group:  isJidGroup(msg.key.remoteJid),
          chat:      msg.key.remoteJid,
          body,
          type: Object.keys(msg.message || {})[0] || 'text',
        });
      } catch (err) {
        console.error('  [Bridge] Message error:', err.message);
      }
    }
  });
}

console.log('\n  Starting AFAQ WhatsApp Bridge...');
connectToWhatsApp().catch(err => {
  console.error('  [Bridge] Fatal:', err);
  writeQRState('error');
  process.exit(1);
});
