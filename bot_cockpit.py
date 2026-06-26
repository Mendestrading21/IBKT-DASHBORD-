# -*- coding: utf-8 -*-
"""
TRACK — BOT COCKPIT (web live)  —  le cockpit du bot paper autonome
====================================================================
Cockpit Flask (port 5003) qui visualise le BOT PAPER AUTONOME (paper_live_bot.py) :
  • KPIs : equity, rendement, Sharpe, max drawdown, win rate, profit factor
  • Courbe d'equity (Chart.js)
  • Positions ouvertes en P&L temps réel + stops
  • Opportunités du jour (setups frais non détenus)
  • Tickets IBKR semi-auto (BUY/SELL prêts à passer À LA MAIN)
  • Statut marché (SPY > MM200)
  • Bouton « TICK » → fait avancer le bot d'un jour (lance paper_live_bot.py)

Auto-refresh, scan de fond toutes les 2 min. ⛔ PAPER ONLY — zéro ordre réel.
Lancer :  py bot_cockpit.py   →   http://localhost:5003
"""
import sys, os, json, threading, time, subprocess
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import numpy as np
import pandas as pd
from flask import Flask, jsonify, redirect
try:
    import yfinance as yf
except Exception as e:
    print("yfinance manquant :", e); sys.exit(1)
from stock_backtest import build_engine, precompute_signals, ATR_MULT

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "paper_state.json")
INIT_CAPITAL = 1000.0
PERIOD, INTERVAL = "2y", "1d"
BENCH = "SPY"
UNIVERSE = ["AAPL","MSFT","NVDA","AMD","GOOGL","AMZN","META","LLY","UNH","JPM",
            "V","MA","AVGO","COST","HD","NFLX","CRM","ADBE","PG","XOM",
            "TSLA","ORCL","WMT","KO","PEP","BAC","DIS","INTC","QCOM","TXN"]
REG = {2:"Hausse forte", 1:"Hausse", 0:"Range", -1:"Baisse", -2:"Baisse forte"}

app = Flask(__name__)
SNAP = {"ready": False, "msg": "Initialisation…"}
LOCK = threading.Lock()

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return dict(cash=INIT_CAPITAL, positions={}, journal=[], equity=[], last_bar=None, init=INIT_CAPITAL)

def max_dd(vals):
    if not vals: return 0.0
    s = pd.Series(vals); peak = s.cummax()
    return float(((peak - s)/peak*100).max())

def compute_snapshot():
    s = load_state()
    raw = yf.download(UNIVERSE+[BENCH], period=PERIOD, interval=INTERVAL,
                      auto_adjust=True, progress=False, group_by="ticker")
    spy = raw[BENCH].rename(columns=str.lower)[["close"]].dropna()
    spy_sma = spy["close"].rolling(200).mean()
    mkt_ok = bool(spy["close"].iloc[-1] > spy_sma.iloc[-1]) if pd.notna(spy_sma.iloc[-1]) else True
    latest = {}
    for tk in UNIVERSE:
        try:
            df = raw[tk].rename(columns=str.lower)[["open","high","low","close","volume"]].dropna()
        except Exception:
            continue
        if len(df) < 260: continue
        e = build_engine(df)
        if len(e) < 10: continue
        rows, sigs = precompute_signals(e)
        latest[tk] = dict(close=float(rows[-1]["close"]), atr=float(rows[-1]["atr"]),
                          net=float(rows[-1]["net"]), reg=int(rows[-1]["rawRegime"]),
                          sig=bool(sigs[-1]))
    # positions + equity
    cash = s.get("cash", INIT_CAPITAL); init = s.get("init", INIT_CAPITAL)
    positions = []; eq = cash
    for tk, p in s.get("positions", {}).items():
        c = latest[tk]["close"] if tk in latest else p["entry"]
        val = p["qty"]*c; eq += val
        positions.append(dict(tk=tk, qty=round(p["qty"],4), entry=round(p["entry"],2),
                              cur=round(c,2), stop=round(p["stop"],2),
                              pl=round((c-p["entry"])*p["qty"],2),
                              plp=round((c/p["entry"]-1)*100,2), be=p["partial"]))
    ret = (eq/init-1)*100
    # stats réalisées
    closed = [j for j in s.get("journal",[]) if j["act"] in ("TP1","STOP")]
    realized = round(sum(j["pnl"] for j in closed),2)
    wr = round(sum(1 for j in closed if j["pnl"]>0)/len(closed)*100,1) if closed else None
    wins = sum(j["pnl"] for j in closed if j["pnl"]>0); loss = -sum(j["pnl"] for j in closed if j["pnl"]<=0)
    pf = round(wins/loss,2) if loss>0 else None
    eqhist = s.get("equity", [])
    eqvals = [e[1] for e in eqhist]
    mdd = round(max_dd(eqvals + [eq]),1)
    sharpe = None
    if len(eqvals) > 3:
        r = pd.Series(eqvals).pct_change().dropna()
        if r.std() > 0: sharpe = round(float(r.mean()/r.std()*np.sqrt(252)),2)
    # opportunités
    held = set(s.get("positions",{}).keys())
    opps = sorted([dict(tk=tk, net=round(v["net"]), reg=REG[v["reg"]], price=round(v["close"],2),
                        stop=round(v["close"]-ATR_MULT*v["atr"],2))
                   for tk,v in latest.items() if v["sig"] and v["net"]>0 and tk not in held],
                  key=lambda x:-x["net"])[:8]
    watch = sorted([dict(tk=tk, net=round(v["net"]), reg=REG[v["reg"]])
                    for tk,v in latest.items() if (not v["sig"]) and v["net"]>15 and tk not in held],
                   key=lambda x:-x["net"])[:8]
    # tickets IBKR
    tickets = [dict(side="SELL STP", tk=p["tk"], qty=p["qty"], px=p["stop"]) for p in positions]
    return dict(ready=True, ts=time.strftime("%Y-%m-%d %H:%M:%S"),
                equity=round(eq,2), cash=round(cash,2), init=init, ret=round(ret,2),
                mkt_ok=mkt_ok, npos=len(positions), maxpos=5,
                realized=realized, wr=wr, pf=pf, mdd=mdd, sharpe=sharpe,
                positions=positions, opps=opps, watch=watch, tickets=tickets,
                eqhist=eqhist, journal=s.get("journal",[])[-12:],
                last_bar=s.get("last_bar"))

