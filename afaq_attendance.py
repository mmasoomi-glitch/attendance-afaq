import json, os, sys, threading, webbrowser, socket, platform, subprocess
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, Response

app = Flask(__name__)
PORT = 3456
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
DATA_FILE    = os.path.join(BASE_DIR, 'attendance_data.json')
ANNOUNCE_FILE = os.path.join(BASE_DIR, 'announcements.json')  # append-only, never overwritten
MESSAGES_FILE = os.path.join(BASE_DIR, 'messages.json')        # written by WhatsApp bridge, read-only here
QR_STATE_FILE = os.path.join(BASE_DIR, 'qr_state.json')        # written by bridge: QR image + connection state
BRIDGE_DIR    = os.path.join(BASE_DIR, 'whatsapp-bridge')
STARTUP_FLAG = os.path.join(BASE_DIR, '.startup_done')

SCHEDULES = {
    "team": [
        {"label": "Morning In",  "time": "10:00"},
        {"label": "Morning Out", "time": "15:30"},
        {"label": "Evening In",  "time": "19:30"},
        {"label": "Evening Out", "time": "22:30"},
    ],
    "masoumi": [
        {"label": "Morning In",  "time": "09:00"},
        {"label": "Morning Out", "time": "14:30"},
        {"label": "Evening In",  "time": "16:00"},
        {"label": "Evening Out", "time": "19:00"},
    ],
}

EMPLOYEES = [
    {"name": "Hafiz",    "type": "team"},
    {"name": "Mehriban", "type": "team"},
    {"name": "Nader",    "type": "team"},
    {"name": "Masoumi",  "type": "masoumi"},
]

# ── OVERTIME DECLARATIONS ─────────────────────────────────────────────────────
# Key: (employee, date, session)  session = "morning" | "evening"
# Set at clock-IN; extends OUT window from ±15 min to before:15, after:75
overtime_flags = {}

SESSION_MAP = {
    "Morning In":  "morning",
    "Evening In":  "evening",
    "Morning Out": "morning",
    "Evening Out": "evening",
}

def declare_overtime(employee, date, session):
    overtime_flags[(employee, date, session)] = True

def has_overtime(employee, date, session):
    return overtime_flags.get((employee, date, session), False)

# ─────────────────────────────────────────────────────────────────────────────

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

LOCAL_IP = get_local_ip()

def revoke_firewall():
    """Remove the AfaqAttendance firewall rule silently."""
    if platform.system() != "Windows":
        return
    try:
        subprocess.run(
            ['netsh','advfirewall','firewall','delete','rule','name=AfaqAttendance'],
            capture_output=True)
        print(f"  [Firewall] Rule revoked — port {PORT} closed.")
    except Exception as e:
        print(f"  [Firewall] Revoke error: {e}")

def open_firewall():
    """Step 1: revoke any leftover rule. Step 2: open fresh."""
    if platform.system() != "Windows":
        return
    # Always clean first — no stale rules from previous sessions
    revoke_firewall()
    try:
        result = subprocess.run(
            ['netsh','advfirewall','firewall','add','rule',
             'name=AfaqAttendance','dir=in','action=allow',
             'protocol=TCP',f'localport={PORT}','profile=private,domain'],
            capture_output=True, text=True)
        status = "✅ opened fresh" if result.returncode == 0 else "⚠️ failed (run as Admin)"
        print(f"  [Firewall] Port {PORT} {status}")
    except Exception as e:
        print(f"  [Firewall] Open error: {e}")

def daily_shutdown():
    """
    Background thread:
      — Waits until 23:59:00 each day
      — Revokes firewall rule
      — Shuts down the exe cleanly
    App is restarted fresh next morning via Task Scheduler / Startup folder.
    """
    import time as _time
    while True:
        now    = datetime.now()
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        h, m = int(wait_seconds // 3600), int((wait_seconds % 3600) // 60)
        print(f"  [Scheduler] Daily shutdown scheduled in {h}h {m}m (at 23:59)")
        _time.sleep(wait_seconds)

        print("\n  [Scheduler] 23:59 — revoking firewall and shutting down...")
        revoke_firewall()
        _time.sleep(2)   # brief pause so revoke completes
        os._exit(0)      # clean exit — kills Flask + all threads

def add_to_startup():
    try:
        import winreg
        exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "AfaqAttendance", 0, winreg.REG_SZ, f'"{exe_path}"')
        winreg.CloseKey(key)
        return True
    except:
        return False

def ask_startup_confirmation():
    if platform.system() != "Windows" or os.path.exists(STARTUP_FLAG):
        return
    try:
        import ctypes
        result = ctypes.windll.user32.MessageBoxW(
            0,
            "Would you like Afaq Attendance to start automatically\n"
            "every time Windows starts?\n\nClick YES to enable auto-start.",
            "Afaq Attendance — Auto Start", 0x00000024)
        if result == 6:
            success = add_to_startup()
            msg = ("✅ Done! Will start automatically with Windows."
                   if success else "⚠️ Could not register. Run as Administrator.")
            ctypes.windll.user32.MessageBoxW(0, msg, "Afaq Attendance", 0x00000040)
        with open(STARTUP_FLAG, 'w') as f:
            f.write("done")
    except Exception as e:
        print(f"  [Startup] Error: {e}")

