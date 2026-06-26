"""
dashboard.py — Track · Cockpit LIVE (dashboard web local, auto-rafraîchi).

Backend Flask : scanne une watchlist de LEADERS US via yfinance, calcule un
score Track multi-facteurs (tendance + momentum + force relative + position
52s), classe les OPPORTUNITÉS DU JOUR, et sert une page qui se met à jour
toute seule.

Lancer :  py dashboard.py   →   ouvrir http://localhost:5000
Données :  yfinance (différé ~15 min — OK pour du swing 1-3 mois).
⛔ ANALYSE ONLY — aucun ordre, aucune exécution. Lecture/affichage seulement.
"""
import time
import threading
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf
from flask import Flask, jsonify

# — Watchlist de leaders US (modifiable) —
WATCHLIST = ['AAPL', 'NVDA', 'MSFT', 'META', 'GOOGL', 'AMZN', 'AVGO', 'TSLA',
             'NFLX', 'AMD', 'CRM', 'COST', 'LLY', 'JPM', 'V', 'MA', 'HD',
             'UNH', 'XOM', 'WMT']
BENCH = 'SPY'
REFRESH_SEC = 60          # le moteur re-scanne toutes les 60 s (yfinance)

app = Flask(__name__)
state = {'rows': [], 'updated': None, 'error': None, 'count': 0}


def _rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _analyse(c, bench_ret):
    """Score Track-lite 0-100 + sous-scores pour une série de clôtures."""
    last = float(c.iloc[-1])
    e20 = float(c.ewm(span=20).mean().iloc[-1])
    e50 = float(c.ewm(span=50).mean().iloc[-1])
    e200 = float(c.ewm(span=200).mean().iloc[-1])
    stack = int(e20 > e50) + int(e50 > e200) + int(last > e50)
    trend = stack / 3 * 100
    r = float(_rsi(c).iloc[-1])
    roc = (last / float(c.iloc[-21]) - 1) * 100 if len(c) > 21 else 0.0
    mom = float(np.clip(50 + (r - 50) * 0.6 + np.clip(roc, -25, 25), 0, 100))
    sym_ret = (last / float(c.iloc[-63]) - 1) if len(c) > 63 else 0.0
    rs = float(np.clip(50 + (sym_ret - bench_ret) * 200, 0, 100))
    hi = float(c.tail(252).max())
    lo = float(c.tail(252).min())
    pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50.0
    score = round(0.40 * trend + 0.30 * mom + 0.20 * rs + 0.10 * pos)
    chg = (last / float(c.iloc[-2]) - 1) * 100 if len(c) > 1 else 0.0
    if score >= 72 and trend >= 66:
        verdict = 'ACHAT'
    elif score >= 55:
        verdict = 'SURVEILLER'
    else:
        verdict = 'ÉVITER'
    return {
        'symbol': '', 'price': round(last, 2), 'change': round(chg, 2),
        'score': int(score), 'trend': round(trend), 'mom': round(mom),
        'rsi': round(r), 'rs': round(rs), 'pos52': round(pos), 'verdict': verdict,
    }


def scan():
    try:
        raw = yf.download(WATCHLIST + [BENCH], period='1y', interval='1d',
                          progress=False, auto_adjust=True, threads=True)
        data = raw['Close'] if isinstance(raw.columns, pd.MultiIndex) else raw
        bench = data[BENCH].dropna()
        bench_ret = (float(bench.iloc[-1]) / float(bench.iloc[-63]) - 1) if len(bench) > 63 else 0.0
        rows = []
        for sym in WATCHLIST:
            try:
                c = data[sym].dropna()
                if len(c) < 60:
                    continue
                row = _analyse(c, bench_ret)
                row['symbol'] = sym
                rows.append(row)
            except Exception:
                continue
        rows.sort(key=lambda x: x['score'], reverse=True)
        state['rows'] = rows
        state['updated'] = datetime.now().strftime('%H:%M:%S')
        state['count'] = len(rows)
        state['error'] = None
    except Exception as e:
        state['error'] = str(e)


def _loop():
    while True:
        scan()
        time.sleep(REFRESH_SEC)


@app.route('/data')
def data():
    return jsonify(state)


@app.route('/')
def index():
    return PAGE


PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Track — Cockpit Live</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#070b14;color:#e6edf3;font-family:-apple-system,Segoe UI,Roboto,Helvetica,sans-serif;font-size:14px}
.wrap{max-width:1100px;margin:0 auto;padding:18px}
header{display:flex;justify-content:space-between;align-items:baseline;border-bottom:1px solid #1c2433;padding-bottom:12px;margin-bottom:16px}
h1{font-size:18px;font-weight:600;margin:0;letter-spacing:.5px}
.muted{color:#7d8aa0;font-size:12px}
h2{font-size:13px;color:#9aa7bd;text-transform:uppercase;letter-spacing:1px;margin:18px 0 10px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.card{background:#0d1320;border:1px solid #1c2433;border-radius:12px;padding:14px}
.card .sym{font-size:18px;font-weight:600}
.card .px{color:#9aa7bd;font-size:13px}
.sc{font-size:30px;font-weight:700;line-height:1}
table{width:100%;border-collapse:collapse;margin-top:6px}
th,td{padding:9px 10px;text-align:right;border-bottom:1px solid #161e2c}
th{color:#7d8aa0;font-size:11px;text-transform:uppercase;letter-spacing:.5px;font-weight:500}
td.l,th.l{text-align:left}
.sym{font-weight:600}
.bar{display:inline-block;height:8px;border-radius:4px;vertical-align:middle}
.badge{padding:3px 9px;border-radius:6px;font-size:12px;font-weight:600}
.b-achat{background:#0f3d2e;color:#34d399}
.b-surv{background:#3d340f;color:#fbbf24}
.b-evit{background:#3d1414;color:#f87171}
.up{color:#34d399}.dn{color:#f87171}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#34d399;margin-right:6px;animation:p 1.6s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
</style></head>
<body><div class="wrap">
<header>
  <h1>◣ TRACK — COCKPIT LIVE</h1>
  <div class="muted"><span class="dot"></span><span id="clock"></span> · données maj <span id="updated">—</span> · <span id="cnt">0</span> titres</div>
</header>
<div id="err" class="muted" style="color:#f87171"></div>
<h2>★ Opportunités du jour</h2>
<div class="cards" id="cards"></div>
<h2>Scan complet · classé par score</h2>
<table><thead><tr>
<th class="l">Titre</th><th>Prix</th><th>Jour</th><th>Score</th><th></th>
<th>Tend</th><th>Mom</th><th>RSI</th><th>RS</th><th>52s</th><th class="l">Verdict</th>
</tr></thead><tbody id="rows"></tbody></table>
<p class="muted" style="margin-top:18px">Track-lite (tendance 40 · momentum 30 · force relative 20 · position 52s 10). Données yfinance différées ~15 min. Analyse uniquement — aucun ordre.</p>
</div>
<script>
function clr(s){return s>=72?'#34d399':s>=55?'#fbbf24':'#f87171'}
function bcls(v){return v==='ACHAT'?'b-achat':v==='SURVEILLER'?'b-surv':'b-evit'}
function tick(){document.getElementById('clock').textContent=new Date().toLocaleTimeString('fr-FR')}
setInterval(tick,1000);tick();
async function refresh(){
  try{
    const d=await(await fetch('/data')).json();
    document.getElementById('updated').textContent=d.updated||'—';
    document.getElementById('cnt').textContent=d.count||0;
    document.getElementById('err').textContent=d.error?('⚠ '+d.error):'';
    const rows=d.rows||[];
    const top=rows.filter(r=>r.verdict==='ACHAT').slice(0,4);
    document.getElementById('cards').innerHTML=(top.length?top:rows.slice(0,4)).map(r=>`
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:baseline">
          <span class="sym">${r.symbol}</span>
          <span class="px">$${r.price} <span class="${r.change>=0?'up':'dn'}">${r.change>=0?'+':''}${r.change}%</span></span>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:10px">
          <span class="sc" style="color:${clr(r.score)}">${r.score}</span>
          <span class="badge ${bcls(r.verdict)}">${r.verdict}</span>
        </div>
      </div>`).join('');
    document.getElementById('rows').innerHTML=rows.map(r=>`
      <tr>
        <td class="l sym">${r.symbol}</td>
        <td>$${r.price}</td>
        <td class="${r.change>=0?'up':'dn'}">${r.change>=0?'+':''}${r.change}%</td>
        <td style="font-weight:700;color:${clr(r.score)}">${r.score}</td>
        <td class="l"><span class="bar" style="width:${r.score}px;background:${clr(r.score)}"></span></td>
        <td>${r.trend}</td><td>${r.mom}</td><td>${r.rsi}</td><td>${r.rs}</td><td>${r.pos52}%</td>
        <td class="l"><span class="badge ${bcls(r.verdict)}">${r.verdict}</span></td>
      </tr>`).join('');
  }catch(e){document.getElementById('err').textContent='⚠ '+e}
}
setInterval(refresh,5000);refresh();
</script>
</body></html>"""

if __name__ == '__main__':
    threading.Thread(target=_loop, daemon=True).start()
    print('Track Cockpit Live -> http://localhost:5000  (Ctrl+C pour arreter)')
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)
