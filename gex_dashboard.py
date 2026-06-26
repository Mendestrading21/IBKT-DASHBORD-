"""
gex_dashboard.py — Track · GEX / Dealer Positioning LIVE (dashboard web local).

Tire la VRAIE chaîne d'options (yfinance : OI + IV par strike), calcule le gamma
(Black-Scholes), puis le GEX par strike, le NET GEX, la CALL WALL, la PUT WALL,
le GAMMA FLIP, le DEX et le régime dealer (gamma positif/négatif) + un playbook.

Lancer :  py gex_dashboard.py   →   http://localhost:5001
Données :  yfinance (chaîne d'options + spot, différé ~15 min). Gamma/Delta = BS maison.
⛔ ANALYSE ONLY — aucun ordre. Lecture/affichage seulement. NOT FINANCIAL ADVICE.
"""
import math
import time
import threading
from datetime import datetime

import numpy as np
import yfinance as yf
from flask import Flask, jsonify

SYMBOL = 'SPY'          # sous-jacent (modifiable : SPY, QQQ, NVDA, META, ...)
R = 0.045               # taux sans risque approx
N_EXP = 2               # nombre d'échéances proches agrégées
BAND = 0.10             # ±10% autour du spot pour les strikes affichés
REFRESH_SEC = 90        # re-calcul toutes les 90 s

app = Flask(__name__)
state = {'updated': None, 'error': None, 'sym': SYMBOL}


def _npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def _d1(S, K, T, sig):
    return (math.log(S / K) + (R + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))


def _gamma(S, K, T, sig):
    if T <= 0 or sig <= 0 or S <= 0 or K <= 0:
        return 0.0
    return _npdf(_d1(S, K, T, sig)) / (S * sig * math.sqrt(T))


def _delta(S, K, T, sig, call):
    if T <= 0 or sig <= 0:
        return (1.0 if S > K else 0.0) if call else (-1.0 if S < K else 0.0)
    dd = _ncdf(_d1(S, K, T, sig))
    return dd if call else dd - 1.0


def _i(x):
    try:
        return 0 if x is None or (isinstance(x, float) and math.isnan(x)) else int(x)
    except Exception:
        return 0


def _f(x):
    try:
        return 0.0 if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)
    except Exception:
        return 0.0


def compute():
    try:
        tk = yf.Ticker(SYMBOL)
        try:
            spot = float(tk.fast_info['lastPrice'])
        except Exception:
            spot = float(tk.history(period='1d')['Close'].iloc[-1])
        exps = list(tk.options)[:N_EXP]
        now = datetime.now()
        lo, hi = spot * (1 - BAND), spot * (1 + BAND)
        agg = {}   # strike -> dict

        for exp in exps:
            T = max((datetime.strptime(exp, '%Y-%m-%d') - now).days, 0) / 365.0
            T = max(T, 0.5 / 365.0)
            ch = tk.option_chain(exp)
            for is_call, df in ((True, ch.calls), (False, ch.puts)):
                for _, row in df.iterrows():
                    K = _f(row['strike'])
                    if K < lo or K > hi:
                        continue
                    iv = _f(row.get('impliedVolatility'))
                    oi = _i(row.get('openInterest'))
                    vol = _i(row.get('volume'))
                    if iv <= 0 or oi <= 0:
                        continue
                    g = _gamma(spot, K, T, iv)
                    de = _delta(spot, K, T, iv, is_call)
                    d = agg.setdefault(K, {'cg': 0., 'pg': 0., 'coi': 0, 'poi': 0,
                                           'cvol': 0, 'pvol': 0, 'cdex': 0., 'pdex': 0.})
                    if is_call:
                        d['cg'] += g * oi
                        d['coi'] += oi
                        d['cvol'] += vol
                        d['cdex'] += de * oi
                    else:
                        d['pg'] += g * oi
                        d['poi'] += oi
                        d['pvol'] += vol
                        d['pdex'] += de * oi

        if not agg:
            state['error'] = 'Aucune donnée option exploitable (marché fermé ou symbole sans options).'
            return

        strikes = sorted(agg.keys())
        scale = 100.0 * spot * spot * 0.01     # $ gamma pour un mouvement de 1%
        rows = []
        for K in strikes:
            d = agg[K]
            net = scale * (d['cg'] - d['pg'])                       # +call gamma, -put gamma (convention dealer)
            dex = 100.0 * spot * (d['cdex'] + d['pdex'])
            rows.append({'strike': K, 'gex': net,
                         'callG': scale * d['cg'], 'putG': -scale * d['pg'],
                         'callDex': 100.0 * spot * d['cdex'], 'putDex': 100.0 * spot * d['pdex'],
                         'callVol': d['cvol'], 'putVol': -d['pvol'],
                         'callOI': d['coi'], 'putOI': d['poi']})

        net_gex = sum(r['gex'] for r in rows)
        # Call wall / Put wall = strike au plus gros gamma*OI (call au-dessus, put en-dessous)
        call_cands = [r for r in rows if r['strike'] >= spot] or rows
        put_cands = [r for r in rows if r['strike'] <= spot] or rows
        call_wall = max(call_cands, key=lambda r: r['callG'])['strike']
        put_wall = max(put_cands, key=lambda r: -r['putG'])['strike']
        # Gamma flip = strike où le GEX cumulé croise zéro
        cum, flip = 0.0, None
        prevK, prevCum = strikes[0], 0.0
        for r in rows:
            newCum = cum + r['gex']
            if flip is None and ((cum <= 0 < newCum) or (cum >= 0 > newCum)) and cum != newCum:
                frac = -cum / (newCum - cum)
                flip = prevK + (r['strike'] - prevK) * frac
            prevK, cum = r['strike'], newCum
        if flip is None:
            flip = put_wall if net_gex > 0 else call_wall

        regime = 'POSITIF' if net_gex > 0 else 'NÉGATIF'
        call_dex = sum(r['callDex'] for r in rows)
        put_dex = sum(r['putDex'] for r in rows)
        dex_ratio = abs(call_dex / put_dex) if put_dex else 0

        state.update({
            'sym': SYMBOL, 'spot': round(spot, 2),
            'net_gex': net_gex, 'regime': regime,
            'call_wall': round(call_wall, 2), 'put_wall': round(put_wall, 2),
            'gamma_flip': round(flip, 2),
            'call_dex': call_dex, 'put_dex': put_dex, 'dex_ratio': round(dex_ratio, 2),
            'rows': rows, 'exps': exps,
            'updated': datetime.now().strftime('%H:%M:%S'), 'error': None,
        })
    except Exception as e:
        state['error'] = f'{type(e).__name__}: {e}'


def _loop():
    while True:
        compute()
        time.sleep(REFRESH_SEC)


@app.route('/data')
def data():
    return jsonify(state)


@app.route('/')
def index():
    return PAGE


PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Track GEX</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#070707;color:#cfe;font-family:ui-monospace,SFMono-Regular,Consolas,Menlo,monospace;font-size:13px}
.wrap{max-width:1180px;margin:0 auto;padding:16px}
.hd{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1a3a2a;padding-bottom:10px}
.hd h1{font-size:17px;letter-spacing:2px;margin:0;color:#7CFFB2}
.muted{color:#5a7a6a;font-size:12px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#34d399;margin-right:6px;animation:p 1.6s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.banner{margin:14px 0;padding:12px 16px;border:1px solid;border-radius:8px;font-size:15px;letter-spacing:1px}
.bp{border-color:#1f6f4a;background:#08160f;color:#7CFFB2}
.bn{border-color:#7a2230;background:#170a0d;color:#ff8198}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.kpi{background:#0a0f0c;border:1px solid #15251c;border-radius:8px;padding:11px 13px}
.kpi .l{font-size:11px;color:#5a7a6a;letter-spacing:1px}
.kpi .v{font-size:20px;font-weight:600;margin-top:3px}
.grid{display:grid;grid-template-columns:1fr;gap:14px;margin-top:8px}
.panel{background:#0a0f0c;border:1px solid #15251c;border-radius:10px;padding:14px}
.panel h2{font-size:12px;color:#7CFFB2;letter-spacing:1.5px;margin:0 0 10px}
.pb{display:flex;gap:10px;align-items:flex-start;margin:9px 0;font-size:12.5px;line-height:1.5}
.pb b{color:#7CFFB2}
.tag{color:#5a7a6a}
canvas{max-width:100%}
</style></head><body><div class="wrap">
<div class="hd"><h1>◣ TRACK · GEX <span class="muted" id="sym"></span></h1>
<div class="muted"><span class="dot"></span><span id="clock"></span> · maj <span id="updated">—</span></div></div>
<div id="err" class="muted" style="color:#ff8198"></div>
<div id="banner" class="banner bp">…chargement de la chaîne d'options…</div>
<div class="kpis" id="kpis"></div>
<div class="grid">
  <div class="panel"><h2>NET GEX PAR STRIKE</h2><div style="height:300px"><canvas id="gexChart"></canvas></div></div>
  <div class="panel"><h2>▶ TRADE PLAYBOOK</h2><div id="pb"></div></div>
</div>
<p class="muted" style="margin-top:16px">GEX = Σ γ·OI·100·spot²·0.01 (signe +call / −put). Walls = strike au plus gros γ·OI. Flip = GEX cumulé = 0. Données yfinance différées. PAPER / NOT FINANCIAL ADVICE.</p>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
function fm(n){const a=Math.abs(n);return (n<0?'-':'')+(a>=1e9?(a/1e9).toFixed(2)+'B':a>=1e6?(a/1e6).toFixed(1)+'M':a>=1e3?(a/1e3).toFixed(0)+'k':a.toFixed(0))}
function tick(){document.getElementById('clock').textContent=new Date().toLocaleTimeString('fr-FR')}
setInterval(tick,1000);tick();
let chart=null;
async function refresh(){
 try{
  const d=await(await fetch('/data')).json();
  document.getElementById('sym').textContent=d.sym||'';
  document.getElementById('updated').textContent=d.updated||'—';
  document.getElementById('err').textContent=d.error?('⚠ '+d.error):'';
  if(!d.rows){return}
  const pos=d.regime==='POSITIF';
  const b=document.getElementById('banner');b.className='banner '+(pos?'bp':'bn');
  b.textContent=(pos?'🟢 GAMMA POSITIF':'🔴 GAMMA NÉGATIF')+' — '+fm(d.net_gex)+' net GEX · '+(pos?'dealers AMORTISSENT (mean-reversion)':'dealers AMPLIFIENT (momentum/volatil)');
  document.getElementById('kpis').innerHTML=[
    ['SPOT','$'+d.spot,'#cfe'],['CALL WALL','$'+d.call_wall,'#5BD3FF'],
    ['PUT WALL','$'+d.put_wall,'#C792EA'],['GAMMA FLIP','$'+d.gamma_flip,'#FFD166'],
    ['NET GEX',fm(d.net_gex),pos?'#7CFFB2':'#ff8198']
  ].map(k=>`<div class="kpi"><div class="l">${k[0]}</div><div class="v" style="color:${k[2]}">${k[1]}</div></div>`).join('');
  const rows=d.rows, labels=rows.map(r=>r.strike), gex=rows.map(r=>r.gex);
  const cols=gex.map(v=>v>=0?'#34d399':'#f87171');
  if(chart)chart.destroy();
  chart=new Chart(document.getElementById('gexChart'),{type:'bar',
    data:{labels:labels,datasets:[{data:gex,backgroundColor:cols,borderWidth:0}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},
      tooltip:{callbacks:{label:c=>'GEX '+fm(c.raw)}}},
      scales:{x:{ticks:{color:'#5a7a6a',maxTicksLimit:14},grid:{color:'#11201a'}},
              y:{ticks:{color:'#5a7a6a',callback:v=>fm(v)},grid:{color:'#11201a'}}}}});
  const pb=document.getElementById('pb');
  pb.innerHTML=pos?`
    <div class="pb"><b>🟢 RÉGIME</b><span>Gamma positif — le hedging dealer AMORTIT les mouvements. Mean-reversion favorisée, range probable.</span></div>
    <div class="pb"><b>📌 RANGE</b><span>Vendre la résistance vers <b>Call Wall $${d.call_wall}</b>, acheter le support vers <b>Put Wall $${d.put_wall}</b> — les dealers défendent ces murs.</span></div>
    <div class="pb"><b>🚀 BREAKOUT</b><span>Cassure + clôture au-dessus de <b>$${d.call_wall}</b> = squeeze gamma possible. Au-dessous du <b>Flip $${d.gamma_flip}</b> = bascule en gamma négatif (accélération).</span></div>
    <div class="pb"><b>🛡 SUPPORT</b><span>Put Wall $${d.put_wall} = zone d'achat des dealers.</span></div>`:`
    <div class="pb"><b>🔴 RÉGIME</b><span>Gamma négatif — le hedging dealer AMPLIFIE les mouvements. Momentum/volatilité, mouvements rapides.</span></div>
    <div class="pb"><b>🚀 MOMENTUM</b><span>Suivre la tendance : au-dessus du <b>Flip $${d.gamma_flip}</b> vise <b>$${d.call_wall}</b> ; en-dessous, accélération baissière vers <b>$${d.put_wall}</b>.</span></div>
    <div class="pb"><b>⚠ VOLATIL</b><span>Mouvements amplifiés — éviter de fader, réduire la taille. Le Flip $${d.gamma_flip} est le pivot clé.</span></div>`;
 }catch(e){document.getElementById('err').textContent='⚠ '+e}
}
setInterval(refresh,6000);refresh();
</script></body></html>"""

if __name__ == '__main__':
    threading.Thread(target=_loop, daemon=True).start()
    print('Track GEX -> http://localhost:5001  (Ctrl+C pour arreter)')
    app.run(host='127.0.0.1', port=5001, debug=False, use_reloader=False)