def is_within_window(t, before_mins=15, after_mins=15):
    """Standard ±15 min. Overtime OUT: before=15, after=75 (1hr extra)."""
    now = datetime.now()
    h, m = map(int, t.split(":"))
    target = datetime.combine(now.date(), datetime.min.time().replace(hour=h, minute=m))
    return (target - timedelta(minutes=before_mins)) <= now <= (target + timedelta(minutes=after_mins))

def save_entry(entry):
    logs = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            try: logs = json.load(f)
            except: logs = []
    logs.append(entry)
    with open(DATA_FILE, 'w') as f:
        json.dump(logs, f, indent=4)

def get_today_logs():
    today = datetime.now().strftime("%Y-%m-%d")
    if not os.path.exists(DATA_FILE): return []
    with open(DATA_FILE, 'r') as f:
        try: logs = json.load(f)
        except: return []
    return [l for l in logs if l.get("date") == today]

# ── ANNOUNCEMENTS (append-only, separate file) ────────────────────────────────
MANAGER_PIN = "1234"   # change this to your preferred PIN

def save_announcement(msg, sender="Management"):
    """Append a new announcement — never overwrites existing messages."""
    msgs = []
    if os.path.exists(ANNOUNCE_FILE):
        with open(ANNOUNCE_FILE, 'r') as f:
            try: msgs = json.load(f)
            except: msgs = []
    msgs.append({
        "date":      datetime.now().strftime("%Y-%m-%d"),
        "timestamp": datetime.now().strftime("%H:%M"),
        "sender":    sender,
        "message":   msg,
    })
    with open(ANNOUNCE_FILE, 'w') as f:
        json.dump(msgs, f, indent=4)

def get_announcements(limit=30):
    if not os.path.exists(ANNOUNCE_FILE): return []
    with open(ANNOUNCE_FILE, 'r') as f:
        try: msgs = json.load(f)
        except: return []
    return msgs[-limit:]  # last 30 only

def get_messages(limit=50):
    """Read WhatsApp messages written by the bridge. Never writes — read-only."""
    if not os.path.exists(MESSAGES_FILE): return []
    with open(MESSAGES_FILE, 'r') as f:
        try: msgs = json.load(f)
        except: return []
    return msgs[-limit:]

def get_qr_state():
    """Read QR state written by the bridge."""
    if not os.path.exists(QR_STATE_FILE):
        return {"state": "bridge_not_running", "qr": None}
    with open(QR_STATE_FILE, 'r') as f:
        try: return json.load(f)
        except: return {"state": "error", "qr": None}

def start_bridge():
    """Launch the Node.js WhatsApp bridge as a subprocess."""
    bridge_js = os.path.join(BRIDGE_DIR, 'bridge.js')
    if not os.path.exists(bridge_js):
        print("  [Bridge] bridge.js not found — skipping.")
        return
    # Find node executable
    node = 'node'
    try:
        subprocess.Popen(
            [node, bridge_js],
            cwd=BRIDGE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
        )
        print("  [Bridge] WhatsApp bridge started.")
    except FileNotFoundError:
        print("  [Bridge] Node.js not found. Install Node.js to enable WhatsApp panel.")

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Afaq Attendance</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@700;900&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Share Tech Mono',monospace;background:#080d1a;color:#dde8ff;min-height:100vh;padding:0 0 40px}

