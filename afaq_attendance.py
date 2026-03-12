import json, os, sys, threading, webbrowser, socket, platform, subprocess
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, Response

app = Flask(__name__)
PORT = 3456
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
DATA_FILE = os.path.join(BASE_DIR, 'attendance_data.json')
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

def open_firewall():
    if platform.system() != "Windows":
        return
    try:
        subprocess.run(['netsh','advfirewall','firewall','delete','rule','name=AfaqAttendance'], capture_output=True)
        result = subprocess.run(
            ['netsh','advfirewall','firewall','add','rule',
             'name=AfaqAttendance','dir=in','action=allow',
             'protocol=TCP',f'localport={PORT}','profile=private,domain'],
            capture_output=True, text=True)
        status = "✅ opened" if result.returncode == 0 else "⚠️ failed (run as Admin)"
        print(f"  [Firewall] Port {PORT} {status}")
    except Exception as e:
        print(f"  [Firewall] Error: {e}")

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

def is_within_window(t, mins=15):
    now = datetime.now()
    h, m = map(int, t.split(":"))
    target = datetime.combine(now.date(), datetime.min.time().replace(hour=h, minute=m))
    return (target - timedelta(minutes=mins)) <= now <= (target + timedelta(minutes=mins))

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

/* ── TOAST ── */
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
      <button type="submit" class="btn-on">▶ {{ s.label }}<br><span style="opacity:.7;font-size:.85em">{{ s.time }}</span></button>
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
        <span class="l-time">{{ l.timestamp }}</span>
      </div>
      {% endfor %}
    {% else %}
      <div class="empty">No punches yet today.</div>
    {% endif %}
  </div>
</div>

<!-- ══════════════════════════════════════════════ -->
<!--            📖 GUIDE & HELP SECTION            -->
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
</script>
</body>
</html>
"""

@app.route('/', methods=['GET', 'POST'])
def index():
    message, error = None, False
    if request.method == 'POST':
        emp_name = request.form.get('employee')
        label    = request.form.get('label')
        time     = request.form.get('time')
        if is_within_window(time):
            entry = {
                "date":      datetime.now().strftime("%Y-%m-%d"),
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "employee":  emp_name,
                "label":     label,
                "scheduled": time,
                "status":    "OK"
            }
            save_entry(entry)
            message = f"✅ {emp_name} — {label} logged at {entry['timestamp']}"
        else:
            message = f"❌ Outside 15-min window for {label} ({time})"
            error = True

    emp_data = []
    for e in EMPLOYEES:
        shifts = [{"label": s["label"], "time": s["time"], "active": is_within_window(s["time"])}
                  for s in SCHEDULES[e["type"]]]
        emp_data.append({"name": e["name"], "shifts": shifts})

    return render_template_string(HTML,
        employees=emp_data,
        today_logs=get_today_logs(),
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
    lines = ["Date,Time,Employee,Event,Scheduled,Status"]
    for l in logs:
        lines.append(f"{l.get('date','')},{l.get('timestamp','')},{l.get('employee','')},{l.get('label','')},{l.get('scheduled','')},{l.get('status','')}")
    return Response("\n".join(lines), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=afaq_attendance.csv"})

def open_browser():
    import time; time.sleep(1.5)
    webbrowser.open(f"http://{LOCAL_IP}:{PORT}")

if __name__ == '__main__':
    print("\n" + "="*54)
    print("  🌙 AFAQ ATTENDANCE — Ramadan KPI")
    print(f"  Local:   http://localhost:{PORT}")
    print(f"  Network: http://{LOCAL_IP}:{PORT}  ← share this")
    print("="*54 + "\n")
    threading.Thread(target=open_firewall,            daemon=True).start()
    threading.Thread(target=ask_startup_confirmation, daemon=True).start()
    threading.Thread(target=open_browser,             daemon=True).start()
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
