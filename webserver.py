import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import state

PORT = int(os.getenv("WEB_PORT", "8080"))

INDEX_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading Bot</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0f1115;color:#e6e6e6;padding:16px;max-width:800px;margin:0 auto}
h1{font-size:1.2em;margin-bottom:12px}
h3{font-size:0.95em;margin-bottom:8px;color:#c0c4cc}
.card{background:#1a1d24;border-radius:10px;padding:14px;margin-bottom:12px}
.row{display:flex;gap:10px;flex-wrap:wrap}
.col{flex:1;min-width:120px}
.label{font-size:0.72em;color:#8a8f98;margin-bottom:4px}
.value{font-size:1.25em;font-weight:600}
.green{color:#2ecc71}.red{color:#e74c3c}.yellow{color:#f1c40f}.dim{color:#8a8f98}
.btn{display:inline-block;padding:10px 14px;border:none;border-radius:8px;font-size:0.85em;cursor:pointer;margin:0 6px 6px 0}
.btn-start{background:#2ecc71;color:#0f1115}
.btn-stop{background:#e74c3c;color:#fff}
.btn-sell{background:#f39c12;color:#0f1115}
.btn-export{background:#3498db;color:#fff}
table{width:100%;border-collapse:collapse;font-size:0.8em}
th,td{padding:7px;text-align:left;border-bottom:1px solid #2a2d35}
th{color:#8a8f98;font-weight:500}
.log{max-height:250px;overflow-y:auto;font-size:0.78em;font-family:monospace}
.log div{padding:3px 0;border-bottom:1px solid #22252c}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:0.7em;font-weight:600}
.badge-run{background:#2ecc71;color:#0f1115}.badge-stop{background:#e74c3c;color:#fff}
.badge-dry{background:#f1c40f;color:#0f1115;margin-left:6px}
.statbox{background:#22262e;border-radius:8px;padding:10px}
.tabs{display:flex;gap:6px;margin-bottom:10px}
.tab{padding:6px 14px;border-radius:8px;background:#22262e;cursor:pointer;font-size:0.85em}
.tab.active{background:#3498db;color:#fff}
</style>
</head>
<body>
<h1>📈 Trading Bot <span id="statusBadge" class="badge badge-stop">...</span> <span id="dryBadge" class="badge badge-dry" style="display:none">DRY-RUN</span></h1>

<div class="card">
<h3>Kontrol</h3>
<button class="btn btn-start" onclick="action('start')">▶ Start</button>
<button class="btn btn-stop" onclick="action('stop')">⏹ Stop (sell all)</button>
<button class="btn btn-sell" onclick="action('sell')">💰 Sell Semua</button>
<button class="btn btn-export" onclick="doExport()">📄 Export Riwayat (.txt)</button>
</div>

<div class="card">
<div class="row">
<div class="col"><div class="statbox"><div class="label">Saldo (USDT)</div><div class="value" id="balance">-</div></div></div>
<div class="col"><div class="statbox"><div class="label">P&L Hari Ini</div><div class="value" id="pnl">-</div></div></div>
<div class="col"><div class="statbox"><div class="label">Risk Aktif</div><div class="value" id="risk">-</div></div></div>
<div class="col"><div class="statbox"><div class="label">Loss Beruntun</div><div class="value" id="losses">-</div></div></div>
</div>
</div>

<div class="card">
<h3>Posisi</h3>
<div id="position">Tidak ada posisi</div>
</div>

<div class="card">
<h3>Sinyal AI Terakhir</h3>
<div id="ai">-</div>
</div>

<div class="card">
<h3>Riwayat Per Hari</h3>
<table><thead><tr><th>Tanggal</th><th>Trade</th><th>P&L %</th><th>P&L USDT</th><th>Saldo</th><th></th></tr></thead>
<tbody id="history"></tbody></table>
</div>

<div class="card">
<h3>Riwayat Trade Hari Ini</h3>
<table><thead><tr><th>Waktu</th><th>Aksi</th><th>Qty</th><th>Harga</th><th>P&L</th></tr></thead>
<tbody id="trades"></tbody></table>
</div>

<div class="card">
<h3>Log</h3>
<div class="log" id="log"></div>
</div>

<script>
function fmt(n){return n==null?"-":Number(n).toFixed(2)}
async function fetchStatus(){
try{
const r=await fetch('/status');const d=await r.json();
document.getElementById('statusBadge').textContent=d.running?'RUNNING':'STOPPED';
document.getElementById('statusBadge').className='badge '+(d.running?'badge-run':'badge-stop');
const db=document.getElementById('dryBadge');
if(d.dry_run){db.style.display='inline-block'}else{db.style.display='none'}
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

if(d.last_ai_action){
document.getElementById('ai').innerHTML=
'<span class="'+(d.last_ai_action=='buy'?'green':'dim')+'">'+d.last_ai_action.toUpperCase()+'</span> '+
'(conf '+d.last_ai_confidence+') '+d.last_ai_reason+' <span class="dim">('+d.last_ai_time+')</span>';
}

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

let th='';
const today=(d.history&&d.history.length)?d.history[d.history.length-1]:null;
const todayTrades=(today&&today.trades)?today.trades:[];
for(let i=todayTrades.length-1;i>=0;i--){const t=todayTrades[i];
th+='<tr><td>'+t.t+'</td><td>'+t.action+'</td><td>'+t.qty+'</td><td>'+fmt(t.price)+'</td><td>'+fmt(t.pnl)+'</td></tr>';}
document.getElementById('trades').innerHTML=th;

let lg='';
for(let i=d.log.length-1;i>=0;i--){lg+='<div>'+d.log[i].t+' '+d.log[i].m+'</div>';}
document.getElementById('log').innerHTML=lg;
}catch(e){}
}
async function action(a){await fetch('/action?cmd='+a);setTimeout(fetchStatus,500);}
async function doExport(){
const r=await fetch('/action?cmd=export');
const d=await r.json();
alert(d.ok?'Export tersimpan: '+d.file:'Gagal: '+d.error);
}
fetchStatus();setInterval(fetchStatus,3000);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, data):
        body = json.dumps(data).encode()
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
        if path == "/":
            self._html(INDEX_HTML.encode())
        elif path == "/status":
            self._json(state.state.data)
        elif path == "/action":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            cmd = qs.get("cmd", [""])[0]
            if cmd == "export":
                try:
                    f = state.state.export_txt()
                    self._json({"ok": True, "file": f})
                except Exception as e:
                    self._json({"ok": False, "error": str(e)})
            else:
                state.state.data["_command"] = cmd
                self._json({"ok": True, "cmd": cmd})
        elif path == "/download":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
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
            except Exception as e:
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


def start_server():
    srv = HTTPServer(("0.0.0.0", PORT), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv
