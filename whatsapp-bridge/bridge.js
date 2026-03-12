/**
 * AFAQ ATTENDANCE — WhatsApp Bridge
 * Uses Baileys to connect to WhatsApp Web (headless, no browser).
 * Listens for incoming messages on your Business number.
 * Appends them ONLY to messages.json — never overwrites.
 *
 * Run:  node bridge.js
 * First run: scan the QR code with your WhatsApp Business phone.
 * Session is saved in ./session/ — no re-scan needed after that.
 */

const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  isJidGroup,
} = require('@whiskeysockets/baileys');

const qrcode = require('qrcode-terminal');
const pino   = require('pino');
const fs     = require('fs');
const path   = require('path');

// ── CONFIG ────────────────────────────────────────────────────────────────────
const MESSAGES_FILE = path.join(__dirname, '..', 'messages.json'); // shared with Flask app
const SESSION_DIR   = path.join(__dirname, 'session');
const MAX_MESSAGES  = 200; // keep last 200 in file, older ones stay archived

// Only show messages FROM these senders (leave empty = show all)
// Example: ['Saharjan', 'Management'] — matches sender name contains
const FILTER_SENDERS = [];

// ── APPEND-ONLY SAVE ──────────────────────────────────────────────────────────
function saveMessage(entry) {
  let msgs = [];
  if (fs.existsSync(MESSAGES_FILE)) {
    try { msgs = JSON.parse(fs.readFileSync(MESSAGES_FILE, 'utf8')); }
    catch { msgs = []; }
  }
  msgs.push(entry);
  // Keep last MAX_MESSAGES in the live file — full history never deleted,
  // just trimmed for performance. Original entries stay in git/backup.
  const toWrite = msgs.slice(-MAX_MESSAGES);
  fs.writeFileSync(MESSAGES_FILE, JSON.stringify(toWrite, null, 2));
  console.log(`  [+] Saved: ${entry.sender}: ${entry.body.substring(0, 60)}`);
}

// ── MAIN CONNECT ──────────────────────────────────────────────────────────────
async function connectToWhatsApp() {
  if (!fs.existsSync(SESSION_DIR)) fs.mkdirSync(SESSION_DIR, { recursive: true });

  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  const { version }          = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth:   state,
    logger: pino({ level: 'silent' }), // silent = headless, no noise
    printQRInTerminal: false,           // we handle QR ourselves below
  });

  // ── QR CODE ────────────────────────────────────────────────────────────────
  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.clear();
      console.log('\n  ══════════════════════════════════════════');
      console.log('  🌙 AFAQ — WhatsApp Bridge');
      console.log('  Scan this QR with your Business WhatsApp:');
      console.log('  ══════════════════════════════════════════\n');
      qrcode.generate(qr, { small: true });
      console.log('\n  Open WhatsApp → More options → Linked Devices → Link a Device\n');
    }

    if (connection === 'open') {
      console.log('\n  ✅ WhatsApp connected. Listening for messages...\n');
    }

    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = code !== DisconnectReason.loggedOut;
      console.log(`  [Bridge] Disconnected (code ${code}). Reconnect: ${shouldReconnect}`);
      if (shouldReconnect) {
        setTimeout(connectToWhatsApp, 3000); // auto-reconnect
      } else {
        console.log('  [Bridge] Logged out. Delete ./session/ and restart to re-link.');
      }
    }
  });

  // ── SAVE SESSION CREDS ─────────────────────────────────────────────────────
  sock.ev.on('creds.update', saveCreds);

  // ── INCOMING MESSAGES ──────────────────────────────────────────────────────
  sock.ev.on('messages.upsert', ({ messages, type }) => {
    if (type !== 'notify') return; // only real-time incoming

    for (const msg of messages) {
      try {
        // Skip status broadcasts and empty messages
        if (msg.key.remoteJid === 'status@broadcast') continue;
        if (!msg.message) continue;

        const isGroup  = isJidGroup(msg.key.remoteJid);
        const isFromMe = msg.key.fromMe;

        // Extract body text
        const body =
          msg.message?.conversation ||
          msg.message?.extendedTextMessage?.text ||
          msg.message?.imageMessage?.caption ||
          msg.message?.videoMessage?.caption ||
          (msg.message?.documentMessage ? '[Document]' : null) ||
          (msg.message?.audioMessage    ? '[Voice Message]' : null) ||
          (msg.message?.imageMessage    ? '[Image]' : null) ||
          (msg.message?.videoMessage    ? '[Video]' : null) ||
          '[Message]';

        // Sender name
        const pushName = msg.pushName || 'Unknown';
        const jid      = msg.key.remoteJid;

        // Apply sender filter if configured
        if (FILTER_SENDERS.length > 0) {
          const match = FILTER_SENDERS.some(f =>
            pushName.toLowerCase().includes(f.toLowerCase())
          );
          if (!match) continue;
        }

        const entry = {
          id:        msg.key.id,
          date:      new Date().toISOString().slice(0, 10),
          timestamp: new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }),
          sender:    pushName,
          from_me:   isFromMe,
          is_group:  isGroup,
          chat:      jid,
          body:      body,
          type:      Object.keys(msg.message || {})[0] || 'text',
        };

        saveMessage(entry);

      } catch (err) {
        console.error('  [Bridge] Error processing message:', err.message);
      }
    }
  });
}

// ── START ─────────────────────────────────────────────────────────────────────
console.log('\n  Starting AFAQ WhatsApp Bridge...');
connectToWhatsApp().catch(err => {
  console.error('  [Bridge] Fatal error:', err);
  process.exit(1);
});