def refresh_loop():
    global SNAP
    while True:
        try:
            snap = compute_snapshot()
            with LOCK: SNAP = snap
        except Exception as e:
            with LOCK: SNAP = {"ready": False, "msg": "Erreur scan : " + str(e)}
        time.sleep(120)

@app.route("/data")
def data():
    with LOCK:
        return jsonify(SNAP)

@app.route("/tick", methods=["POST", "GET"])
def tick():
    try:
        subprocess.run([sys.executable, os.path.join(HERE, "paper_live_bot.py")],
                       cwd=HERE, timeout=180, capture_output=True)
    except Exception:
        pass
    try:
        snap = compute_snapshot()
        with LOCK:
            globals()["SNAP"] = snap
    except Exception:
        pass
    return redirect("/")

@app.route("/")
def index():
    return PAGE

PAGE = r"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Track — Bot Cockpit</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#070A12;color:#E6EDF3;font-family:'Segoe UI',system-ui,sans-serif;padding:18px}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:10px}
h1{font-size:20px;color:#5EEAD4;letter-spacing:.5px}
.sub{color:#787B86;font-size:12px}
.badge{padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}
.on{background:#0c3;color:#031;}.off{background:#a33;color:#fee}
button{background:#1b2230;color:#5EEAD4;border:1px solid #2b3a4f;padding:8px 16px;border-radius:8px;cursor:pointer;font-weight:600}
button:hover{background:#243049}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}
.kpi{background:#0D1117;border:1px solid #1d2733;border-radius:10px;padding:12px}
.kpi .l{color:#787B86;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.kpi .v{font-size:22px;font-weight:700;margin-top:4px}
.pos{color:#2BE8A0}.neg{color:#FF5C7A}.neu{color:#B0BEC5}
.cols{display:grid;grid-template-columns:1.4fr 1fr;gap:14px}
@media(max-width:900px){.cols{grid-template-columns:1fr}}
.card{background:#0D1117;border:1px solid #1d2733;border-radius:10px;padding:14px;margin-bottom:14px}
.card h2{font-size:13px;color:#5EEAD4;margin-bottom:10px;text-transform:uppercase;letter-spacing:.5px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:#787B86;text-align:right;font-weight:500;padding:5px;border-bottom:1px solid #1d2733;font-size:11px}
th:first-child,td:first-child{text-align:left}
td{padding:5px;border-bottom:1px solid #131a26}
.tk{font-weight:700;color:#E6EDF3}
.tick-ticket{font-family:monospace;font-size:12px;color:#E0A24A;padding:3px 0}
.muted{color:#566;font-size:11px;margin-top:8px}
</style></head><body>
<div class="top">
  <div><h1>🤖 TRACK — BOT COCKPIT</h1><div class="sub" id="ts">chargement…</div></div>
  <div style="display:flex;gap:10px;align-items:center">
    <span id="mkt" class="badge neu">marché ?</span>
    <form action="/tick" method="post" style="display:inline"><button>▶ TICK (avancer le bot)</button></form>
  </div>
</div>
<div class="grid" id="kpis"></div>
<div class="card"><h2>Courbe d'equity (paper)</h2><canvas id="eq" height="90"></canvas></div>
<div class="cols">
  <div>
    <div class="card"><h2>Positions ouvertes</h2><div id="pos"></div></div>
    <div class="card"><h2>📋 Tickets IBKR — à passer à la main (semi-auto)</h2><div id="tix"></div></div>
    <div class="card"><h2>Journal</h2><div id="jrn"></div></div>
  </div>
  <div>
    <div class="card"><h2>🎯 Opportunités du jour</h2><div id="opp"></div></div>
    <div class="card"><h2>👁 Sous surveillance</h2><div id="wch"></div></div>
  </div>
</div>
<div class="muted">⛔ PAPER ONLY — simulation, zéro argent réel. Scan auto toutes les 2 min. Le bot prépare ; tu valides chaque ordre réel.</div>
<script>
let chart=null;
function col(v){return v>0?'pos':v<0?'neg':'neu'}
function sgn(v,suf=''){return (v>0?'+':'')+v+suf}
async function load(){
 const d=await (await fetch('/data')).json();
 if(!d.ready){document.getElementById('ts').textContent=d.msg||'…';return;}
 document.getElementById('ts').textContent='maj '+d.ts+' · barre '+(d.last_bar||'—');
 const mk=document.getElementById('mkt');mk.textContent=d.mkt_ok?'▲ marché haussier':'▼ marché baissier';mk.className='badge '+(d.mkt_ok?'on':'off');
 const k=[['Equity',d.equity.toLocaleString()+' $',col(d.ret)],['Rendement',sgn(d.ret,' %'),col(d.ret)],
   ['Réalisé',sgn(d.realized,' $'),col(d.realized)],['Win rate',d.wr==null?'—':d.wr+' %','neu'],
   ['Profit factor',d.pf==null?'—':d.pf,d.pf==null?'neu':(d.pf>=1?'pos':'neg')],
   ['Max DD',d.mdd+' %','neu'],['Sharpe',d.sharpe==null?'—':d.sharpe,'neu'],
   ['Positions',d.npos+'/'+d.maxpos,'neu']];
 document.getElementById('kpis').innerHTML=k.map(x=>`<div class="kpi"><div class="l">${x[0]}</div><div class="v ${x[2]}">${x[1]}</div></div>`).join('');
 // positions
 document.getElementById('pos').innerHTML = d.positions.length? `<table><tr><th>Titre</th><th>qté</th><th>entrée</th><th>actuel</th><th>stop</th><th>P&L</th></tr>`+
   d.positions.map(p=>`<tr><td class="tk">${p.tk}${p.be?' ·BE':''}</td><td>${p.qty}</td><td>${p.entry}</td><td>${p.cur}</td><td>${p.stop}</td><td class="${col(p.pl)}">${sgn(p.pl,' $')} (${sgn(p.plp,'%')})</td></tr>`).join('')+`</table>`
   : '<div class="muted">Aucune position.</div>';
 // tickets
 document.getElementById('tix').innerHTML = d.tickets.length? d.tickets.map(t=>`<div class="tick-ticket">➤ ${t.side} ${t.qty} ${t.tk} @ ${t.px}</div>`).join('') : '<div class="muted">Aucun ordre à passer.</div>';
 // journal
 document.getElementById('jrn').innerHTML = d.journal.length? `<table><tr><th>date</th><th>act</th><th>titre</th><th>qté</th><th>px</th><th>P&L</th></tr>`+
   d.journal.slice().reverse().map(j=>`<tr><td>${j.date}</td><td>${j.act}</td><td class="tk">${j.tk}</td><td>${j.qty}</td><td>${j.px}</td><td class="${col(j.pnl)}">${j.act=='BUY'?'—':sgn(j.pnl,' $')}</td></tr>`).join('')+`</table>` : '<div class="muted">Vide.</div>';
 // opps
 document.getElementById('opp').innerHTML = d.opps.length? `<table><tr><th>Titre</th><th>score</th><th>régime</th><th>prix</th><th>stop</th></tr>`+
   d.opps.map(o=>`<tr><td class="tk">${o.tk}</td><td class="pos">+${o.net}</td><td>${o.reg}</td><td>${o.price}</td><td>${o.stop}</td></tr>`).join('')+`</table>` : '<div class="muted">Aucun setup frais.</div>';
 document.getElementById('wch').innerHTML = d.watch.length? `<table><tr><th>Titre</th><th>score</th><th>régime</th></tr>`+
   d.watch.map(o=>`<tr><td class="tk">${o.tk}</td><td class="pos">+${o.net}</td><td>${o.reg}</td></tr>`).join('')+`</table>` : '<div class="muted">—</div>';
 // chart
 const lab=d.eqhist.map(e=>e[0]), val=d.eqhist.map(e=>e[1]);
 if(!chart){chart=new Chart(document.getElementById('eq'),{type:'line',data:{labels:lab,datasets:[{data:val,borderColor:'#5EEAD4',backgroundColor:'rgba(94,234,212,.08)',fill:true,tension:.25,pointRadius:0,borderWidth:2}]},options:{plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#566',maxTicksLimit:8},grid:{color:'#131a26'}},y:{ticks:{color:'#566'},grid:{color:'#131a26'}}}}});}
 else{chart.data.labels=lab;chart.data.datasets[0].data=val;chart.update();}
}
load();setInterval(load,5000);
</script></body></html>"""

if __name__ == "__main__":
    print("[cockpit] scan initial (peut prendre ~20 s)…")
    try:
        SNAP = compute_snapshot()
    except Exception as e:
        SNAP = {"ready": False, "msg": "Erreur init : " + str(e)}
    threading.Thread(target=refresh_loop, daemon=True).start()
    print("[cockpit] ouvre http://localhost:5003")
    app.run(host="127.0.0.1", port=5003, debug=False, threaded=True)