/* ── IMPORTANCE BANNER ── */
.importance{
  background:linear-gradient(90deg,#7b0000,#c0392b,#7b0000);
  padding:14px 20px;text-align:center;
  border-bottom:2px solid #ff4444;
  animation:pulse-red 2s infinite;
}
@keyframes pulse-red{0%,100%{box-shadow:0 0 0 rgba(255,68,68,0)}50%{box-shadow:0 0 18px rgba(255,68,68,0.5)}}
.importance-icon{font-size:1.3em;margin-right:8px}
.importance-text{font-family:'Orbitron',sans-serif;font-size:.78em;color:#fff;letter-spacing:2px;line-height:1.7}
.importance-text strong{color:#ffd700;font-size:1.05em}

/* ── HEADER ── */
.header{padding:24px 16px 8px;text-align:center}
h1{font-family:'Orbitron',sans-serif;font-size:1.75em;color:#f0c040;letter-spacing:4px;margin-bottom:4px}
.sub{color:#3a5a80;font-size:.72em;letter-spacing:3px;margin-bottom:10px}
.clock{font-family:'Orbitron',sans-serif;font-size:2em;color:#7fd1fc;margin-bottom:4px}
.date-str{color:#3a5a80;font-size:.72em;margin-bottom:16px}

/* ── NETWORK BANNER ── */
.net-banner{max-width:500px;margin:0 auto 20px;background:linear-gradient(135deg,#0a1f10,#0d2a1a);border:1px solid #00c853;border-radius:10px;padding:14px 20px;text-align:center}
.net-label{color:#3a7a4a;font-size:.66em;letter-spacing:2px;margin-bottom:6px}
.net-link{font-family:'Orbitron',sans-serif;font-size:1.15em;color:#00e676;letter-spacing:1px}
.net-sub{color:#2a5a38;font-size:.64em;margin-top:5px}

.note{text-align:center;color:#1e3a5f;font-size:.68em;margin-bottom:24px;padding:0 16px}

/* ── PUNCH CARDS ── */
.grid{display:flex;flex-wrap:wrap;justify-content:center;gap:16px;margin-bottom:32px;padding:0 16px}
.card{background:linear-gradient(160deg,#101d35,#0d1726);border:1px solid #1e3a5f;border-radius:12px;padding:18px 16px;flex:1;min-width:175px;max-width:215px;box-shadow:0 4px 24px rgba(0,100,255,.08)}
.emp{font-family:'Orbitron',sans-serif;font-size:.78em;color:#f0c040;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #1e3a5f}
button{display:block;width:100%;padding:9px 12px;margin:5px 0;border:none;border-radius:6px;font-family:'Share Tech Mono',monospace;font-size:.77em;text-align:left;cursor:pointer;transition:all .15s}
.btn-on{background:linear-gradient(135deg,#00c853,#1de9b6);color:#001a0e;font-weight:bold;box-shadow:0 0 12px #00c85344}
.btn-on:hover{transform:scale(1.02);box-shadow:0 0 20px #00c85377}
.btn-off{background:#0d1726;color:#1e3a5f;cursor:not-allowed;border:1px solid #111d30}

/* ── OVERTIME ── */
.ot-box{background:#0d1f12;border:1px solid #1a5a2a;border-radius:6px;padding:7px 10px;margin:3px 0 6px;display:flex;align-items:center;gap:8px;cursor:pointer}
.ot-box input[type=checkbox]{accent-color:#f0c040;width:15px;height:15px;cursor:pointer;flex-shrink:0}
.ot-label{font-size:.72em;color:#7fc89a;line-height:1.3}
.ot-label strong{color:#f0c040}
.ot-active{background:linear-gradient(135deg,#7b4a00,#c07800);color:#fff8e1;font-size:.68em;padding:4px 8px;border-radius:4px;margin:3px 0;text-align:center;letter-spacing:1px}

/* ── TODAY LOG ── */
.logs-wrap{max-width:660px;margin:0 auto 32px;padding:0 16px}
.logs-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}
.logs-title{font-family:'Orbitron',sans-serif;font-size:.7em;color:#7fd1fc;letter-spacing:3px}
.export-btn{background:#1e3a5f;color:#7fd1fc;border:none;border-radius:6px;padding:6px 14px;cursor:pointer;font-family:'Share Tech Mono',monospace;font-size:.7em;text-decoration:none;display:inline-block}
.log-box{background:#0d1726;border:1px solid #1e3a5f;border-radius:10px;padding:14px 18px;min-height:56px}
.log-row{display:flex;gap:10px;padding:6px 0;border-bottom:1px solid #111d30;font-size:.76em;align-items:center;flex-wrap:wrap}
.log-row:last-child{border-bottom:none}
.l-emp{color:#f0c040;min-width:76px;font-weight:bold}
.l-event{color:#00c853}
.l-sched{color:#3a5a80}
.l-time{color:#7fd1fc;margin-left:auto}
.empty{color:#1e3a5f;font-style:italic;font-size:.8em}

/* ── GUIDE SECTION ── */
.guide{max-width:700px;margin:0 auto;padding:0 16px}
.guide-toggle{width:100%;background:linear-gradient(135deg,#0f2040,#1a3a6a);border:1px solid #2a5a9a;border-radius:10px;color:#7fd1fc;font-family:'Orbitron',sans-serif;font-size:.78em;letter-spacing:2px;padding:14px 20px;cursor:pointer;text-align:left;display:flex;justify-content:space-between;align-items:center;margin-bottom:0}
.guide-toggle:hover{background:linear-gradient(135deg,#152850,#1e4a80)}
.guide-body{display:none;background:#0a1628;border:1px solid #1e3a5f;border-top:none;border-radius:0 0 10px 10px;padding:24px;font-size:.82em;line-height:1.8}
.guide-body.open{display:block}
.g-section{margin-bottom:22px}
.g-title{font-family:'Orbitron',sans-serif;font-size:.75em;color:#f0c040;letter-spacing:2px;margin-bottom:10px;display:flex;align-items:center;gap:8px}
.g-row{display:flex;gap:12px;padding:7px 0;border-bottom:1px solid #0f1e30;align-items:flex-start}
.g-row:last-child{border-bottom:none}
.g-icon{font-size:1.1em;min-width:24px}
.g-text{color:#8aadd4}
.g-text strong{color:#dde8ff}
.kpi-table{width:100%;border-collapse:collapse;margin-top:8px;font-size:.9em}
.kpi-table th{background:#0d1e36;color:#7fd1fc;padding:8px 12px;text-align:left;font-family:'Orbitron',sans-serif;font-size:.7em;letter-spacing:1px}
.kpi-table td{padding:8px 12px;border-bottom:1px solid #0f1e30;color:#8aadd4}
.kpi-table tr:last-child td{border-bottom:none}
.kpi-table .good{color:#00c853}
.kpi-table .bad{color:#e74c3c}

/* ── WHATSAPP PANEL ── */
.wa-panel{
  width:100%;max-width:1920px;margin:32px auto 0;
  padding:0 16px;
}
.wa-header{
  display:flex;align-items:center;gap:12px;
  background:#075e54;
  padding:14px 20px;border-radius:14px 14px 0 0;
}
.wa-avatar{
  width:42px;height:42px;border-radius:50%;
  background:#25d366;display:flex;align-items:center;justify-content:center;
  font-size:1.3em;flex-shrink:0;
}
.wa-title{font-family:'Orbitron',sans-serif;font-size:.85em;color:#fff;letter-spacing:1px}
.wa-sub{font-size:.7em;color:#a8d5c2;margin-top:2px}
.wa-status{margin-left:auto;display:flex;align-items:center;gap:6px;font-size:.7em;color:#a8d5c2}
.wa-dot{width:8px;height:8px;border-radius:50%;background:#25d366;animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}

.wa-body{
  background:#0b141a;
  background-image: url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.02'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
  min-height:480px;max-height:600px;overflow-y:auto;
  padding:20px 16px;
  border-left:1px solid #1f2c33;border-right:1px solid #1f2c33;
  scroll-behavior:smooth;
}
@media(min-width:1200px){.wa-body{min-height:560px;max-height:680px}}
@media(min-width:1920px){.wa-body{min-height:640px;max-height:760px}}

.wa-date-divider{
  text-align:center;margin:12px 0;
}
.wa-date-divider span{
  background:#1f2c33;color:#8696a0;font-size:.7em;padding:4px 12px;border-radius:8px;
}
.wa-bubble-wrap{display:flex;margin:3px 0;padding:0 4px}
.wa-bubble-wrap.from-me{justify-content:flex-end}
.wa-bubble-wrap.from-them{justify-content:flex-start}

.wa-bubble{
  max-width:65%;min-width:120px;
  padding:8px 12px 6px;border-radius:8px;
  position:relative;word-break:break-word;
  box-shadow:0 1px 2px rgba(0,0,0,.4);
}
@media(max-width:600px){.wa-bubble{max-width:88%}}
@media(min-width:1920px){.wa-bubble{max-width:45%}}

.wa-bubble.from-them{
  background:#202c33;
  border-top-left-radius:2px;
}
.wa-bubble.from-me{
  background:#005c4b;
  border-top-right-radius:2px;
}
.wa-sender{
  font-size:.72em;font-weight:bold;color:#25d366;
  margin-bottom:3px;display:block;
}
.wa-text{font-size:.88em;color:#e9edef;line-height:1.5}
.wa-meta{
  display:flex;justify-content:flex-end;align-items:center;
  gap:4px;margin-top:4px;
}
.wa-time{font-size:.67em;color:#8696a0}
.wa-tick{font-size:.75em;color:#53bdeb}

.wa-empty{
  text-align:center;padding:80px 20px;color:#3a4a54;
  font-size:.85em;
}
.wa-empty-icon{font-size:3em;display:block;margin-bottom:12px;opacity:.3}

/* ── QR SCAN ── */
.wa-qr-wrap{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:40px 20px;min-height:420px}
.wa-qr-title{font-family:'Orbitron',sans-serif;font-size:.9em;color:#25d366;letter-spacing:2px;margin-bottom:8px}
.wa-qr-sub{font-size:.75em;color:#8696a0;margin-bottom:24px;text-align:center}
.wa-qr-img{width:240px;height:240px;border-radius:12px;border:4px solid #25d366;padding:8px;background:#fff;box-shadow:0 0 30px rgba(37,211,102,.3)}
.wa-qr-note{font-size:.68em;color:#3a4a54;margin-top:16px;text-align:center}

.wa-footer{
  background:#1f2c33;padding:10px 16px;
  border-radius:0 0 14px 14px;
  border:1px solid #2a3942;border-top:none;
  display:flex;align-items:center;gap:10px;
}
.wa-readonly{color:#8696a0;font-size:.75em;font-style:italic}
.wa-refresh{background:#075e54;color:#fff;border:none;border-radius:20px;padding:6px 16px;font-size:.72em;cursor:pointer;font-family:'Share Tech Mono',monospace}
.wa-refresh:hover{background:#128c7e}
.toast{position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;font-size:.82em;z-index:999;animation:sli .3s ease;box-shadow:0 4px 20px rgba(0,0,0,.5);max-width:300px}
.t-ok{background:#00c853;color:#001a0e;font-weight:bold}
.t-err{background:#c0392b;color:#fff}
@keyframes sli{from{opacity:0;transform:translateX(30px)}to{opacity:1;transform:translateX(0)}}
@media(max-width:480px){h1{font-size:1.3em}.net-link{font-size:.95em}.importance-text{font-size:.7em}}
</style>
</head>
<body>

<!-- ══════════════════════════════════════════════ -->
<!--           ⚠️  IMPORTANCE BANNER               -->
<!-- ══════════════════════════════════════════════ -->
<div class="importance">
  <div class="importance-text">
    <span class="importance-icon">⚠️</span>
    <strong>MANDATORY — ALL STAFF MUST CLOCK IN AND OUT EVERY SHIFT</strong>
    <br>
    Missing a punch = Incomplete shift on record · Late punch = Marked as late · No exceptions
    <br>
    <strong>This data is reviewed by management weekly and directly affects your KPI score</strong>
  </div>
</div>

<!-- ══════════════════════════════════════════════ -->
<!--                   HEADER                      -->
<!-- ══════════════════════════════════════════════ -->
<div class="header">
  <h1>🌙 AFAQ ATTENDANCE</h1>
  <div class="sub">RAMADAN KAREEM — KPI TRACKER</div>
  <div class="clock">{{ now_time }}</div>
  <div class="date-str">{{ today_date }}</div>

  <div class="net-banner">
    <div class="net-label">📡 OPEN THIS LINK ON YOUR PHONE OR ANY PC</div>
    <div class="net-link">http://{{ local_ip }}:{{ port }}</div>
    <div class="net-sub">Works on any device connected to the office WiFi · Chrome, Safari, any browser</div>
  </div>

  <div class="note">🟢 Buttons turn GREEN only within ±15 minutes of your shift time — locked outside that window · Auto-refresh every 30s</div>
</div>

<!-- ══════════════════════════════════════════════ -->
<!--               PUNCH CARDS                     -->
<!-- ══════════════════════════════════════════════ -->
<div class="grid">
{% for emp in employees %}
<div class="card">
  <div class="emp">{{ emp.name }}</div>
  {% for s in emp.shifts %}
    {% if s.active %}
    <form method="POST" style="margin:0">
      <input type="hidden" name="employee" value="{{ emp.name }}">
      <input type="hidden" name="label" value="{{ s.label }}">
      <input type="hidden" name="time" value="{{ s.time }}">
      {% if s.is_in %}
        <button type="submit" class="btn-on">▶ {{ s.label }}<br><span style="opacity:.7;font-size:.85em">{{ s.time }}</span></button>
        <label class="ot-box">
          <input type="checkbox" name="overtime" value="1">
          <span class="ot-label">🕐 Staying extra today?<br><strong>+1 hr overtime</strong> (out window +1h15)</span>
        </label>
      {% elif s.is_out %}
        {% if s.overtime_active %}<div class="ot-active">⏱ OVERTIME ACTIVE — window extended +1hr</div>{% endif %}
        <button type="submit" class="btn-on">▶ {{ s.label }}<br><span style="opacity:.7;font-size:.85em">{{ s.time }}{% if s.overtime_active %} +1hr{% endif %}</span></button>
      {% else %}
        <button type="submit" class="btn-on">▶ {{ s.label }}<br><span style="opacity:.7;font-size:.85em">{{ s.time }}</span></button>
      {% endif %}
    </form>
    {% else %}
    <button class="btn-off" disabled>⬛ {{ s.label }}<br><span style="font-size:.85em">{{ s.time }}</span></button>
    {% endif %}
  {% endfor %}
</div>
{% endfor %}
</div>

<!-- ══════════════════════════════════════════════ -->
<!--               TODAY'S LOG                     -->
<!-- ══════════════════════════════════════════════ -->
<div class="logs-wrap">
  <div class="logs-hdr">
    <div class="logs-title">TODAY — {{ today_logs|length }} PUNCH{% if today_logs|length != 1 %}ES{% endif %}</div>
    <a href="/export" class="export-btn">⬇ Export CSV</a>
  </div>
  <div class="log-box">
    {% if today_logs %}
      {% for l in today_logs %}
      <div class="log-row">
        <span class="l-emp">{{ l.employee }}</span>
        <span class="l-event">{{ l.label }}</span>
        <span class="l-sched">→ {{ l.scheduled }}</span>
        {% if l.overtime_declared %}<span style="color:#f0c040;font-size:.8em">⏱+1hr</span>{% endif %}
        <span class="l-time">{{ l.timestamp }}</span>
      </div>
      {% endfor %}
    {% else %}
      <div class="empty">No punches yet today.</div>
    {% endif %}
  </div>
</div>

<!-- ══════════════════════════════════════════════ -->
<!--         📱 WHATSAPP BROADCAST PANEL          -->
<!-- ══════════════════════════════════════════════ -->
<div class="wa-panel">
  <div class="wa-header">
    <div class="wa-avatar">🏢</div>
    <div>
      <div class="wa-title">AFAQ ALNASEEM</div>
      <div class="wa-sub">Business WhatsApp — Staff Broadcast</div>
    </div>
    <div class="wa-status">
      <div class="wa-dot" id="waDot"></div>
      <span id="waStatus">connecting...</span>
    </div>
  </div>

  <div class="wa-body" id="waBody">
    {% if qr_state.state == 'waiting_scan' and qr_state.qr %}
      <!-- ── QR SCAN SCREEN ── -->
      <div class="wa-qr-wrap">
        <div class="wa-qr-title">📱 Scan to connect WhatsApp</div>
        <div class="wa-qr-sub">Open WhatsApp Business → ⋮ → Linked Devices → Link a Device</div>
        <img src="{{ qr_state.qr }}" class="wa-qr-img" alt="WhatsApp QR Code">
        <div class="wa-qr-note">QR refreshes automatically · Page auto-reloads every 15s</div>
      </div>
    {% elif qr_state.state == 'connected' and wa_messages %}
      {% set ns = namespace(last_date='') %}
      {% for m in wa_messages %}
        {% if m.date != ns.last_date %}
          <div class="wa-date-divider"><span>{{ m.date }}</span></div>
          {% set ns.last_date = m.date %}
        {% endif %}
        <div class="wa-bubble-wrap {{ 'from-me' if m.from_me else 'from-them' }}">
          <div class="wa-bubble {{ 'from-me' if m.from_me else 'from-them' }}">
            {% if not m.from_me %}<span class="wa-sender">{{ m.sender }}</span>{% endif %}
            <span class="wa-text">{{ m.body }}</span>
            <div class="wa-meta">
              <span class="wa-time">{{ m.timestamp }}</span>
              {% if m.from_me %}<span class="wa-tick">✓✓</span>{% endif %}
            </div>
          </div>
        </div>
      {% endfor %}
    {% elif qr_state.state == 'connected' %}
      <div class="wa-empty">
        <span class="wa-empty-icon">✅</span>
        WhatsApp connected.<br>No messages yet — they will appear here as they arrive.
      </div>
    {% else %}
      <div class="wa-empty">
        <span class="wa-empty-icon">⏳</span>
        Starting WhatsApp bridge...<br>QR code will appear here in a few seconds.
      </div>
    {% endif %}
  </div>

  <div class="wa-footer">
    <span class="wa-readonly">🔒 Read-only — Messages from Afaq Business WhatsApp</span>
    <button class="wa-refresh" onclick="location.reload()">↻ Refresh</button>
  </div>
</div>

<!-- ══════════════════════════════════════════════ -->
<!-- ══════════════════════════════════════════════ -->
<div class="guide">
  <button class="guide-toggle" onclick="toggleGuide()">
    <span>📖 &nbsp; HOW IT WORKS — GUIDE & HELP</span>
    <span id="arrow">▼</span>
  </button>
  <div class="guide-body" id="guideBody">

    <!-- HOW TO USE -->
    <div class="g-section">
      <div class="g-title">✅ HOW TO CLOCK IN / OUT</div>
      <div class="g-row"><span class="g-icon">1️⃣</span><span class="g-text">Open <strong>http://{{ local_ip }}:{{ port }}</strong> on your phone or PC — any browser works</span></div>
      <div class="g-row"><span class="g-icon">2️⃣</span><span class="g-text">Find <strong>your name</strong> on the screen</span></div>
      <div class="g-row"><span class="g-icon">3️⃣</span><span class="g-text">When your shift time is near, your button turns <strong style="color:#00c853">GREEN</strong> — press it immediately</span></div>
      <div class="g-row"><span class="g-icon">4️⃣</span><span class="g-text">You will see a <strong style="color:#00c853">green confirmation</strong> message. That means it's saved. Done.</span></div>
      <div class="g-row"><span class="g-icon">5️⃣</span><span class="g-text">Do this <strong>4 times per day</strong>: Morning In, Morning Out, Evening In, Evening Out</span></div>
    </div>

    <!-- OVERTIME -->
    <div class="g-section">
      <div class="g-title">⏱ OVERTIME — STAYING EXTRA</div>
      <div class="g-row"><span class="g-icon">🕐</span><span class="g-text">When clocking <strong>IN</strong>, tick <strong style="color:#f0c040">"Staying extra today? +1hr overtime"</strong> if you plan to stay longer</span></div>
      <div class="g-row"><span class="g-icon">📌</span><span class="g-text">This extends your <strong>OUT window</strong> from 15 min to <strong>1 hour and 15 minutes</strong> after your scheduled finish</span></div>
      <div class="g-row"><span class="g-icon">🟡</span><span class="g-text">Example: Morning Out 15:30 — normal closes 15:45. With overtime: closes at <strong>16:45</strong></span></div>
      <div class="g-row"><span class="g-icon">⚠️</span><span class="g-text">Must be declared <strong>at clock-in</strong>. Cannot be added after the fact.</span></div>
    </div>

    <!-- SHIFT TIMES -->
    <div class="g-section">
      <div class="g-title">🕐 RAMADAN SHIFT TIMES</div>
      <div class="g-row"><span class="g-icon">👥</span><span class="g-text"><strong>Hafiz / Mehriban / Nader:</strong><br>Morning 10:00 → 15:30 &nbsp;|&nbsp; Evening 19:30 → 22:30</span></div>
      <div class="g-row"><span class="g-icon">👤</span><span class="g-text"><strong>Mr. Masoumi:</strong><br>Morning 09:00 → 14:30 &nbsp;|&nbsp; Evening 16:00 → 19:00</span></div>
    </div>

    <!-- WINDOW EXPLAINED -->
    <div class="g-section">
      <div class="g-title">⏰ THE 15-MINUTE WINDOW</div>
      <div class="g-row"><span class="g-icon">🟢</span><span class="g-text">Button is <strong>GREEN and clickable</strong> from 15 minutes before to 15 minutes after your shift time</span></div>
      <div class="g-row"><span class="g-icon">⬛</span><span class="g-text">Button is <strong>GREY and locked</strong> outside that window — cannot be pressed even if you try</span></div>
      <div class="g-row"><span class="g-icon">📌</span><span class="g-text">Example: Morning In at 10:00 → button is green from <strong>09:45 to 10:15 only</strong></span></div>
      <div class="g-row"><span class="g-icon">🔒</span><span class="g-text">The lock is enforced by the server — <strong>it cannot be bypassed or faked</strong></span></div>
    </div>

    <!-- KPI IMPACT -->
    <div class="g-section">
      <div class="g-title">📊 HOW THIS AFFECTS YOUR KPI</div>
      <table class="kpi-table">
        <tr><th>Action</th><th>KPI Impact</th></tr>
        <tr><td>✅ All 4 punches on time</td><td class="good">Full score — Perfect attendance</td></tr>
        <tr><td>⏱ Punched but late (after window)</td><td class="bad">Cannot punch — recorded as absent</td></tr>
        <tr><td>❌ Missed a punch</td><td class="bad">Incomplete shift — negative mark</td></tr>
        <tr><td>📅 Present every day</td><td class="good">Consistency bonus on weekly review</td></tr>
        <tr><td>🚫 Missing multiple days</td><td class="bad">Serious KPI deduction</td></tr>
      </table>
      <div class="g-row" style="margin-top:10px"><span class="g-icon">📋</span><span class="g-text">Management exports and reviews all data <strong>every week</strong>. Every punch is timestamped and cannot be edited.</span></div>
    </div>

    <!-- TROUBLESHOOTING -->
    <div class="g-section">
      <div class="g-title">🔧 TROUBLESHOOTING</div>
      <div class="g-row"><span class="g-icon">📵</span><span class="g-text"><strong>Can't open the link on phone?</strong> Make sure your phone is on the <strong>office WiFi</strong>, not mobile data</span></div>
      <div class="g-row"><span class="g-icon">🔘</span><span class="g-text"><strong>Button is grey?</strong> You are outside the 15-minute window. Wait until your shift time approaches.</span></div>
      <div class="g-row"><span class="g-icon">🌐</span><span class="g-text"><strong>Page not loading?</strong> The server PC must be on. Ask the manager to check if the app is running.</span></div>
      <div class="g-row"><span class="g-icon">🔄</span><span class="g-text"><strong>Page looks old?</strong> Pull down to refresh on phone or press F5 on PC. It auto-refreshes every 30 seconds.</span></div>
      <div class="g-row"><span class="g-icon">✅</span><span class="g-text"><strong>How do I know it worked?</strong> A <strong style="color:#00c853">green message</strong> appears at the top right with your name and time.</span></div>
    </div>

  </div><!-- end guide-body -->
</div><!-- end guide -->

{% if message %}
<div class="toast {{ 't-ok' if not error else 't-err' }}">{{ message }}</div>
<script>setTimeout(()=>{var t=document.querySelector('.toast');if(t)t.remove()},4000)</script>
{% endif %}

<script>
function toggleGuide(){
  var b=document.getElementById('guideBody');
  var a=document.getElementById('arrow');
  b.classList.toggle('open');
  a.textContent=b.classList.contains('open')?'▲':'▼';
}

(function(){
  // Scroll WA panel to bottom
  var body = document.getElementById('waBody');
  if(body) body.scrollTop = body.scrollHeight;

  var lastMsgCount = document.querySelectorAll('.wa-bubble').length;
  var isWaitingQR  = document.querySelector('.wa-qr-img') !== null;

  // Poll faster when waiting for QR scan (5s), slower when connected (8s)
  var pollInterval = isWaitingQR ? 5000 : 8000;

  setInterval(function(){
    fetch('/api/qr')
      .then(r => r.json())
      .then(function(qs){
        var dot    = document.getElementById('waDot');
        var status = document.getElementById('waStatus');

        if(qs.state === 'connected'){
          dot.style.background = '#25d366';
          status.textContent   = 'connected';
          // If we were on QR screen, reload to show messages
          if(isWaitingQR){ location.reload(); return; }
        } else if(qs.state === 'waiting_scan'){
          dot.style.background = '#f0c040';
          status.textContent   = 'scan QR';
          // Reload to get fresh QR image
          if(!isWaitingQR){ location.reload(); return; }
          // Refresh QR image in place if already showing
          var img = document.querySelector('.wa-qr-img');
          if(img && qs.qr && qs.qr !== img.src){ img.src = qs.qr; }
        } else {
          dot.style.background = '#e74c3c';
          status.textContent   = 'bridge starting...';
        }
      })
      .catch(function(){
        var dot = document.getElementById('waDot');
        dot.style.background = '#f0c040';
        document.getElementById('waStatus').textContent = 'reconnecting...';
      });

    // Check for new messages
    fetch('/api/messages')
      .then(r => r.json())
      .then(function(msgs){
        if(msgs.length !== lastMsgCount){
          lastMsgCount = msgs.length;
          location.reload();
        }
      }).catch(()=>{});

  }, pollInterval);
})();
</script>
</body>
</html>
"""

@app.route('/', methods=['GET', 'POST'])
def index():
    message, error = None, False
    today = datetime.now().strftime("%Y-%m-%d")

    if request.method == 'POST':
        emp_name = request.form.get('employee')
        label    = request.form.get('label')
        time     = request.form.get('time')
        overtime = request.form.get('overtime') == '1'

        session   = SESSION_MAP.get(label, "")
        is_out    = label.endswith("Out")
        ot_active = is_out and has_overtime(emp_name, today, session)
        after_m   = 75 if ot_active else 15

        if is_within_window(time, before_mins=15, after_mins=after_m):
            entry = {
                "date":              today,
                "timestamp":         datetime.now().strftime("%H:%M:%S"),
                "employee":          emp_name,
                "label":             label,
                "scheduled":         time,
                "overtime_declared": overtime,
                "status":            "OK"
            }
            save_entry(entry)
            if overtime and label.endswith("In"):
                declare_overtime(emp_name, today, session)
            ot_note = " (⏱ overtime +1hr declared)" if overtime else ""
            message = f"✅ {emp_name} — {label} logged at {entry['timestamp']}{ot_note}"
        else:
            message = f"❌ Outside window for {label} ({time})"
            error = True

    emp_data = []
    for e in EMPLOYEES:
        shifts = []
        for s in SCHEDULES[e["type"]]:
            lbl     = s["label"]
            session = SESSION_MAP.get(lbl, "")
            is_in   = lbl.endswith("In")
            is_out  = lbl.endswith("Out")
            ot_flag = is_out and has_overtime(e["name"], today, session)
            after_m = 75 if ot_flag else 15
            active  = is_within_window(s["time"], before_mins=15, after_mins=after_m)
            shifts.append({
                "label":           lbl,
                "time":            s["time"],
                "active":          active,
                "is_in":           is_in,
                "is_out":          is_out,
                "overtime_active": ot_flag,
            })
        emp_data.append({"name": e["name"], "shifts": shifts})

    return render_template_string(HTML,
        employees=emp_data,
        today_logs=get_today_logs(),
        wa_messages=get_messages(50),
        qr_state=get_qr_state(),
        now_time=datetime.now().strftime("%H:%M:%S"),
        today_date=datetime.now().strftime("%A, %d %B %Y"),
        local_ip=LOCAL_IP,
        port=PORT,
        message=message,
        error=error)

@app.route('/export')
def export():
    logs = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            try: logs = json.load(f)
            except: pass
    lines = ["Date,Time,Employee,Event,Scheduled,Overtime,Status"]
    for l in logs:
        ot = "YES" if l.get("overtime_declared") else "NO"
        lines.append(f"{l.get('date','')},{l.get('timestamp','')},{l.get('employee','')},{l.get('label','')},{l.get('scheduled','')},{ot},{l.get('status','')}")
    return Response("\n".join(lines), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=afaq_attendance.csv"})

@app.route('/api/messages')
def api_messages():
    from flask import jsonify
    return jsonify(get_messages(50))

@app.route('/api/qr')
def api_qr():
    from flask import jsonify
    return jsonify(get_qr_state())

def open_browser():
    import time; time.sleep(1.5)
    webbrowser.open(f"http://{LOCAL_IP}:{PORT}")

import signal

def graceful_exit(signum, frame):
    print("\n  [Ctrl+C] Caught interrupt — revoking firewall rule before exit...")
    revoke_firewall()
    print("  [Ctrl+C] Port cleared. Goodbye.")
    os._exit(0)

if __name__ == '__main__':
    signal.signal(signal.SIGINT,  graceful_exit)
    signal.signal(signal.SIGTERM, graceful_exit)
    print("\n" + "="*54)
    print("  🌙 AFAQ ATTENDANCE — Ramadan KPI")
    print(f"  Local:   http://localhost:{PORT}")
    print(f"  Network: http://{LOCAL_IP}:{PORT}  ← share this")
    print("="*54 + "\n")
    threading.Thread(target=open_firewall,            daemon=True).start()
    threading.Thread(target=daily_shutdown,           daemon=True).start()
    threading.Thread(target=ask_startup_confirmation, daemon=True).start()
    threading.Thread(target=start_bridge,             daemon=True).start()
    threading.Thread(target=open_browser,             daemon=True).start()
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
