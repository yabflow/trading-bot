import json
import os
import signal
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import state

PORT = int(os.getenv("WEB_PORT", "8080"))
BASE_DIR = os.path.dirname(__file__)
BOT_PID = None
BOT_LOG_TAIL = []

INDEX_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading Bot Control</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0f1115;color:#e6e6e6;padding:16px;max-width:820px;margin:0 auto}
h1{font-size:1.15em;margin-bottom:4px}
.sub{font-size:0.8em;color:#8a8f98;margin-bottom:14px}
.card{background:#1a1d24;border-radius:10px;padding:14px;margin-bottom:12px}
h3{font-size:0.95em;margin-bottom:8px;color:#c0c4cc}
.row{display:flex;gap:10px;flex-wrap:wrap}
.col{flex:1;min-width:120px}
.label{font-size:0.72em;color:#8a8f98;margin-bottom:4px}
.value{font-size:1.25em;font-weight:600}
.green{color:#2ecc71}.red{color:#e74c3c}.yellow{color:#f1c40f}.dim{color:#8a8f98}
.btn{display:inline-block;padding:11px 16px;border:none;border-radius:8px;font-size:0.85em;cursor:pointer;margin:0 6px 6px 0;font-weight:600}
.btn-start{background:#2ecc71;color:#0f1115}
.btn-stop{background:#e74c3c;color:#fff}
.btn-sell{background:#f39c12;color:#0f1115}
.btn-export{background:#3498db;color:#fff}
.btn:disabled{opacity:0.4;cursor:not-allowed}
table{width:100%;border-collapse:collapse;font-size:0.8em}
th,td{padding:7px;text-align:left;border-bottom:1px solid #2a2d35}
th{color:#8a8f98;font-weight:500}
.log{max-height:260px;overflow-y:auto;font-size:0.78em;font-family:monospace}
.scroll{max-height:260px;overflow-y:auto}
.log div{padding:3px 0;border-bottom:1px solid #22252c}
.badge{display:inline-block;padding:4px 12px;border-radius:20px;font-size:0.75em;font-weight:700}
.badge-run{background:#2ecc71;color:#0f1115}.badge-stop{background:#e74c3c;color:#fff}
.badge-dry{background:#f1c40f;color:#0f1115;margin-left:6px}
.badge-live{background:#2ecc71;color:#0f1115;margin-left:6px}
.statbox{background:#22262e;border-radius:8px;padding:10px}
.alert{background:#5c1a1a;border:2px solid #e74c3c;border-radius:10px;padding:14px;margin-bottom:12px;animation:pulse 1.2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.7}}
.alert h3{color:#ff6b6b;font-size:1em}
.nav{display:flex;gap:6px;margin-bottom:14px;border-bottom:1px solid #2a2d35;padding-bottom:10px}
.nav a{padding:8px 16px;border-radius:8px;color:#8a8f98;text-decoration:none;font-size:0.9em;font-weight:600}
.nav a.active{background:#3498db;color:#fff}
.page{display:none}
.page.active{display:block}
input{width:100%;padding:10px;border-radius:8px;border:1px solid #333;background:#0f1115;color:#e6e6e6;font-size:0.9em}
</style>
</head>
<body>
<h1>📈 Trading Bot Control</h1>
<div class="sub">Dashboard selalu aktif — bot dikontrol dari sini</div>

<div class="nav">
<a href="#" class="active" onclick="showPage('dash',this)">📊 Dashboard</a>
<a href="#" onclick="showPage('settings',this)">⚙️ Pengaturan AI</a>
</div>

<div id="page-dash" class="page active">

<div id="alertBox" style="display:none" class="alert">
<h3>⚠️ PERINGATAN KERAS</h3>
<div id="alertText"></div>
</div>

<div class="card">
<h3>Status Bot</h3>
<div style="margin-bottom:8px">
<span id="statusBadge" class="badge badge-stop">STOPPED</span>
<span id="dryBadge" class="badge badge-dry" style="display:none">DRY-RUN (AMAN)</span>
<span id="liveBadge" class="badge badge-live" style="display:none">LIVE (DANA ASLI)</span>
</div>
<div class="row">
<div class="col"><div class="statbox"><div class="label">Saldo (USDT)</div><div class="value" id="balance">-</div></div></div>
<div class="col"><div class="statbox"><div class="label">P&L Hari Ini</div><div class="value" id="pnl">-</div></div></div>
<div class="col"><div class="statbox"><div class="label">Risk Aktif</div><div class="value" id="risk">-</div></div></div>
<div class="col"><div class="statbox"><div class="label">Loss Beruntun</div><div class="value" id="losses">-</div></div></div>
</div>
</div>

<div class="card">
<h3>Kontrol</h3>
<button class="btn btn-start" id="btnStart" onclick="action('start')">▶ START BOT</button>
<button class="btn btn-stop" id="btnStop" onclick="action('stop')">⏹ STOP (sell semua)</button>
<button class="btn btn-sell" id="btnSell" onclick="action('sell')">💰 SELL SEMUA POSISI</button>
<button class="btn btn-export" onclick="doExport()">📄 Export Riwayat</button>
</div>

<div class="card">
<h3>🔍 Analisis Pasar & Kandidat</h3>
<button class="btn btn-export" onclick="loadMarket()">🔄 Refresh Analisis</button>
<div id="marketBox" style="margin-top:8px;font-size:0.85em">Klik Refresh untuk analisis...</div>
<div style="margin-top:12px">
<div class="label" style="margin-bottom:6px">🎯 Kandidat Top 20 (Scanner + AI)</div>
<table><thead><tr><th>Coin</th><th>Harga</th><th>24h</th><th>Skor</th><th>Alasan</th><th>AI</th></tr></thead>
<tbody id="candTable"></tbody></table>
</div>
</div>

<div class="card">
<h3>Posisi</h3>
<div id="position">Tidak ada posisi</div>
</div>

<div class="card">
<h3>Riwayat Per Hari</h3>
<div class="scroll"><table><thead><tr><th>Tanggal</th><th>Trade</th><th>P&L %</th><th>P&L USDT</th><th>Saldo</th><th></th></tr></thead>
<tbody id="history"></tbody></table></div>
</div>

<div class="card">
<h3>Log Bot</h3>
<div class="log" id="log"></div>
</div>
</div>

<div id="page-settings" class="page">
<div class="card">
<h3>⚙️ Konfigurasi AI</h3>
<div class="sub" style="margin-bottom:10px">Ubah provider/reseller atau model AI. Config di-test dulu sebelum disimpan.</div>
<div class="row" style="margin-bottom:10px">
<div class="col" style="flex:2;min-width:220px"><div class="label">Base URL</div><input id="cfgBase" placeholder="https://api.provider.com/v1"></div>
</div>
<div class="row" style="margin-bottom:10px">
<div class="col" style="flex:2;min-width:220px"><div class="label">API Key</div><input id="cfgKey" type="password" placeholder="sk-..."></div>
</div>
<div class="row" style="margin-bottom:14px">
<div class="col" style="flex:2;min-width:220px"><div class="label">Model</div><input id="cfgModel" placeholder="model-name"></div>
</div>
<button class="btn btn-export" onclick="saveConfig()">💾 Simpan & Test Config</button>
<div id="cfgMsg" style="margin-top:10px;font-size:0.85em"></div>
</div>
</div>

<script>
function showPage(name,el){
document.querySelectorAll('.nav a').forEach(a=>a.classList.remove('active'));
el.classList.add('active');
document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
document.getElementById('page-'+name).classList.add('active');
if(name==='settings')loadConfig();
}
function fmt(n){return n==null?"-":Number(n).toFixed(2)}
async function fetchStatus(){
try{
const r=await fetch('/status');const d=await r.json();
const running=d.running;
document.getElementById('statusBadge').textContent=running?'RUNNING':'STOPPED';
document.getElementById('statusBadge').className='badge '+(running?'badge-run':'badge-stop');
document.getElementById('btnStart').disabled=running;
document.getElementById('btnStop').disabled=!running;
document.getElementById('btnSell').disabled=!running;

if(d.dry_run){
document.getElementById('dryBadge').style.display='inline-block';
document.getElementById('liveBadge').style.display='none';
}else{
document.getElementById('dryBadge').style.display='none';
document.getElementById('liveBadge').style.display='inline-block';
}

document.getElementById('balance').textContent=fmt(d.balance);
const pnlEl=document.getElementById('pnl');
pnlEl.textContent=(d.daily_pnl_pct>=0?'+':'')+(d.daily_pnl_pct*100).toFixed(2)+'% ('+fmt(d.daily_pnl_usdt)+')';
pnlEl.className='value '+(d.daily_pnl_pct>=0?'green':'red');
document.getElementById('risk').textContent=(d.risk_pct*100).toFixed(2)+'%';
document.getElementById('losses').textContent=d.consecutive_losses;

if(d.position){
document.getElementById('position').innerHTML=
'<div class="row">'+
'<div class="col"><div class="label">Sisi</div><div class="value">'+d.position.side+'</div></div>'+
'<div class="col"><div class="label">Qty</div><div class="value">'+d.position.qty+'</div></div>'+
'<div class="col"><div class="label">Entry</div><div class="value">'+fmt(d.position.entry)+'</div></div>'+
'<div class="col"><div class="label">Puncak</div><div class="value">'+fmt(d.highest_price)+'</div></div>'+
'<div class="col"><div class="label">Trailing</div><div class="value">'+(d.trailing_pct*100).toFixed(2)+'%</div></div>'+
'</div>';
}else{document.getElementById('position').textContent='Tidak ada posisi'}

let hh='';
const hist=d.history||[];
for(let i=hist.length-1;i>=0;i--){const h=hist[i];
const pct=(h.daily_pnl_pct*100);
hh+='<tr><td>'+h.date+'</td><td>'+h.total_trades+'</td>'+
'<td class="'+(pct>=0?'green':'red')+'">'+(pct>=0?'+':'')+pct.toFixed(2)+'%</td>'+
'<td class="'+(h.daily_pnl_usdt>=0?'green':'red')+'">'+fmt(h.daily_pnl_usdt)+'</td>'+
'<td>'+fmt(h.balance)+'</td>'+
'<td><a href="/download?date='+h.date+'" style="color:#3498db">⬇</a></td></tr>';}
document.getElementById('history').innerHTML=hh;
renderCandidates(d);

let lg='';
const logs=d.log||[];
for(let i=logs.length-1;i>=0;i--){lg+='<div>'+logs[i].t+' '+logs[i].m+'</div>';}
document.getElementById('log').innerHTML=lg;

if(d.alert){
document.getElementById('alertBox').style.display='block';
document.getElementById('alertText').innerHTML=d.alert;
}else{
document.getElementById('alertBox').style.display='none';
}
}catch(e){document.getElementById('alertBox').style.display='block';document.getElementById('alertText').textContent='Gagal koneksi ke server: '+e}
}
async function action(a){
const r=await fetch('/action?cmd='+a);
const d=await r.json();
setTimeout(fetchStatus,800);
}
async function doExport(){
const r=await fetch('/action?cmd=export');
const d=await r.json();
alert(d.ok?'Export tersimpan: '+d.file:'Gagal: '+d.error);
}
async function loadConfig(){
try{
const r=await fetch('/config');const d=await r.json();
document.getElementById('cfgBase').value=d.base_url||'';
document.getElementById('cfgKey').value=d.api_key||'';
document.getElementById('cfgModel').value=d.model||'';
}catch(e){}
}
function renderCandidates(d){
const cands=d.candidates||[];
const aiRes=d.ai_results||[];
const aiMap={};
aiRes.forEach(a=>aiMap[a.symbol]=a);
let ch='';
for(let i=0;i<cands.length;i++){const c=cands[i];
const ai=aiMap[c.symbol];
let aiTxt='-';
if(ai){aiTxt='<span class="'+(ai.action=='buy'?'green':'dim')+'">'+ai.action.toUpperCase()+' '+ai.confidence+'</span><div class="dim" style="font-size:0.7em">'+ai.reason+'</div>';}
ch+='<tr><td>'+c.symbol+'</td><td>'+fmt(c.price)+'</td>'+
'<td class="'+(c.price24hPcnt>=0?'green':'red')+'">'+(c.price24hPcnt>=0?'+':'')+c.price24hPcnt.toFixed(2)+'%</td>'+
'<td>'+c.score+'</td><td>'+((c.reasons||[]).join(', '))+'</td><td>'+aiTxt+'</td></tr>';}
document.getElementById('candTable').innerHTML=ch;
}
async function saveConfig(){
const body={base_url:document.getElementById('cfgBase').value.trim(),api_key:document.getElementById('cfgKey').value.trim(),model:document.getElementById('cfgModel').value.trim()};
const msg=document.getElementById('cfgMsg');
msg.textContent='Menyimpan & test koneksi...';
msg.className='yellow';
const r=await fetch('/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const d=await r.json();
if(d.ok){msg.textContent='✅ '+d.msg;msg.className='green';}
else{msg.textContent='❌ '+d.msg;msg.className='red';}
}
async function loadMarket(){
const box=document.getElementById('marketBox');
box.innerHTML='<span class="dim">Menganalisis pasar...</span>';
try{
const r=await fetch('/market');const d=await r.json();
if(d.error){box.innerHTML='<span class="red">'+d.error+'</span>';return;}
const ind=d.indicators||{};
let html='<div class="row">';
html+='<div class="col"><div class="label">BTC/USDT</div><div class="value">'+fmt(ind.price)+'</div></div>';
html+='<div class="col"><div class="label">RSI</div><div class="value '+(ind.rsi>70?'red':ind.rsi<30?'green':'')+'">'+(ind.rsi??'-')+'</div></div>';
html+='<div class="col"><div class="label">EMA9/21</div><div class="value">'+(ind.ema9??'-')+' / '+(ind.ema21??'-')+'</div></div>';
html+='<div class="col"><div class="label">Trend</div><div class="value '+(ind.trend=='bullish'?'green':ind.trend=='bearish'?'red':'dim')+'">'+ind.trend+'</div></div>';
html+='<div class="col"><div class="label">24h</div><div class="value '+(ind.change_pct>=0?'green':'red')+'">'+(ind.change_pct??'-')+'%</div></div>';
html+='</div>';
if(d.ai&&d.ai.reason){
const act=d.ai.action||'';
html+='<div style="margin-top:10px;padding:10px;background:#22262e;border-radius:8px">';
html+='<span class="'+(act=='buy'?'green':'dim')+'">'+act.toUpperCase()+'</span> '+(d.ai.confidence||0)+' — '+d.ai.reason+'</div>';
}
html+='<div class="dim" style="margin-top:6px">BTC diperbarui '+d.time+'</div>';
box.innerHTML=html;
}catch(e){box.innerHTML='<span class="red">Gagal: '+e+'</span>';}
try{
const s=await fetch('/status');const sd=await s.json();
renderCandidates(sd);
}catch(e){}
}
loadConfig();
fetchStatus();setInterval(fetchStatus,3000);
</script>
</body>
</html>"""


def start_bot():
    global BOT_PID
    if BOT_PID and _alive(BOT_PID):
        return False, "bot sudah jalan"
    env = dict(os.environ)
    proc = subprocess.Popen(
        ["python", "-u", "bot.py"],
        cwd=BASE_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    BOT_PID = proc.pid
    with open(os.path.join(BASE_DIR, "bot.pid"), "w") as f:
        f.write(str(proc.pid))
    state.state.update(running=True)
    threading.Thread(target=_pump_log, args=(proc,), daemon=True).start()
    return True, f"bot started (PID {proc.pid})"


def stop_bot():
    global BOT_PID
    if not BOT_PID or not _alive(BOT_PID):
        state.state.update(running=False)
        return False, "bot tidak jalan"
    os.kill(BOT_PID, signal.SIGTERM)
    return True, "SIGTERM dikirim (sell all + exit)"


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pump_log(proc):
    global BOT_PID
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            state.state.log(line)
    proc.wait()
    if proc.pid == BOT_PID:
        BOT_PID = None
        state.state.update(running=False)
        try:
            os.remove(os.path.join(BASE_DIR, "bot.pid"))
        except OSError:
            pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if path == "/":
            self._html(INDEX_HTML.encode())
        elif path == "/status":
            state.state._load_bot_state()
            state.state._load_trades()
            state.state._load_history()
            self._json(state.state.data)
        elif path == "/action":
            cmd = qs.get("cmd", [""])[0]
            if cmd == "start":
                ok, msg = start_bot()
                self._json({"ok": ok, "msg": msg})
            elif cmd == "stop":
                ok, msg = stop_bot()
                self._json({"ok": ok, "msg": msg})
            elif cmd == "sell":
                if BOT_PID and _alive(BOT_PID):
                    os.kill(BOT_PID, signal.SIGUSR1)
                    self._json({"ok": True, "msg": "signal sell dikirim"})
                else:
                    self._json({"ok": False, "msg": "bot tidak jalan"})
            elif cmd == "export":
                try:
                    f = state.state.export_txt()
                    self._json({"ok": True, "file": f})
                except Exception as e:
                    self._json({"ok": False, "error": str(e)})
            else:
                self._json({"ok": False, "msg": "cmd tidak dikenal"})
        elif path == "/config":
            env = _load_env()
            self._json({
                "base_url": env.get("AI_BASE_URL", ""),
                "api_key": env.get("AI_API_KEY", ""),
                "model": env.get("AI_MODEL", ""),
            })
        elif path == "/market":
            try:
                import market_analyzer
                self._json(market_analyzer.get_analysis())
            except Exception as e:
                self._json({"error": str(e)})
        elif path == "/download":
            date_str = qs.get("date", [""])[0]
            f = state.state.export_txt(date_str or None)
            try:
                with open(f, "rb") as fp:
                    body = fp.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(f)}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/config":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(body)
            except Exception:
                data = {}
            base_url = (data.get("base_url") or "").strip()
            api_key = (data.get("api_key") or "").strip()
            model = (data.get("model") or "").strip()

            updates = {}
            if base_url:
                updates["AI_BASE_URL"] = base_url
            if api_key:
                updates["AI_API_KEY"] = api_key
            if model:
                updates["AI_MODEL"] = model

            if not updates:
                self._json({"ok": False, "msg": "tidak ada perubahan"})
                return

            # test koneksi dulu sebelum simpan
            new_env = _load_env()
            new_env.update(updates)
            tb = new_env.get("AI_BASE_URL", "")
            tk = new_env.get("AI_API_KEY", "")
            tm = new_env.get("AI_MODEL", "")
            try:
                ok, _ = _test_ai_connection(tb, tk, tm)
                if not ok:
                    self._json({"ok": False, "msg": "test koneksi AI gagal"})
                    return
            except Exception as e:
                self._json({"ok": False, "msg": f"test koneksi AI gagal: {e}"})
                return

            _save_env(updates)
            self._json({"ok": True, "msg": "config disimpan. Restart bot agar berlaku."})
        else:
            self.send_response(404)
            self.end_headers()


def _env_path():
    return os.path.join(BASE_DIR, ".env")


def _load_env():
    data = {}
    try:
        with open(_env_path()) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    except Exception:
        pass
    return data


def _save_env(updates):
    env = _load_env()
    env.update(updates)
    lines = []
    with open(_env_path()) as f:
        seen = set()
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                lines.append(line.rstrip("\n"))
                continue
            k = s.split("=", 1)[0].strip()
            if k in env:
                lines.append(f"{k}={env[k]}")
                seen.add(k)
            else:
                lines.append(line.rstrip("\n"))
        for k, v in env.items():
            if k not in seen:
                lines.append(f"{k}={v}")
    with open(_env_path(), "w") as f:
        f.write("\n".join(lines) + "\n")


def _test_ai_connection(base_url, api_key, model):
    import urllib.request as ur
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "reply OK"}],
        "max_tokens": 5,
    }).encode()
    req = ur.Request(base_url.rstrip("/") + "/chat/completions", data=body, headers={
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "User-Agent": "TradingBot/1.0",
    })
    with ur.urlopen(req, timeout=20) as r:
        return True, "OK"


def main():
    srv = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Daemon jalan: http://0.0.0.0:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
