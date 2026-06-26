"""
terminal.py — TRACK TERMINAL (cockpit web unifié, local, live).

Un seul terminal : watchlist scannée + classée  →  clic sur un titre  →  fiche
détaillée (signaux techniques, score Track, IV / IV-rank, earnings, plan de
trade entrée/stop/cibles, mini-chart)  +  GEX (positionnement dealers) à la
demande. Esthétique sombre pro. Auto-rafraîchi.

Lancer :  py terminal.py   →   http://localhost:5002
Données :  yfinance (différé ~15 min — OK swing). Greeks/GEX = Black-Scholes maison.
⛔ ANALYSE ONLY — aucun ordre, aucune exécution. NOT FINANCIAL ADVICE.
"""
import os
import math
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf
from flask import Flask, jsonify, redirect

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))   # charge la clé API si présente (jamais commitée)
except Exception:
    pass

from elio import scoring, config, options, ai, daily, anomalies, sectors, research, market, weekly, fundamentals, engine, ibkr

DAILY_PREV_PATH = os.path.join(os.path.dirname(__file__), 'daily_prev.json')  # baseline diff jour/jour
WEEKLY_PATH = os.path.join(os.path.dirname(__file__), 'weekly_snapshot.json')  # sélection hebdo FIGÉE

WATCHLIST = ['AAPL', 'NVDA', 'MSFT', 'META', 'GOOGL', 'AMZN', 'AVGO', 'TSLA',
             'NFLX', 'AMD', 'CRM', 'COST', 'LLY', 'JPM', 'V', 'MA', 'HD',
             'UNH', 'XOM', 'WMT', 'PLTR', 'NOW', 'TSM', 'ASML', 'ANET', 'MU',
             'QCOM', 'ARM', 'SMCI', 'PANW', 'SNOW', 'CRWD', 'UBER', 'ABNB',
             'SHOP', 'COIN', 'MSTR', 'INTU', 'ORCL', 'ADBE', 'AMAT', 'MRVL',
             'DELL', 'CEG', 'VRT',
             # momentum / « qui cartonnent » (MAJ 26.06.2026) : AI infra, power, fintech, adtech, connectivité IA
             'HOOD', 'AXON', 'NET', 'MELI', 'RDDT', 'APP', 'ISRG',
             'GEV', 'VST', 'ALAB', 'CRDO', 'NBIS']
# ─── UNIVERS ÉLARGI : ~110 plus grosses capitalisations US (S&P 500 / Nasdaq / Dow) ───
# scanné automatiquement (cockpit, watchlist). Le streaming IBKR live, les news et les
# fondamentaux restent sur le core WATCHLIST (57) → limites IBKR + perfs respectées.
_BIG_EXTRA = [
    'BRK-B', 'JNJ', 'PG', 'KO', 'PEP', 'ABBV', 'MRK', 'TMO', 'ABT', 'DHR', 'PFE', 'BMY',
    'AMGN', 'GILD', 'VRTX', 'CVS', 'MDT',
    'BAC', 'WFC', 'GS', 'MS', 'C', 'AXP', 'SCHW', 'BLK', 'SPGI', 'CB', 'PGR', 'BX',
    'CSCO', 'IBM', 'INTC', 'TXN', 'ADI', 'LRCX', 'KLAC', 'MCHP', 'NXPI',
    'DIS', 'CMCSA', 'T', 'VZ', 'TMUS',
    'CAT', 'BA', 'HON', 'GE', 'RTX', 'LMT', 'DE', 'MMM', 'UPS', 'ETN',
    'CVX', 'COP', 'SLB',
    'MCD', 'NKE', 'SBUX', 'LOW', 'TJX', 'BKNG', 'PM', 'MO',
    'GM', 'F', 'PYPL', 'LIN', 'ACN', 'TXT',
]
# ─── VALEURS « TREND » : celles qui font le buzz / momentum / fast movers ───
# IA & quantum, nucléaire/énergie IA, crypto-mining, fintech, EV, meme stocks,
# espace/défense, IPO chaudes. Très volatiles — pour repérer ce qui bouge.
_TREND_EXTRA = [
    'SOFI', 'AFRM', 'UPST', 'DKNG', 'RBLX', 'U', 'PINS', 'SNAP', 'LYFT',
    'RIVN', 'LCID', 'NIO', 'GME', 'BABA', 'NU', 'GRAB',
    'MARA', 'RIOT', 'CLSK', 'WULF', 'CIFR',
    'IONQ', 'RGTI', 'QBTS', 'SOUN', 'BBAI', 'AI', 'PATH', 'DDOG', 'ZS', 'TTD',
    'HIMS', 'TEM', 'CRWV', 'CART', 'CAVA', 'DJT',
    'RKLB', 'OKLO', 'SMR', 'LUNR', 'ACHR', 'JOBY',
    'PLUG', 'FSLR', 'ENPH', 'RUN', 'DASH', 'ROKU', 'ABNB',
]
UNIVERSE = list(dict.fromkeys(WATCHLIST + _BIG_EXTRA + _TREND_EXTRA))   # dédupliqué, ordre préservé
# ─── COURS EN DIRECT IBKR (au bureau) : limite ~100 lignes de données IBKR ───
# Priorité : core 57 + fast movers (trend) + plus grosses caps, plafonné à 95.
# Les titres au-delà gardent leur dernier cours yfinance (scan), pas le tick live.
LIVE_SYMBOLS = list(dict.fromkeys(WATCHLIST + _TREND_EXTRA + _BIG_EXTRA))[:95]
TREND_SET = set(_TREND_EXTRA)   # valeurs « buzz / fast movers » → badge 🔥 dans l'UI
BENCH = 'SPY'
R = 0.045
# IBKR désactivé sur le cloud (pas de TWS) → met NO_IBKR=1 en variable d'env
IBKR_ENABLED = os.environ.get('NO_IBKR') != '1'
REFRESH_SEC = 120   # ~170 titres scannés (core + big caps + trend) → intervalle large pour le plan gratuit

app = Flask(__name__)
scan_state = {'rows': [], 'detail': {}, 'portfolio': None, 'options_board': [], 'daily': None,
              'anomalies': [], 'sectors': [], 'market_ctx': None, 'fundamentals': None, 'indices': [],
              'recommendations': [], 'updated': None, 'error': None}


# ─── helpers numériques ───────────────────────────────────────────────────
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


def _rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _atr(df, n=14):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _adx(df, n=14):
    """Force de tendance (ADX). Élevé = tendance directionnelle ; bas = range/bruit."""
    h, l, c = df['High'], df['Low'], df['Close']
    up, dn = h.diff(), -l.diff()
    plus = ((up > dn) & (up > 0)) * up
    minus = ((dn > up) & (dn > 0)) * dn
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return float(dx.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])


def _npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def _gamma(S, K, T, sig):
    if T <= 0 or sig <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (R + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    return _npdf(d1) / (S * sig * math.sqrt(T))


def market_status():
    """Phase de marché US (heure de New York) : pré-marché 4h-9h30, séance 9h30-16h,
    après-bourse 16h-20h, sinon fermé. IBKR fournit les cours en pré/après-bourse."""
    try:
        et = datetime.now(ZoneInfo('America/New_York'))
        t = et.hour * 60 + et.minute
        wd = et.weekday() < 5
        if wd and 570 <= t < 960:
            session = 'open'        # 9:30 → 16:00
        elif wd and 240 <= t < 570:
            session = 'pre'         # 4:00 → 9:30  (avant-bourse)
        elif wd and 960 <= t < 1200:
            session = 'after'       # 16:00 → 20:00 (après-bourse)
        else:
            session = 'closed'
        return {'open': session == 'open', 'session': session, 'et': et.strftime('%H:%M ET')}
    except Exception:
        return {'open': False, 'session': 'closed', 'et': '—'}


# ─── analyse par titre (sur OHLCV daily) ─────────────────────────────────
def analyse(df, bench_ret):
    c = df['Close'].dropna()
    last = float(c.iloc[-1])
    e20 = float(c.ewm(span=20).mean().iloc[-1])
    e50 = float(c.ewm(span=50).mean().iloc[-1])
    e200 = float(c.ewm(span=200).mean().iloc[-1])
    s50 = float(c.rolling(50).mean().iloc[-1])
    s200 = float(c.rolling(200).mean().iloc[-1])
    s50p = float(c.rolling(50).mean().iloc[-2])
    s200p = float(c.rolling(200).mean().iloc[-2])
    atr = float(_atr(df).iloc[-1])
    rsi_s = _rsi(c)
    r = float(rsi_s.iloc[-1])
    roc = (last / float(c.iloc[-21]) - 1) * 100 if len(c) > 21 else 0.0
    vol = float(df['Volume'].iloc[-1])
    volavg = float(df['Volume'].tail(20).mean())
    hi = float(c.tail(252).max()); lo = float(c.tail(252).min())
    pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50.0

    stack = int(e20 > e50) + int(e50 > e200) + int(last > e50)
    trend = stack / 3 * 100
    sym_ret = (last / float(c.iloc[-63]) - 1) if len(c) > 63 else 0.0
    rs = float(np.clip(50 + (sym_ret - bench_ret) * 200, 0, 100))
    volx = vol / volavg if volavg else 1.0
    atr_pct = atr / last * 100 if last else 0.0
    ext_atr = (last - e20) / atr if atr else 0.0
    chg = (last / float(c.iloc[-2]) - 1) * 100 if len(c) > 1 else 0.0

    # RÉGIME : ADX (force de tendance) + Choppiness (bruit) → filtre les faux signaux en range
    adx = _adx(df)
    tr14 = pd.concat([df['High'] - df['Low'], (df['High'] - c.shift()).abs(),
                      (df['Low'] - c.shift()).abs()], axis=1).max(axis=1).tail(14).sum()
    rng14 = float(df['High'].tail(14).max() - df['Low'].tail(14).min())
    chop = float(100 * math.log10(tr14 / rng14) / math.log10(14)) if rng14 > 0 else 50.0
    regime = 'TREND' if adx >= 25 and chop < 50 else 'CHOP' if chop >= 60 else 'NEUTRAL'
    # DIVERGENCE RSI réelle (pente prix vs pente RSI sur 20 barres) — pas un proxy
    win = 20
    bear_div = (last >= float(c.iloc[-win:-1].max())) and (r < float(rsi_s.iloc[-win:-1].max()) - 3)
    bull_div = (last <= float(c.iloc[-win:-1].min())) and (r > float(rsi_s.iloc[-win:-1].min()) + 3)
    rsi_div = 'bear' if bear_div else 'bull' if bull_div else None

    # signaux booléens (style checklist)
    sig = {
        'above20': last > e20, 'above50': last > s50, 'above200': last > s200,
        'stacked': e20 > e50 > e200,
        'golden': (s50 > s200) and (s50p <= s200p or s50 > s200),
        'goldenNow': (s50p <= s200p) and (s50 > s200),
        'momCross': e20 > e50,
        'rsiBull': r >= 50, 'volUp': vol > volavg,
    }
    sigcount = sum(1 for k in ('above20', 'above50', 'above200', 'stacked', 'golden', 'momCross', 'volUp') if sig[k])

    # SCORING MODULAIRE (elio.scoring) : technique/momentum/fondamental/risque → global + grade + verdict
    ind = {'above20': sig['above20'], 'above50': sig['above50'], 'above200': sig['above200'],
           'stacked': sig['stacked'], 'golden': sig['golden'], 'rsi': r, 'roc': roc,
           'rs': rs, 'pos52': pos, 'volx': volx, 'atr_pct': atr_pct, 'ext_atr': ext_atr}
    sc = scoring.compose(ind)
    score, grade, mom = sc['global'], sc['grade'], sc['momentum']
    verdict = config.verdict(score, trend, regime)

    # PLAN — stop sur STRUCTURE (dernier swing-low réel), R:R réel vers la résistance
    low = df['Low']
    Np = 10
    piv = [i for i in range(len(low) - Np - 1, max(len(low) - 60, Np), -1)
           if float(low.iloc[i]) == float(low.iloc[i - Np:i + Np + 1].min())]
    swing_low = float(low.iloc[piv[0]]) if piv else float(low.tail(20).min())
    struct_stop = swing_low - 0.25 * atr
    stop = max(struct_stop, last - 2.5 * atr)              # respecte la structure, plafonne le risque
    if last - stop < 0.8 * atr:                            # stop trop serré (bruit) → plancher ATR
        stop, stop_type = last - 1.2 * atr, 'ATR (structure trop proche)'
    else:
        stop_type = 'structure' if struct_stop > last - 2.5 * atr else 'ATR (plafond risque)'
    risk = last - stop
    recent_high = float(df['High'].tail(40).max())
    rr_res = (recent_high - last) / risk if risk > 0 else 0.0
    headroom = (recent_high - last) / atr if atr else 0.0
    setup_quality = round(float(np.clip(40 + rr_res * 20 + min(headroom, 4) * 5
                                        - max(0.0, (last - recent_high) / atr) * 15, 0, 100)))
    plan = {'entry': round(last, 2), 'stop': round(stop, 2),
            'tp1': round(last + risk, 2), 'tp2': round(last + 2 * risk, 2),
            'tp3': round(last + 3 * risk, 2), 'rr': 3.0, 'atr': round(atr, 2),
            'stop_type': stop_type, 'stop_dist_atr': round(risk / atr, 2) if atr else 0,
            'resistance': round(recent_high, 2), 'rr_res': round(rr_res, 1),
            'setup_quality': setup_quality}

    # série pour le mini-chart (120 dernières barres)
    cc = c.tail(120)
    ema20s = cc.ewm(span=20).mean()
    sma50s = c.rolling(50).mean().tail(120)
    rsi120 = rsi_s.tail(120)
    series = {
        'dates': [d.strftime('%m-%d') for d in cc.index],
        'close': [round(float(x), 2) for x in cc.values],
        'ema20': [round(float(x), 2) for x in ema20s.values],
        'sma50': [None if math.isnan(x) else round(float(x), 2) for x in sma50s.values],
        'rsi': [None if math.isnan(x) else round(float(x), 1) for x in rsi120.values],
    }
    return {
        'price': round(last, 2), 'change': round(chg, 2), 'score': score, 'grade': grade, 'verdict': verdict,
        'trend': round(trend), 'mom': round(mom), 'rs': round(rs), 'rsi': round(r),
        'roc': round(roc, 1), 'pos52': round(pos), 'sigcount': sigcount,
        'ma20': round(e20, 2), 'ma50': round(s50, 2), 'ma200': round(s200, 2),
        'volx': round(volx, 2), 'atr_pct': round(atr_pct, 2), 'ext_atr': round(ext_atr, 2),
        'regime': regime, 'adx': round(adx), 'chop': round(chop), 'rsi_div': rsi_div,
        'setup_quality': setup_quality, 'confidence': sc.get('confidence'),
        'signals': sig, 'sub': sc,
        'plan': plan, 'series': series,
    }


def backtest(data, lb=126, top_n=5, smin=58, eq0=100000.0):
    """Forward-test PAPER : top N titres score>=smin, rebalance quotidien, signal d'HIER (zéro lookahead)."""
    closes, scores = {}, {}
    for sym in WATCHLIST:
        try:
            df = data[sym].dropna()
            if len(df) < 210:
                continue
            c = df['Close']
            e20, e50, e200 = c.ewm(span=20).mean(), c.ewm(span=50).mean(), c.ewm(span=200).mean()
            rsi = _rsi(c)
            roc = (c / c.shift(21) - 1) * 100
            trend = ((e20 > e50).astype(int) + (e50 > e200).astype(int) + (c > e50).astype(int)) / 3 * 100
            mom = (50 + (rsi - 50) * 0.6 + roc.clip(-25, 25)).clip(0, 100)
            scores[sym] = 0.55 * trend + 0.45 * mom
            closes[sym] = c
        except Exception:
            continue
    if len(closes) < 3:
        return None
    idx = None
    for s in closes:
        idx = closes[s].index if idx is None else idx.intersection(closes[s].index)
    idx = idx[-lb:]
    rets = pd.DataFrame({s: closes[s].pct_change() for s in closes}).reindex(idx).fillna(0.0)
    sig = pd.DataFrame({s: scores[s] for s in closes}).reindex(idx).shift(1)
    eq, dr, journal, entry_px = [eq0], [], [], {}
    prev = set()
    for date in idx:
        row = sig.loc[date].dropna()
        held = set(row[row >= smin].nlargest(top_n).index.tolist())
        r = float(rets.loc[date, list(held)].mean()) if held else 0.0
        r = 0.0 if math.isnan(r) else r
        dr.append(r)
        eq.append(eq[-1] * (1 + r))
        for s in held - prev:
            entry_px[s] = float(closes[s].loc[date])
            journal.append({'sym': s, 'action': 'BUY', 'date': date.strftime('%m-%d'), 'price': round(entry_px[s], 2), 'pnl': None})
        for s in prev - held:
            px = float(closes[s].loc[date])
            pnl = (px / entry_px.get(s, px) - 1) * 100 if entry_px.get(s) else 0
            journal.append({'sym': s, 'action': 'SELL', 'date': date.strftime('%m-%d'), 'price': round(px, 2), 'pnl': round(pnl, 2)})
        prev = held
    eq = eq[1:]
    drs = pd.Series(dr)
    peak = pd.Series(eq).cummax()
    closed = [j for j in journal if j['action'] == 'SELL']
    wins = [j for j in closed if (j['pnl'] or 0) > 0]
    return {
        'dates': [d.strftime('%Y-%m-%d') for d in idx], 'equity': [round(x) for x in eq],
        'balance': round(eq[-1], 2), 'total': round((eq[-1] / eq0 - 1) * 100, 2),
        'sharpe': round(float(drs.mean() / drs.std() * math.sqrt(252)), 2) if drs.std() else 0,
        'maxdd': round(float((pd.Series(eq) / peak - 1).min() * 100), 2),
        'ath': round((max(eq) / eq0 - 1) * 100, 2), 'atl': round((min(eq) / eq0 - 1) * 100, 2),
        'trades': len(closed), 'winrate': round(len(wins) / len(closed) * 100) if closed else 0,
        'holdings': sorted(prev), 'journal': list(reversed(journal))[:40], 'top_n': top_n,
    }


def _download_universe(tickers, period='1y', chunk=50):
    """Télécharge l'univers PAR LOTS (plus robuste/rapide qu'un seul gros appel
    sur le plan gratuit). Renvoie un dict {ticker: DataFrame} ; un lot ou un
    ticker en échec est simplement ignoré (jamais de plantage global)."""
    frames = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            dl = yf.download(part, period=period, interval='1d', progress=False,
                             auto_adjust=True, group_by='ticker', threads=True)
        except Exception:
            continue
        if dl is None or len(dl) == 0:
            continue
        for t in part:
            try:
                df = dl if len(part) == 1 else dl[t]
                if df is not None and not df.dropna().empty:
                    frames[t] = df
            except Exception:
                continue
    return frames


def scan():
    try:
        data = _download_universe(UNIVERSE + [BENCH, '^VIX', '^GSPC', '^IXIC', '^DJI', '^RUT'])
        if BENCH not in data:
            return
        bc = data[BENCH]['Close'].dropna()
        bench_ret = (float(bc.iloc[-1]) / float(bc.iloc[-63]) - 1) if len(bc) > 63 else 0.0
        rows, detail = [], {}
        for sym in UNIVERSE:
            try:
                df = data[sym].dropna()
                if len(df) < 60:
                    continue
                d = analyse(df, bench_ret)
                detail[sym] = d
                d['trend'] = sym in TREND_SET
                rows.append({'symbol': sym, 'price': d['price'], 'change': d['change'],
                             'score': d['score'], 'grade': d['grade'], 'verdict': d['verdict'],
                             'sigcount': d['sigcount'], 'trend': sym in TREND_SET})
            except Exception:
                continue
        rows.sort(key=lambda x: x['score'], reverse=True)
        try:
            pf = backtest(data)
        except Exception:
            pf = scan_state.get('portfolio')
        breadth = round(sum(1 for r in rows if r['verdict'] == 'BUY') / len(rows) * 100) if rows else 0
        spy = ({'price': round(float(bc.iloc[-1]), 2),
                'change': round((float(bc.iloc[-1]) / float(bc.iloc[-2]) - 1) * 100, 2)} if len(bc) > 1 else None)
        # INDICES PRINCIPAUX (bande du haut)
        indices = []
        for _tk, _nm in [('^GSPC', 'S&P 500'), ('^IXIC', 'Nasdaq'), ('^DJI', 'Dow Jones'), ('^RUT', 'Russell 2000'), ('^VIX', 'VIX')]:
            try:
                _cc = data[_tk]['Close'].dropna()
                indices.append({'name': _nm, 'price': round(float(_cc.iloc[-1]), 2),
                                'change': round((float(_cc.iloc[-1]) / float(_cc.iloc[-2]) - 1) * 100, 2),
                                'vix': _tk == '^VIX'})
            except Exception:
                pass
        # BRIEF QUOTIDIEN + ANOMALIES + SECTEURS (tout depuis rows/detail, zéro réseau)
        prev = {}
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            prev = daily.load_baseline(DAILY_PREV_PATH, today)
            daily_brief = daily.build_daily(rows, detail, prev=prev)
            daily.save_state(DAILY_PREV_PATH, today, daily.snapshot(rows, detail))
        except Exception:
            daily_brief = scan_state.get('daily')
        try:
            anoms = anomalies.detect_anomalies(rows, detail)
        except Exception:
            anoms = scan_state.get('anomalies') or []
        try:
            secs = sectors.build_sectors(rows, detail, prev=prev)
        except Exception:
            secs = scan_state.get('sectors') or []
        try:
            vix_close = data['^VIX']['Close']
        except Exception:
            vix_close = None
        try:
            mctx = market.context(data[BENCH].dropna(), vix_close, rows, detail, secs)
        except Exception:
            mctx = scan_state.get('market_ctx')
        # RECOMMANDATIONS : moteur de décision IBKR (/40 + niveau + timing) sur tout l'univers
        recs = []
        fstate = scan_state.get('fundamentals') or {}
        fsym = fstate.get('by_sym') or {}
        fsec = fstate.get('by_sector') or {}
        # meilleur contrat CALL par titre (depuis l'options_board) pour les composantes option
        board_best = {}
        for c in (scan_state.get('options_board') or []):
            if c.get('type') != 'CALL':
                continue
            s = c.get('sym')
            cur = board_best.get(s)
            rank = {'long': 3, 'moyen': 2, 'court': 1}.get(c.get('bucket'), 0) * 100 + (c.get('pop') or 0)
            if not cur or rank > cur[0]:
                board_best[s] = (rank, c)
        for r in rows:
            sym = r['symbol']
            d = detail.get(sym)
            if not d:
                continue
            fu = fsym.get(sym) or {}
            sec = fu.get('sector') or sectors.SECTOR_MAP.get(sym)
            med = (fsec.get(sec) or {}).get('median_pe')
            opt = {'valuation': fundamentals.valuation(fu.get('pe'), med)} if fu.get('pe') else {}
            if sym in board_best:
                opt['best_pick'] = board_best[sym][1]
            v = ibkr.verdict(d, opt, fu)
            dec = engine.decide(d)
            if v:
                recs.append({'symbol': sym, 'price': r['price'], 'change': r['change'],
                             'grade': r['grade'], 'sector': sectors.SECTOR_MAP.get(sym),
                             'decision': v['decision'], 'tone': v['tone'], 'niveau': v['niveau'],
                             'score40': v['score40'], 'alloc': v['alloc'], 'timing': v['timing']['state'],
                             'raison': v['raison'], 'action': v['action'],
                             'pros': (dec or {}).get('pros', [])[:2]})
        recs.sort(key=lambda x: -x['score40'])
        scan_state.update({'rows': rows, 'detail': detail, 'portfolio': pf, 'daily': daily_brief,
                           'anomalies': anoms, 'sectors': secs, 'market_ctx': mctx, 'indices': indices,
                           'recommendations': recs,
                           'breadth': breadth, 'spy': spy, 'market': market_status(),
                           'universe_n': len(UNIVERSE), 'scanned_n': len(rows),
                           'updated': datetime.now().strftime('%H:%M:%S'), 'error': None})
    except Exception as e:
        scan_state['error'] = f'{type(e).__name__}: {e}'


def _loop():
    while True:
        scan()
        time.sleep(REFRESH_SEC)


def _opt_loop():
    """Construit le board options (chaînes réelles, lent) dès que le scan est prêt, puis toutes les 5 min."""
    while True:
        if scan_state.get('rows') and scan_state.get('detail'):
            try:
                scan_state['options_board'] = options.build_board(scan_state['detail'], scan_state['rows'], max_calls=14, max_puts=4)
            except Exception:
                pass
            time.sleep(300)
        else:
            time.sleep(8)


# ─── NEWS LIVE : flux marché rafraîchi chaque minute ─────────────────────
news_state = {'items': [], 'updated': None}
NEWS_SYMS = ['SPY', 'QQQ', 'NVDA', 'AAPL', 'MSFT', 'META', 'AMZN', 'TSLA', 'AMD', 'GOOGL', 'AVGO', 'PLTR']


def _news_loop():
    while True:
        try:
            seen, feed = set(), []
            for sym in NEWS_SYMS:
                try:
                    its = options.news_for(yf.Ticker(sym), n=4)
                    its, _ = ai.fr_news(sym, its)
                    for it in its:
                        k = (it.get('title') or '')[:60]
                        if k and k not in seen:
                            seen.add(k)
                            feed.append({**it, 'sym': sym})
                except Exception:
                    continue
            feed.sort(key=lambda x: x.get('time') or '', reverse=True)
            news_state['items'] = feed[:45]
            news_state['updated'] = datetime.now().strftime('%H:%M:%S')
        except Exception:
            pass
        time.sleep(60)


# ─── CALENDRIER EARNINGS : prochaines dates pour les 45 (rafraîchi /3h) ───
cal_state = {'items': [], 'updated': None}


def _cal_loop():
    while True:
        if scan_state.get('rows'):
            items = []
            for sym in WATCHLIST:
                try:
                    cal = yf.Ticker(sym).calendar
                    ed = cal.get('Earnings Date') if isinstance(cal, dict) else None
                    ed = ed[0] if isinstance(ed, (list, tuple)) and ed else ed
                    if ed is not None:
                        es = str(ed)[:10]
                        d = (datetime.strptime(es, '%Y-%m-%d') - datetime.now()).days
                        det = scan_state['detail'].get(sym, {})
                        items.append({'sym': sym, 'date': es, 'dte': d,
                                      'score': det.get('score'), 'grade': det.get('grade'),
                                      'verdict': det.get('verdict')})
                except Exception:
                    continue
            items.sort(key=lambda x: x['dte'] if x['dte'] is not None else 9999)
            cal_state['items'] = [x for x in items if x['dte'] is not None and x['dte'] >= -2]
            cal_state['updated'] = datetime.now().strftime('%H:%M %d/%m')
            time.sleep(3 * 3600)
        else:
            time.sleep(10)


# ─── FONDAMENTAUX : P/E par titre + médianes secteur (lent, rafraîchi /6h) ───
def _fund_loop():
    while True:
        if scan_state.get('rows'):
            try:
                scan_state['fundamentals'] = fundamentals.build(WATCHLIST)
            except Exception:
                pass
            time.sleep(6 * 3600)
        else:
            time.sleep(12)


# ─── WATCHLIST DE LA SEMAINE : sélection FIGÉE le lundi, options associées ──
weekly_state = {'data': None, 'updated': None, 'regenerated': False}


def _earnings_map():
    """{sym: dte} depuis le calendrier earnings collecté (cal_state) — pour écarter
    les titres dont les résultats tombent dans la semaine (gap / IV-crush)."""
    return {x['sym']: x['dte'] for x in (cal_state.get('items') or [])
            if x.get('sym') and x.get('dte') is not None}


def _weekly_loop():
    """Construit/charge la sélection hebdo. Le ROSTER est figé pour la semaine ISO
    (snapshot persisté) ; on rafraîchit seulement les chiffres vivants à chaque tour."""
    while True:
        if scan_state.get('rows') and scan_state.get('detail'):
            try:
                snap, regen = weekly.get_or_build(
                    WEEKLY_PATH, scan_state['rows'], scan_state['detail'],
                    earnings=_earnings_map(), n=6, with_options=True)
                weekly_state.update({'data': snap, 'regenerated': regen,
                                     'updated': datetime.now().strftime('%H:%M:%S')})
            except Exception:
                pass
            time.sleep(300)        # options réelles = lent → toutes les 5 min
        else:
            time.sleep(8)


# ─── options / GEX / earnings à la demande ───────────────────────────────
def options_pack(sym):
    out = {'sym': sym, 'iv': None, 'ivrank': None, 'earnings': None, 'error': None,
           'name': None, 'sector': None, 'mcap': None, 'pe': None, 'beta': None,
           'news': [], 'news_why': None, 'contracts': [],
           'net_gex': None, 'regime': None, 'call_wall': None, 'put_wall': None, 'gamma_flip': None}
    try:
        tk = yf.Ticker(sym)
        try:
            spot = float(tk.fast_info['lastPrice'])
        except Exception:
            spot = float(tk.history(period='1d')['Close'].iloc[-1])
        out['spot'] = round(spot, 2)
        # infos société EN DIRECT (yfinance .info — lent/flaky → try)
        try:
            info = tk.info or {}
            out['name'] = info.get('shortName') or info.get('longName')
            out['sector'] = info.get('sector')
            out['mcap'] = info.get('marketCap')
            out['pe'] = info.get('trailingPE')
            out['beta'] = info.get('beta')
        except Exception:
            pass
        # comparaison fondamentale vs MÉDIANE DU SECTEUR (cache fundamentals)
        _fd = scan_state.get('fundamentals') or {}
        _fs = (_fd.get('by_sym') or {}).get(sym) or {}
        _fsec = (_fd.get('by_sector') or {}).get(_fs.get('sector') or out.get('sector')) or {}
        out['fund'] = _fs
        out['sector_median_pe'] = _fsec.get('median_pe')
        out['sector_median_margin'] = _fsec.get('median_margin')
        out['sector_median_growth'] = _fsec.get('median_growth')
        out['valuation'] = fundamentals.valuation(_fs.get('pe') or out.get('pe'), _fsec.get('median_pe'))
        # news (pourquoi ça bouge) + traduction FR live
        out['news'] = options.news_for(tk)
        out['news'], out['news_why'] = ai.fr_news(sym, out['news'])
        # HV-rank proxy (yfinance ne donne pas l'IV-rank historique)
        h = tk.history(period='1y')['Close']
        ret = np.log(h / h.shift(1)).dropna()
        hv = ret.rolling(20).std() * math.sqrt(252) * 100
        out['ivrank'] = round(float((hv.rank(pct=True).iloc[-1]) * 100)) if len(hv.dropna()) else None
        # earnings (+ jours avant résultats, pour la pénalité options court terme)
        edte = None
        try:
            cal = tk.calendar
            ed = None
            if isinstance(cal, dict):
                ed = cal.get('Earnings Date')
                ed = ed[0] if isinstance(ed, (list, tuple)) and ed else ed
            out['earnings'] = str(ed)[:10] if ed is not None else '—'
            if ed is not None:
                try:
                    edte = (datetime.strptime(str(ed)[:10], '%Y-%m-%d') - datetime.now()).days
                    edte = edte if edte >= 0 else None
                except Exception:
                    edte = None
        except Exception:
            out['earnings'] = '—'
        out['earnings_dte'] = edte
        # meilleures options CALL par bucket (court/moyen/long) pour CE titre
        out['contracts'] = options.best_for_symbol(sym, spot, spot * 1.12, 'call', max_n=2,
                                                    buckets=('court', 'moyen', 'long'), earnings_dte=edte)
        out['best_pick'] = options.recommend(out['contracts'])   # LA meilleure entre les 3
        _d = scan_state['detail'].get(sym)
        out['chart_read'] = research.chart_read(_d)               # analyse graphique (texte FR)
        out['chart_verdict'] = research.chart_verdict(_d)
        out['decision'] = engine.decide(_d, out)                  # MOTEUR DE DÉCISION (synthèse)
        _fu = ((scan_state.get('fundamentals') or {}).get('by_sym') or {}).get(sym) or {}
        out['ibkr'] = ibkr.verdict(_d, out, _fu)                  # VERDICT IBKR (/40 + niveau + timing)
        # OPTIONS DESK : scénarios + breakeven + expected move sur le contrat recommandé
        if out.get('best_pick'):
            _plan = (_d or {}).get('plan') or {}
            _lv = {'stop': _plan.get('stop'), 'tp1': _plan.get('tp1'),
                   'tp2': _plan.get('tp2'), 'tp3': _plan.get('tp3')}
            out['scenarios'] = options.scenarios(out['best_pick'], spot, _lv)
            out['breakeven'] = options.breakeven_check(out['best_pick'], spot)
            _em = out['best_pick'].get('em_pct') or 0
            out['expected_move'] = {'pct': _em, 'lo': round(spot * (1 - _em / 100), 2),
                                    'hi': round(spot * (1 + _em / 100), 2)}
        # chaîne d'options → ATM IV + GEX
        exps = list(tk.options)[:2]
        lo, hi = spot * 0.9, spot * 1.1
        agg, atm_ivs = {}, []
        now = datetime.now()
        for exp in exps:
            T = max((datetime.strptime(exp, '%Y-%m-%d') - now).days, 0) / 365.0
            T = max(T, 0.5 / 365.0)
            ch = tk.option_chain(exp)
            for is_call, dfo in ((True, ch.calls), (False, ch.puts)):
                for _, row in dfo.iterrows():
                    K = _f(row['strike'])
                    if K < lo or K > hi:
                        continue
                    iv = _f(row.get('impliedVolatility')); oi = _i(row.get('openInterest'))
                    if iv <= 0 or oi <= 0:
                        continue
                    if is_call and abs(K - spot) <= spot * 0.03 and 0.03 < iv < 3.0:
                        atm_ivs.append(iv)
                    g = _gamma(spot, K, T, iv)
                    d = agg.setdefault(K, {'cg': 0., 'pg': 0.})
                    d['cg' if is_call else 'pg'] += g * oi
        out['iv'] = round(float(np.median(atm_ivs)) * 100, 1) if atm_ivs else None
        if agg:
            ks = sorted(agg)
            scale = 100.0 * spot * spot * 0.01
            gx = [(K, scale * (agg[K]['cg'] - agg[K]['pg'])) for K in ks]
            net = sum(v for _, v in gx)
            out['net_gex'] = net
            out['regime'] = 'POSITIF' if net > 0 else 'NÉGATIF'
            out['call_wall'] = round(max((k for k in ks if k >= spot), default=ks[-1],
                                         key=lambda k: agg[k]['cg']), 2)
            out['put_wall'] = round(max((k for k in ks if k <= spot), default=ks[0],
                                        key=lambda k: agg[k]['pg']), 2)
            cum, flip = 0., None
            pk = ks[0]
            for K, v in gx:
                nc = cum + v
                if flip is None and cum * nc < 0:
                    flip = pk + (K - pk) * (-cum / (nc - cum))
                pk, cum = K, nc
            out['gamma_flip'] = round(flip, 2) if flip else (out['put_wall'] if net > 0 else out['call_wall'])
    except Exception as e:
        out['error'] = f'{type(e).__name__}: {e}'
    return out


@app.route('/scan')
def scan_ep():
    return jsonify({**scan_state, 'ai_on': ai.available()})


@app.route('/healthz')
@app.route('/api/healthz')
def healthz():
    """Health check (Render) — toujours 200 si le process répond. Indique l'état
    du scan sans bloquer. Aucune donnée sensible, lecture seule."""
    return jsonify({
        'status': 'ok',
        'ibkr_enabled': IBKR_ENABLED,
        'universe': len(UNIVERSE),
        'scanned': scan_state.get('scanned_n'),
        'last_scan': scan_state.get('updated'),
        'scan_error': scan_state.get('error'),
    }), 200


@app.route('/options/<sym>')
def opt_ep(sym):
    return jsonify(options_pack(sym.upper()))


# ─── IBKR LECTURE SEULE (ib_reader, readonly) — jamais d'ordre ──────────────
_ibkr_cache = {'ts': 0.0, 'data': None}
_IBKR_MODE = {7496: 'RÉEL (TWS)', 7497: 'PAPER (TWS)', 4001: 'RÉEL (Gateway)', 4002: 'PAPER (Gateway)'}


def _ibkr_worker(res):
    import asyncio
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())   # boucle dédiée à ce thread
    except Exception:
        pass
    try:
        from ib_reader import IBKRReader
    except Exception as e:
        res['error'] = f'ib_async non disponible ({type(e).__name__})'
        return
    r = IBKRReader()
    for port in (7497, 7496, 4002, 4001):                  # PAPER d'abord
        try:
            r.ib.connect('127.0.0.1', port, clientId=17, readonly=True, timeout=2)
            r.port = port
            break
        except Exception:
            continue
    if not r.ib.isConnected():
        res['error'] = "TWS/Gateway non lancé ou API désactivée (ports 7497/7496). Lance TWS en lecture seule + active l'API."
        return
    try:
        # lecture devise-consciente : on préfère la vraie devise (USD/CHF…), jamais la ligne 'BASE'
        summ, ccy = {}, 'USD'
        for row in r.ib.accountSummary():
            if row.tag not in summ or (row.currency and row.currency != 'BASE'):
                summ[row.tag] = row.value
            if row.tag == 'NetLiquidation' and row.currency and row.currency != 'BASE':
                ccy = row.currency
        res['connected'] = True
        res['mode'] = _IBKR_MODE.get(r.port, '?')
        res['account'] = (r.ib.managedAccounts() or ['—'])[0]

        def gf(k):
            try:
                return round(float(summ.get(k)))
            except Exception:
                return None
        res['net_liq'] = gf('NetLiquidation')
        res['cash'] = gf('TotalCashValue')
        res['buying_power'] = gf('BuyingPower')
        res['upnl'] = gf('UnrealizedPnL')
        res['currency'] = ccy
        df = r.positions()
        pos = []
        if df is not None and len(df):
            for _, p in df.iterrows():
                pos.append({'symbol': p.get('symbol'), 'qty': p.get('position'),
                            'avg_cost': round(float(p.get('avgCost') or 0), 2), 'sectype': p.get('secType')})
        res['positions'] = pos
    except Exception as e:
        res['error'] = f'lecture IBKR : {type(e).__name__}'
    finally:
        try:
            r.disconnect()
        except Exception:
            pass


def _ibkr_snapshot():
    if not IBKR_ENABLED:
        return {'connected': False, 'error': 'IBKR désactivé (cloud — pas de TWS)', 'mode': None,
                'account': None, 'net_liq': None, 'cash': None, 'buying_power': None,
                'upnl': None, 'currency': None, 'positions': []}
    now = time.time()
    if _ibkr_cache['data'] and now - _ibkr_cache['ts'] < 15:
        return _ibkr_cache['data']
    res = {'connected': False, 'error': None, 'mode': None, 'account': None, 'net_liq': None, 'cash': None,
           'buying_power': None, 'upnl': None, 'currency': None, 'positions': []}
    t = threading.Thread(target=_ibkr_worker, args=(res,), daemon=True)
    t.start()
    t.join(timeout=14)
    if t.is_alive() and not res['error']:
        res['error'] = 'connexion IBKR trop longue (timeout)'
    _ibkr_cache['data'] = res
    _ibkr_cache['ts'] = now
    return res


# ─── COURS EN DIRECT (flux IBKR permanent, lecture seule) ───────────────────
_live_quotes = {}                       # {sym: {last, change, bid, ask}}
_live_meta = {'connected': False, 'ts': 0.0, 'rt': False, 'n': 0}


def _store_ticker(t):
    """Range un ticker IBKR dans _live_quotes. Renvoie True si temps réel."""
    s = t.contract.symbol
    last = t.last if (t.last is not None and t.last == t.last and t.last > 0) else None
    close = t.close if (t.close is not None and t.close == t.close and t.close > 0) else None
    if last is None:
        last = close
    if last and close:
        _live_quotes[s] = {
            'last': round(last, 2),
            'change': round((last - close) / close * 100, 2),
            'bid': round(t.bid, 2) if (t.bid is not None and t.bid == t.bid and t.bid > 0) else None,
            'ask': round(t.ask, 2) if (t.ask is not None and t.ask == t.ask and t.ask > 0) else None,
        }
    return getattr(t, 'marketDataType', None) == 1


def _quotes_worker():
    """Maintient une connexion IBKR readonly et stream les cours en direct de la
    watchlist dans _live_quotes. ⛔ LECTURE SEULE — reqMktData uniquement, jamais d'ordre."""
    import asyncio
    while True:
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
            from ib_async import IB, Stock
            ib = IB()
            ok = False
            for port in (7496, 7497, 4001, 4002):
                try:
                    ib.connect('127.0.0.1', port, clientId=18, readonly=True, timeout=4)
                    ok = True
                    break
                except Exception:
                    continue
            if not ok:
                _live_meta['connected'] = False
                time.sleep(20)
                continue
            ib.reqMarketDataType(1)      # 1 = temps réel (bascule auto en différé si pas d'abonnement)
            cs = [Stock(s, 'SMART', 'USD') for s in LIVE_SYMBOLS]
            ib.qualifyContracts(*cs)
            valid = [c for c in cs if getattr(c, 'conId', 0)]
            _live_meta.update({'connected': True, 'n': len(valid)})
            while ib.isConnected():
                rt = False
                for i in range(0, len(valid), 8):    # lots de 8 = bon compromis sous la limite de lignes
                    try:                              # timeout : un symbole sans données ne bloque pas le lot
                        tickers = ib.run(asyncio.wait_for(ib.reqTickersAsync(*valid[i:i + 8]), 12))
                    except Exception as _e:
                        _live_meta['err'] = f'reqTickers: {type(_e).__name__}: {_e}'
                        continue
                    for t in tickers:
                        if _store_ticker(t):
                            rt = True
                    _live_meta.update({'ts': time.time(), 'rt': rt})   # frais dès le 1er lot
                    ib.sleep(0.2)
                ib.sleep(4)
        except Exception as _e:
            _live_meta['connected'] = False
            _live_meta['err'] = f'loop: {type(_e).__name__}: {_e}'
        time.sleep(15)


@app.route('/quotes')
def quotes_ep():
    fresh = (time.time() - _live_meta.get('ts', 0)) < 30
    return jsonify({'quotes': _live_quotes if fresh else {}, 'meta': _live_meta, 'fresh': fresh})


@app.route('/ibkr')
def ibkr_ep():
    return jsonify(_ibkr_snapshot())


@app.route('/manifest.webmanifest')
def manifest_ep():
    """Manifeste PWA → permet « Ajouter à l'écran d'accueil » sur iPhone/Android
    et l'ouverture en plein écran comme une vraie app."""
    return jsonify({
        'name': 'Trading Desk — Cockpit IBKR',
        'short_name': 'Trading Desk',
        'description': "Cockpit d'analyse trading (analyse only).",
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'orientation': 'portrait-primary',
        'background_color': '#0b0e14',
        'theme_color': '#0b0e14',
        'icons': [
            {'src': '/static/icon-180.png', 'sizes': '180x180', 'type': 'image/png', 'purpose': 'any maskable'},
        ],
    })


_SW_JS = r"""
const CACHE='td-shell-v2';
self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(CACHE).then(c=>c.addAll(['/manifest.webmanifest','/static/icon-180.png']).catch(()=>{})));});
self.addEventListener('activate',e=>{e.waitUntil((async()=>{const ks=await caches.keys();await Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)));await self.clients.claim();})());});
self.addEventListener('fetch',e=>{
  const req=e.request; if(req.method!=='GET')return;
  const url=new URL(req.url); if(url.origin!==location.origin)return;
  const cacheable=(req.mode==='navigate'||url.pathname.startsWith('/static')||url.pathname==='/manifest.webmanifest');
  e.respondWith((async()=>{
    const cache=await caches.open(CACHE);
    try{
      // network-first : on prefere TOUJOURS le frais ; repli cache si reseau lent (cold start) ou hors-ligne
      const net=await Promise.race([fetch(req),new Promise((_,rej)=>setTimeout(()=>rej(new Error('to')),4500))]);
      if(net&&net.ok&&cacheable)cache.put(req,net.clone());
      return net;
    }catch(err){
      const c=(await cache.match(req))||(req.mode==='navigate'?await cache.match('/'):null);
      return c||fetch(req);
    }
  })());
});
"""


@app.route('/sw.js')
def service_worker():
    """Service worker PWA (network-first + repli cache) — masque les cold starts
    Render. ⛔ Aucune donnee perso ici (favoris/notes restent en localStorage)."""
    return app.response_class(_SW_JS, mimetype='application/javascript',
                              headers={'Service-Worker-Allowed': '/', 'Cache-Control': 'no-cache'})


@app.route('/')
@app.route('/daily')
def home():
    return PAGE_DAILY


# Pages historiques fusionnées dans le dashboard → tout est sur une seule page.
# On garde les routes pour ne pas casser d'anciens liens, mais elles redirigent vers /.
@app.route('/analyse')
@app.route('/sectors')
@app.route('/news')
@app.route('/calendar')
@app.route('/semaine')
def _legacy_pages_redirect():
    return redirect('/', code=302)


@app.route('/options')
def options_desk_page():
    return PAGE_OPTIONS_DESK


@app.route('/news-feed')
def news_feed_ep():
    return jsonify({**news_state, 'ai_on': ai.available()})


@app.route('/cal-feed')
def cal_feed_ep():
    return jsonify(cal_state)


@app.route('/weekly-feed')
def weekly_feed_ep():
    return jsonify({**weekly_state, 'ai_on': ai.available()})


@app.route('/weekly-regen', methods=['POST', 'GET'])
def weekly_regen_ep():
    """Force la régénération de la sélection hebdo (ex. nouveau lundi manuel, ou
    après un gros changement de marché). Reste ANALYSE ONLY — recalcule un snapshot."""
    if not (scan_state.get('rows') and scan_state.get('detail')):
        return jsonify({'ok': False, 'error': 'scan pas encore prêt'})
    try:
        snap, _ = weekly.get_or_build(WEEKLY_PATH, scan_state['rows'], scan_state['detail'],
                                      earnings=_earnings_map(), n=6, with_options=True, force=True)
        weekly_state.update({'data': snap, 'regenerated': True,
                             'updated': datetime.now().strftime('%H:%M:%S')})
        return jsonify({'ok': True, 'week': snap.get('week'), 'n': snap.get('meta', {}).get('n')})
    except Exception as e:
        return jsonify({'ok': False, 'error': f'{type(e).__name__}: {e}'})


PAGE = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Trading Desk"><meta name="mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0b0e14"><link rel="apple-touch-icon" href="/static/icon-180.png"><link rel="manifest" href="/manifest.webmanifest"><title>TRACK TERMINAL</title>
<style>
*{box-sizing:border-box}html{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
body{margin:0;background:#070707;color:#e8e8e8;font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:13px}
.top{display:flex;justify-content:space-between;align-items:center;padding:12px 18px;border-bottom:1px solid #161b26;background:#0d0d0d}
.brand{font-size:16px;font-weight:700;letter-spacing:2px;color:#FF8C32}
.muted{color:#5b6678;font-size:12px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#FF8C32;margin-right:6px;animation:p 1.6s infinite}
.bubbles{display:flex;gap:8px;flex-wrap:wrap}
.bub{display:inline-flex;align-items:center;gap:6px;font-size:11px;padding:4px 11px;border-radius:20px;border:1px solid #161b26;background:#111111;color:#7b8696;letter-spacing:.3px;font-weight:600}
.bub .bdot{width:7px;height:7px;border-radius:50%;background:#5b6678}
.bub.on{border-color:#1f6f4a;color:#FF8C32}.bub.on .bdot{background:#FF8C32;animation:p 1.6s infinite}
.bub.off{border-color:#5a4a1a;color:#FFB23F}.bub.off .bdot{background:#FFB23F}
.strip{padding:8px 18px;background:#090b10;border-bottom:1px solid #161b26;font-size:13px;color:#9aa7bd;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.strip b{color:#FF8C32}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.layout{display:grid;grid-template-columns:260px 1fr;min-height:calc(100vh - 49px)}
.side{border-right:1px solid #161b26;overflow:auto;background:#090b10}
.side h3{font-size:11px;color:#5b6678;letter-spacing:1px;padding:12px 14px 6px;margin:0}
.wl{display:flex;justify-content:space-between;align-items:center;padding:9px 14px;cursor:pointer;border-left:2px solid transparent}
.wl:hover{background:#171717}.wl.sel{background:#171717;border-left-color:#FF8C32}
.wl .s{font-weight:600}.wl .r{display:flex;gap:8px;align-items:center}
.sc{font-weight:700;font-size:13px}
.bd{padding:6px 18px 30px}
.tabs{display:flex;gap:6px;margin:10px 0 2px}
.tab{background:#111111;border:1px solid #161b26;color:#9aa7bd;padding:7px 14px;border-radius:8px;font-size:12px;cursor:pointer;font-weight:600}
.tab.active{border-color:#FF8C32;color:#FF8C32}
.hdr{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:1px solid #161b26;padding:14px 0}
.hdr .nm{font-size:30px;font-weight:700;line-height:1}.hdr .tk{color:#5b6678;font-size:14px}
.hdr .px{font-size:30px;font-weight:700;text-align:right}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:16px 0}
.kpi{background:#111111;border:1px solid #161b26;border-radius:10px;padding:12px}
.kpi .l{font-size:11px;color:#5b6678;letter-spacing:.5px}.kpi .v{font-size:20px;font-weight:600;margin-top:4px}
.cols{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin:18px 0}
.col h4{font-size:11px;color:#5b6678;letter-spacing:1px;margin:0 0 8px}
.sg{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #11151e}
.ok{color:#FF8C32}.no{color:#EF4444}
.plan{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:8px 0 18px}
.pcell{background:#111111;border:1px solid #161b26;border-radius:8px;padding:9px;text-align:center}
.pcell .l{font-size:10px;color:#5b6678}.pcell .v{font-size:15px;font-weight:600;margin-top:2px}
.badge{padding:3px 9px;border-radius:6px;font-size:11px;font-weight:700}
.b-achat{background:#0f3d2e;color:#FF8C32}.b-surv{background:#3d340f;color:#FFB23F}.b-evit{background:#3d1414;color:#EF4444}
.up{color:#FF8C32}.dn{color:#EF4444}
.panel{background:#111111;border:1px solid #161b26;border-radius:10px;padding:14px;margin-top:8px}
.panel h4{font-size:11px;color:#FF8C32;letter-spacing:1px;margin:0 0 10px}
.gx{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px}
canvas{max-width:100%}
/* ============ THEME NEON (rouge / noir / vert / jaune + glow) ============ */
body{background:#070707}
.top{background:#0a0a0a;border-bottom:1px solid #FF8C3226}
.brand{color:#FF8C32;text-shadow:0 0 12px #FF8C32aa}
.strip{background:#0a0a0a;border-bottom:1px solid #FF8C321a}.strip b{color:#FF8C32;text-shadow:0 0 7px #FF8C3288}
.side{background:#0a0a0a;border-right:1px solid #FF8C321a}
.card,.kpi,.panel{background:#111111;border-color:#1a1a22}
.bub{background:#111111}
.bub.on{box-shadow:0 0 12px #FF8C323a}.bub.on .bdot{box-shadow:0 0 8px #FF8C32}
.b-achat{background:rgba(255,140,50,0.10);color:#FF8C32;border:1px solid #FF8C3266;box-shadow:0 0 9px #FF8C3233}
.b-evit{background:rgba(255,43,78,0.10);color:#EF4444;border:1px solid #EF444466;box-shadow:0 0 9px #EF444433}
.b-surv{background:rgba(255,234,0,0.10);color:#FFB23F;border:1px solid #FFB23F55;box-shadow:0 0 9px #FFB23F33}
.up{color:#FF8C32;text-shadow:0 0 7px #FF8C3255}.dn{color:#EF4444;text-shadow:0 0 7px #EF444455}
.ok{color:#FF8C32}.no{color:#EF4444}
.sc,.kpi .v{text-shadow:0 0 9px currentColor}
.tab.active{border-color:#FF8C32;color:#FF8C32;box-shadow:0 0 12px #FF8C3233}
.wl.sel{border-left-color:#FF8C32;box-shadow:inset 0 0 20px #FF8C3212}
.panel h4,h2{color:#FF8C32;text-shadow:0 0 7px #FF8C3244}
.dot{background:#FF8C32;box-shadow:0 0 9px #FF8C32}
.pep{color:#FFB23F;text-shadow:0 0 8px #FFB23F99;font-weight:700}
.spk{display:flex;align-items:center;justify-content:flex-end}svg.spark{overflow:visible}
.wl .r{display:flex;align-items:center;gap:8px}
.anbadge{font-size:10px;cursor:help}
/* ===== LISIBILITÉ : moins de néon, contraste max, chiffres NETS ===== */
body{color:#eaf0fa}
.muted{color:#8794ab}
.sc,.kpi .v,.up,.dn,.grade,.big,.bv,.pep{text-shadow:none}
.up{color:#FF8C32}.dn{color:#EF4444}
.b-achat,.b-evit,.b-surv{box-shadow:none}
.bub.on{box-shadow:0 0 6px #FF8C3222}
.brand{text-shadow:0 0 10px #FF8C3255}
.sc{font-weight:800}
.ibtn{background:rgba(255,140,50,.1);color:#FF8C32;border:1px solid #FF8C3255;border-radius:7px;padding:4px 11px;font-size:11px;font-weight:700;cursor:pointer;margin-left:8px}
.ibtn:hover{background:rgba(255,140,50,.2)}
</style><style id="m-fix">
html,body{overflow-x:hidden;max-width:100%}
@media(max-width:640px){
  .grid,.cols,.herorow,.hero,.panorama,.layout,.heat,.secgrid,.poster{grid-template-columns:1fr!important}
  [style*="grid-template-columns"]{grid-template-columns:1fr!important}
  .top3{grid-template-columns:1fr 1fr 1fr!important}
  .plan{grid-template-columns:1fr 1fr!important}
  td,th{padding-left:9px!important;padding-right:9px!important}
  table{font-size:11.5px!important}
  .wrap,.daily{padding-left:12px!important;padding-right:12px!important}
  .phead-top{padding:15px 16px!important}
  .htitle{font-size:25px!important}
  .hmeta{margin-left:0!important;text-align:left!important}
  .back{padding:5px 9px!important;font-size:11px!important}
}
</style>
<style id="nav-css">
#gnav{position:sticky;top:0;z-index:1000;background:rgba(10,10,12,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid #1c1c24}
.gnav-in{max-width:1500px;margin:0 auto;display:flex;align-items:center;gap:12px;padding:8px 16px;flex-wrap:wrap}
.gnav-links{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
#gnav a{color:#aab4c4;text-decoration:none;font-size:13px;font-weight:700;padding:7px 12px;border-radius:9px;white-space:nowrap}
#gnav a:hover{background:#ffffff0d;color:#fff}
#gnav a.act{background:rgba(245,180,91,.14);color:#F5B45B}
.gnav-search{display:flex;gap:6px;align-items:center}
.gnav-search input{background:#0e0e0e;border:1px solid #1c1c24;color:#e8edf5;border-radius:9px;padding:7px 11px;font-size:13px;width:210px;max-width:46vw}
.gnav-search button{background:rgba(245,180,91,.12);border:1px solid #F5B45B55;color:#F5B45B;border-radius:9px;padding:7px 13px;font-weight:800;cursor:pointer}
.back{display:none!important}
@media(max-width:640px){.gnav-in{padding:6px 10px;gap:8px}.gnav-links{order:2;overflow-x:auto;flex-wrap:nowrap}#gnav a{font-size:12px;padding:6px 9px}.gnav-search{order:1;width:100%}.gnav-search input{flex:1;max-width:none;width:auto}}
/* === systeme de design premium (toutes pages) === */
body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;background:radial-gradient(1200px 780px at 50% -8%,#0d0d12,#050506 60%)!important}
.panel,.scard,.card,.pcard,.seccard,.tile,.kpi,.fav,.pchip,.stat{background:linear-gradient(165deg,#16171c,#0d0e12)!important;border:1px solid rgba(255,255,255,.08)!important;border-radius:16px!important}
.panel,.scard,.card,.pcard,.fav,.seccard{transition:border-color .2s ease,box-shadow .2s ease,transform .18s ease!important}
.panel:hover,.scard:hover,.card:hover,.fav:hover,.seccard:hover{border-color:rgba(255,140,50,.32)!important;box-shadow:0 0 26px rgba(255,140,50,.10)!important}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#26262e;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:#3a3a44}
::selection{background:rgba(255,140,50,.28);color:#fff}
/* bandeau KPI (cockpit + autres) */
.kpiband{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin:14px 0}
.kc{background:linear-gradient(165deg,#16171c,#0d0e12);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:11px 12px;min-width:0}
.kc .kt{font-size:8.5px;letter-spacing:.6px;text-transform:uppercase;color:#8794ab;display:flex;align-items:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kv{font-size:17px;font-weight:900;margin-top:3px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .ks{font-size:9px;color:#6b7689;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kbar{height:5px;border-radius:4px;background:#0a0a0a;overflow:hidden;margin-top:6px}
.kc .kbar>div{height:100%;border-radius:4px}
@media(max-width:760px){.kpiband{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:44%;overflow-x:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;padding-bottom:2px}.kc{scroll-snap-align:start}}
/* navigation mobile sticky en bas */
#mnav{display:none}
@media(max-width:760px){
  #mnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:1200;background:rgba(8,8,10,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid rgba(255,255,255,.09);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
  #mnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;text-decoration:none;color:#8794ab;font-size:9.5px;font-weight:700;padding:5px 2px;border-radius:12px}
  #mnav a .mi{font-size:19px;line-height:1}
  #mnav a.act{color:#FF8C32;background:rgba(255,140,50,.13)}
  body{padding-bottom:68px!important}
  #gnav .gnav-links{display:none}
}
</style>
<script>
(function(){var L=[['/','🔱 Cockpit'],['/ma-page','⭐ Ma Page'],['/watchlist','📋 Watchlist'],['/entreprises','🏢 Entreprises'],['/options','💎 Options']];
function build(){if(document.getElementById('gnav'))return;var p=location.pathname;
var links=L.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'>'+x[1]+'</a>';}).join('');
var nav=document.createElement('nav');nav.id='gnav';
nav.innerHTML='<div class="gnav-in"><div class="gnav-links">'+links+'</div><form class="gnav-search" onsubmit="return gnavGo(event)"><input id="gnavq" placeholder="Rechercher un titre… (ex: NVDA)" autocomplete="off"><button type="submit">→</button></form></div>';
document.body.insertBefore(nav,document.body.firstChild);
var M=[['/','🏠','Cockpit'],['/watchlist','📡','Scan'],['/ma-page','⭐','Ma Page'],['/options','💎','Options'],['/entreprises','🏢','Titres']];
var mn=document.createElement('nav');mn.id='mnav';
mn.innerHTML=M.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'><span class="mi">'+x[1]+'</span>'+x[2]+'</a>';}).join('');
document.body.appendChild(mn);}
window.gnavGo=function(e){e.preventDefault();var v=(document.getElementById('gnavq').value||'').trim().toUpperCase();if(v)location.href='/titre/'+encodeURIComponent(v);return false;};
if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();})();
</script></head><body>
<div class="top"><span class="brand">◣ TRACK TERMINAL</span>
<a href="/daily" style="margin-left:16px;color:#F5B45B;text-decoration:none;font-weight:700;font-size:12px;letter-spacing:1px;border:1px solid #F5B45B55;padding:5px 12px;border-radius:8px">📅 DAILY</a>
<a href="/sectors" style="margin-left:8px;color:#FFB23F;text-decoration:none;font-weight:700;font-size:12px;letter-spacing:1px;border:1px solid #FFB23F55;padding:5px 12px;border-radius:8px">🔥 SECTEURS</a>
<div class="bubbles" id="bubbles"></div></div>
<div class="strip"><span id="mkt" class="muted">connexion…</span><span class="muted"> · <span id="clock"></span> · scan maj <span id="updated">—</span> · analyse only</span></div>
<div class="layout">
  <div class="side"><h3>WATCHLIST · classée par score</h3><div id="wl"></div>
    <div id="scanerr" class="muted" style="color:#EF4444;padding:10px 14px"></div></div>
  <div class="bd">
    <div class="tabs"><button class="tab active" id="tSym" onclick="showTab('sym')">📈 ANALYSE TITRE</button><button class="tab" id="tPf" onclick="showTab('pf')">💼 PORTEFEUILLE (paper)</button><button class="tab" id="tOpt" onclick="showTab('opt')">💎 OPTIONS</button></div>
    <div id="detail"><p class="muted" style="padding:40px">← choisis un titre dans la watchlist</p></div>
    <div id="pf" style="display:none"><div id="ibkrpanel"></div><div id="pfbody"></div></div>
    <div id="opt" style="display:none"></div>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script src="https://s3.tradingview.com/tv.js"></script>
<script>
let SEL=null, DETAIL={}, chart=null, PF=null, pfChart=null, OPTB=null, ANOMSET=new Set();
let __tvSym=null;
function makeTV(sym){
  if(typeof TradingView==='undefined'||!sym)return;
  if(__tvSym===sym)return; __tvSym=sym;
  const el=document.getElementById('tvchart'); if(!el)return; el.innerHTML='';
  try{new TradingView.widget({container_id:'tvchart',symbol:sym,interval:'D',timezone:'Europe/Zurich',
    theme:'dark',style:'1',locale:'fr',autosize:true,hide_top_toolbar:false,hide_side_toolbar:true,
    allow_symbol_change:true,studies:['STD;EMA'],backgroundColor:'#111111',gridColor:'#1a1a22'});}catch(e){}
}
function bcls(v){return v==='BUY'?'b-achat':(v==='WATCH'||v==='WAIT')?'b-surv':'b-evit'}
function vfr(v){return {BUY:'ACHAT',WATCH:'SURVEILLER',WAIT:'ATTENTE',AVOID:'ÉVITER'}[v]||v}
function clr(s){return s>=72?'#FF8C32':s>=55?'#FFB23F':'#EF4444'}
function fm(n){if(n==null)return '—';const a=Math.abs(n);return (n<0?'-':'')+(a>=1e9?(a/1e9).toFixed(2)+'B':a>=1e6?(a/1e6).toFixed(1)+'M':a>=1e3?(a/1e3).toFixed(0)+'k':a.toFixed(0))}
function bub(l,on,s){return '<span class="bub '+(on?'on':'off')+'"><span class="bdot"></span>'+l+(s?' '+s:'')+'</span>'}
function tick(){document.getElementById('clock').textContent=new Date().toLocaleTimeString('fr-FR')}
setInterval(tick,1000);tick();
function row(k,label,ok,val){return `<div class="sg"><span class="${ok?'ok':'no'}">${ok?'✓':'✗'} ${label}</span><span class="muted">${val||''}</span></div>`}
function render(){
  const d=DETAIL[SEL]; if(!d){return}
  const s=d.signals, p=d.plan;
  document.getElementById('detail').innerHTML=`
   <div class="hdr"><div><div class="nm">${SEL}</div><div class="tk"><span id="cname"></span> · ${d.sigcount}/7 signaux</div></div>
     <div class="px">$${d.price} <span class="${d.change>=0?'up':'dn'}" style="font-size:15px">${d.change>=0?'+':''}${d.change}%</span></div></div>
   <div id="cdecision"></div>
   <div class="kpis">
     <div class="kpi"><div class="l">SCORE GLOBAL</div><div class="v" style="color:${clr(d.score)}">${d.score} · ${d.grade} <span class="badge ${bcls(d.verdict)}">${vfr(d.verdict)}</span></div></div>
     <div class="kpi"><div class="l">RÉGIME</div><div class="v" style="color:${d.regime==='TREND'?'#FF8C32':d.regime==='CHOP'?'#EF4444':'#FFB23F'}">${d.regime==='TREND'?'TENDANCE':d.regime==='CHOP'?'RANGE AGITÉ':'NEUTRE'} <span class="muted" style="font-size:11px">ADX ${d.adx}</span></div></div>
     <div class="kpi"><div class="l">QUALITÉ SETUP</div><div class="v" style="color:${clr(d.setup_quality||0)}">${d.setup_quality!=null?d.setup_quality:'—'}/100</div></div>
     <div class="kpi"><div class="l">CONFIANCE</div><div class="v" style="color:${clr(d.confidence||0)}">${d.confidence!=null?d.confidence:'—'}${d.rsi_div?` <span class="dn" style="font-size:10px">⚠ div. RSI</span>`:''}</div></div>
     <div class="kpi"><div class="l">TENDANCE</div><div class="v">${d.trend}</div></div>
     <div class="kpi"><div class="l">MOMENTUM · RSI</div><div class="v">${d.mom} · ${d.rsi}</div></div>
     <div class="kpi"><div class="l">FORCE REL.</div><div class="v">${d.rs}</div></div>
     <div class="kpi"><div class="l">POSITION 52s</div><div class="v">${d.pos52}%</div></div>
     <div class="kpi"><div class="l">IV-RANK (proxy)</div><div class="v" id="ivv">…</div></div>
     <div class="kpi"><div class="l">EARNINGS</div><div class="v" id="earn">…</div></div>
     <div class="kpi"><div class="l">SECTEUR</div><div class="v" id="csector" style="font-size:13px">…</div></div>
     <div class="kpi"><div class="l">MARKET CAP</div><div class="v" id="cmcap">…</div></div>
     <div class="kpi"><div class="l">P/E</div><div class="v" id="cpe">…</div></div>
   </div>
   <div class="cols">
     <div class="col"><h4>TENDANCE</h4>
       ${row('a20','Au-dessus EMA20',s.above20,'$'+d.ma20)}
       ${row('a50','Au-dessus MM50',s.above50,'$'+d.ma50)}
       ${row('a200','Au-dessus MM200',s.above200,'$'+d.ma200)}
       ${row('st','Moyennes empilées',s.stacked,'20>50>200')}</div>
     <div class="col"><h4>MOMENTUM</h4>
       ${row('gc','Golden cross 50/200',s.golden,s.goldenNow?'récent':'')}
       ${row('mc','Croisement 20/50',s.momCross,'')}
       ${row('rb','RSI haussier ≥50',s.rsiBull,'RSI '+d.rsi)}
       ${row('ro','ROC 1 mois',d.roc>=0,d.roc+'%')}</div>
     <div class="col"><h4>VOLUME</h4>
       ${row('vu','Volume > moyenne',s.volUp,d.volx+'× moy')}</div>
   </div>
   <h4 style="font-size:11px;color:#FF8C32;letter-spacing:1px">PLAN DE TRADE (ATR ${p.atr})</h4>
   <div class="plan">
     <div class="pcell"><div class="l">ENTRÉE</div><div class="v">$${p.entry}</div></div>
     <div class="pcell"><div class="l">STOP</div><div class="v dn">$${p.stop}</div></div>
     <div class="pcell"><div class="l">TP1 ·1R</div><div class="v up">$${p.tp1}</div></div>
     <div class="pcell"><div class="l">TP2 ·2R</div><div class="v up">$${p.tp2}</div></div>
     <div class="pcell"><div class="l">TP3 ·3R</div><div class="v up">$${p.tp3}</div></div>
   </div>
   <div class="panel"><h4>PRIX & TENDANCE (120j · Track)</h4><div style="height:240px"><canvas id="pc"></canvas></div></div>
   <div class="panel"><h4>🏦 FONDAMENTAUX · valorisation vs secteur</h4><div id="cfund"><span class="muted">…</span></div></div>
   <div class="panel"><h4>🔬 ANALYSE GRAPHIQUE</h4><div id="cchart"><span class="muted">…</span></div></div>
   <div class="panel"><h4>📊 GRAPHIQUE TRADINGVIEW · LIVE</h4><div id="tvchart" style="height:440px"></div></div>
   <div class="panel"><h4>💎 MEILLEURES OPTIONS · ${SEL} (calls court / moyen / long)</h4><div id="cbest"></div><div style="overflow:auto" id="csyopt"><span class="muted">chargement chaîne…</span></div></div>
   <div class="panel"><h4>🧮 GREEKS · sensibilités (call recommandé)</h4><div id="cgreeks"><span class="muted">…</span></div></div>
   <div class="panel"><h4>📉 GRAPHIQUE D'OPTION · profil de gain à l'échéance (reco)</h4><div style="height:220px"><canvas id="payoff"></canvas></div><div id="payoffInfo" class="muted" style="font-size:11px;margin-top:8px"></div></div>
   <div class="panel"><h4>📰 NEWS · pourquoi ça bouge</h4><div id="cnews"><span class="muted">chargement…</span></div></div>
   <div class="panel"><h4>🧱 GEX · POSITIONNEMENT DEALERS</h4><div class="gx" id="gx"><span class="muted">chargement options…</span></div></div>`;
  // chart
  const se=d.series;
  if(chart)chart.destroy();
  const pcEl=document.getElementById('pc'),pcg=pcEl.getContext('2d').createLinearGradient(0,0,0,240);
  pcg.addColorStop(0,'rgba(255,140,50,.30)');pcg.addColorStop(1,'rgba(255,140,50,0)');
  chart=new Chart(pcEl,{type:'line',
    data:{labels:se.dates,datasets:[
      {data:se.close,borderColor:'#FF8C32',backgroundColor:pcg,fill:true,borderWidth:2,pointRadius:0,tension:.18},
      {data:se.ema20,borderColor:'#F5B45B',borderWidth:1.2,pointRadius:0,tension:.1,borderDash:[4,3]},
      {data:se.sma50,borderColor:'#6b7689',borderWidth:1.1,pointRadius:0,tension:.1}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},
      scales:{x:{ticks:{color:'#6b6b6b',maxTicksLimit:8},grid:{color:'#161616'}},
              y:{ticks:{color:'#6b6b6b'},grid:{color:'#161616'}}}}});
  loadOptions(SEL);
  __tvSym=null; makeTV(SEL);
}
async function loadOptions(sym){
  try{
    const o=await(await fetch('/options/'+sym)).json();
    if(SEL!==sym)return;
    document.getElementById('ivv').textContent=o.ivrank!=null?o.ivrank+'%':'—';
    document.getElementById('earn').textContent=o.earnings||'—';
    document.getElementById('cname').textContent=o.name||sym;
    document.getElementById('csector').textContent=o.sector||'—';
    document.getElementById('cmcap').textContent=o.mcap?('$'+(o.mcap/1e9).toFixed(o.mcap>=1e11?0:1)+'B'):'—';
    document.getElementById('cpe').textContent=o.pe?o.pe.toFixed(1):'—';
    const pos=o.regime==='POSITIF';
    document.getElementById('gx').innerHTML=o.net_gex==null?'<span class="muted">pas de données options</span>':[
      ['RÉGIME',(pos?'🟢 +γ':'🔴 −γ'),pos?'#FF8C32':'#EF4444'],
      ['NET GEX',fm(o.net_gex),pos?'#FF8C32':'#EF4444'],
      ['CALL WALL','$'+o.call_wall,'#FFB23F'],
      ['PUT WALL','$'+o.put_wall,'#F5B45B'],
      ['GAMMA FLIP','$'+o.gamma_flip,'#FFD166']
    ].map(k=>`<div class="kpi" style="margin:0"><div class="l">${k[0]}</div><div class="v" style="color:${k[2]}">${k[1]}</div></div>`).join('');
    const dec=o.decision, dc=dec?(dec.tone==='strong'?'#22C55E':dec.tone==='buy'?'#4ade80':dec.tone==='watch'?'#FF8C32':dec.tone==='wait'?'#FFB23F':'#EF4444'):'#888';
    document.getElementById('cdecision').innerHTML=dec?`<div style="background:linear-gradient(135deg,${dc}14,#0d0d0d);border:1px solid ${dc}55;border-radius:14px;padding:16px 18px;margin-bottom:14px">
      <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
        <div style="font-size:23px;font-weight:800;color:${dc};text-shadow:0 0 14px ${dc}55">${dec.decision}</div>
        <div style="flex:1;min-width:130px"><div class="muted" style="font-size:10px;letter-spacing:1px">CONVICTION</div><div style="height:8px;background:#1a1a1a;border-radius:5px;margin-top:5px;overflow:hidden"><div style="height:100%;width:${dec.conviction}%;background:${dc};box-shadow:0 0 8px ${dc}"></div></div></div>
        <div style="font-size:21px;font-weight:800;color:${dc}">${dec.conviction}<span style="font-size:12px;color:#888">/100</span></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:14px">
        <div><div style="font-size:10px;letter-spacing:1px;color:#22C55E;font-weight:700;margin-bottom:6px">✓ FORCES</div>${(dec.pros||[]).map(p=>`<div style="font-size:12px;margin:4px 0;line-height:1.4"><span class="up">✓</span> ${p}</div>`).join('')||'<div class="muted" style="font-size:12px">—</div>'}</div>
        <div><div style="font-size:10px;letter-spacing:1px;color:#EF4444;font-weight:700;margin-bottom:6px">⚠ RISQUES</div>${(dec.cons||[]).map(c=>`<div style="font-size:12px;margin:4px 0;line-height:1.4"><span class="dn">✗</span> ${c}</div>`).join('')||'<div class="muted" style="font-size:12px">aucun risque majeur</div>'}</div>
      </div>
      <div style="margin-top:13px;padding-top:12px;border-top:1px solid #ffffff10;font-size:13px"><b style="color:${dc}">→ Action :</b> ${dec.action}</div>
    </div>`:'';
    const bp=o.best_pick;
    document.getElementById('cbest').innerHTML=bp?`<div style="background:rgba(255,140,50,.08);border:1px solid #F5B45B55;border-radius:10px;padding:12px 14px;margin-bottom:12px">
      <div style="font-size:10px;letter-spacing:1px;color:#F5B45B;font-weight:800;margin-bottom:5px">🎯 RECOMMANDATION · la meilleure des 3 échéances</div>
      <div style="font-size:16px;font-weight:800">${(bp.bucket||'').toUpperCase()} · ${eud(bp.exp)} · strike $${bp.strike} <span style="color:${clr(bp.suit)}">${bp.grade}</span></div>
      <div style="display:flex;gap:14px;margin-top:8px;flex-wrap:wrap;font-size:12px">
        <span>💰 coût <b>$${(bp.cost||0).toLocaleString('fr-FR')}</b></span>
        <span>🎯 proba profit <b style="color:${(bp.pop||0)>=50?'#FF8C32':(bp.pop||0)>=38?'#FFB23F':'#EF4444'}">${bp.pop}%</b></span>
        <span>⚠️ danger <b style="color:${dcol(bp.danger)}">${bp.danger}</b></span>
        <span>📈 si $${bp.tgt} → <b class="${bp.pot>=0?'up':'dn'}">${bp.pot>=0?'+':''}${bp.pot}%</b> <span class="muted">(${bp.p_tgt}%)</span></span></div>
      <div class="muted" style="margin-top:7px;font-size:11px">${bp.why||''}</div></div>`:'';
    document.getElementById('cchart').innerHTML=o.chart_read?`<div style="font-weight:700;margin-bottom:7px">${o.chart_verdict||''}</div><div class="muted" style="font-size:12.5px;line-height:1.6">${o.chart_read}</div>`:'<span class="muted">analyse en cours…</span>';
    const fnd=o.fund||{},vl=o.valuation,pct=v=>v!=null?(v*100).toFixed(1)+'%':'—';
    const fcell=(lab,val,extra)=>`<div class="kpi" style="margin:0"><div class="l">${lab}</div><div class="v">${val}</div>${extra?`<div class="muted" style="font-size:10px">${extra}</div>`:''}</div>`;
    document.getElementById('cfund').innerHTML='<div class="gx">'
      +fcell('P/E',o.pe?o.pe.toFixed(1):'—',o.sector_median_pe?'médiane secteur '+o.sector_median_pe:'')
      +fcell('VALORISATION',vl?`<span class="${vl.tone==='warn'?'dn':vl.tone==='good'?'up':''}">${vl.label}</span>`:'—',vl?'×'+vl.ratio+' vs secteur':'')
      +fcell('P/E FORWARD',fnd.fwd_pe?fnd.fwd_pe.toFixed(1):'—')
      +fcell('MARGE NETTE',pct(fnd.margin),o.sector_median_margin!=null?'secteur '+o.sector_median_margin+'%':'')
      +fcell('CROISSANCE CA',pct(fnd.growth),o.sector_median_growth!=null?'secteur '+o.sector_median_growth+'%':'')
      +fcell('BETA',(fnd.beta||o.beta)?(fnd.beta||o.beta).toFixed(2):'—')
      +'</div><div class="muted" style="font-size:10px;margin-top:8px">Fondamentaux yfinance (peuvent dater). P/E comparé à la médiane des leaders du même secteur.</div>';
    const _bp=o.best_pick;
    document.getElementById('cgreeks').innerHTML=_bp?'<div class="greeks">'
      +`<div class="greek"><div class="gk">Δ Delta</div><div class="gv">${_bp.delta}</div></div>`
      +`<div class="greek"><div class="gk">Γ Gamma</div><div class="gv">${_bp.gamma}</div></div>`
      +`<div class="greek"><div class="gk">Θ Theta/j</div><div class="gv dn">${_bp.theta}</div></div>`
      +`<div class="greek"><div class="gk">V Vega</div><div class="gv">${_bp.vega}</div></div>`
      +`<div class="greek"><div class="gk">IV</div><div class="gv">${_bp.iv}%</div></div>`
      +`<div class="greek"><div class="gk">Θ / prime</div><div class="gv ${_bp.theta_burn>=1.5?'dn':''}">${_bp.theta_burn}%/j</div></div>`
      +`<div class="greek"><div class="gk">Breakeven</div><div class="gv">$${_bp.be}</div></div>`
      +`<div class="greek"><div class="gk">Échéance</div><div class="gv">${_bp.dte}j</div></div>`
      +'</div><div class="muted" style="font-size:10px;margin-top:8px">Δ sensibilité au prix · Γ variation du delta · Θ perte de temps par jour · V sensibilité à la volatilité implicite.</div>'
      :'<span class="muted">aucune option recommandée pour ce titre</span>';
    renderPayoff(o);
    document.getElementById('csyopt').innerHTML=(o.contracts&&o.contracts.length)?
      ('<table><thead><tr><th class="l">Horizon</th><th>Échéance</th><th>Strike</th><th>Grade</th><th>Δ</th><th>Proba</th><th>Danger</th><th>Coût</th><th>Si cible→gain</th><th>Θ/j</th><th>Flags</th></tr></thead><tbody>'
       +o.contracts.map(c=>`<tr><td class="l"><span style="color:${c.bucket==='court'?'#FFB23F':c.bucket==='moyen'?'#FFB23F':'#FF8C32'};font-weight:800;font-size:10px">${(c.bucket||'long').toUpperCase()}</span></td><td>${eud(c.exp)} <span class="muted">${c.dte}j</span></td><td>$${c.strike}</td><td><span style="color:${clr(c.suit)};font-weight:700">${c.grade}</span> <span class="muted">${c.suit}</span></td><td>${c.delta}</td><td style="font-weight:700;color:${(c.pop||0)>=50?'#FF8C32':(c.pop||0)>=38?'#FFB23F':'#EF4444'}">${c.pop}%</td><td style="font-weight:700;color:${dcol(c.danger)}">${c.danger}</td><td>$${c.cost.toLocaleString('fr-FR')}</td><td>si $${c.tgt} <span class="${c.pot>=0?'up':'dn'}">${c.pot>=0?'+':''}${c.pot}%</span></td><td class="${c.theta_burn>=1.5?'dn':'muted'}">${c.theta_burn}%</td><td class="muted" style="font-size:9px">${(c.flags||[]).join(' · ')}</td></tr>`).join('')
       +'</tbody></table>')
      :'<span class="muted">aucun contrat propre (filtré : spread ≤12% / OI ≥100 / liquidité)</span>';
    document.getElementById('cnews').innerHTML=(o.news_why?`<div style="color:#FFB23F;font-weight:600;margin-bottom:8px">⚡ ${o.news_why}</div>`:'')+((o.news&&o.news.length)?
      o.news.map(n=>`<div style="display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid #0a0a0a;font-size:13px"><span>${n.link?`<a href="${n.link}" target="_blank" style="color:#cfe;text-decoration:none">${n.fr||n.title}</a>`:(n.fr||n.title)}</span><span class="muted" style="white-space:nowrap">${n.pub}${n.time?' · '+n.time:''}</span></div>`).join('')
      :'<span class="muted">pas de news récente (yfinance)</span>');
  }catch(e){}
}
async function refresh(){
  try{
    const d=await(await fetch('/scan')).json();
    DETAIL=d.detail||{};PF=d.portfolio||null;OPTB=d.options_board||null;
    ANOMSET=new Set((d.anomalies||[]).filter(a=>a.sev>=70).map(a=>a.symbol));
    if(document.getElementById('pf').style.display!=='none')renderPortfolio();
    if(document.getElementById('opt').style.display!=='none')renderOptions();
    document.getElementById('updated').textContent=d.updated||'—';
    const m=d.market||{};
    document.getElementById('bubbles').innerHTML=bub('yfinance',!d.error)+bub('marché '+(m.open?'OUVERT':'fermé'),!!m.open,m.et||'')+bub('scan live',true)+bub('IA news FR',!!d.ai_on,d.ai_on?'active':'clé .env requise')+bub('backtest',!!d.portfolio);
    document.getElementById('mkt').innerHTML=d.spy?('SPY $'+d.spy.price+' <span class="'+(d.spy.change>=0?'up':'dn')+'">'+(d.spy.change>=0?'+':'')+d.spy.change+'%</span> · <b>'+(d.breadth||0)+'%</b> des leaders en ACHAT'):'connexion…';
    document.getElementById('scanerr').textContent=d.error?('⚠ '+d.error):'';
    document.getElementById('wl').innerHTML=(d.rows||[]).map(r=>`
      <div class="wl ${r.symbol===SEL?'sel':''}" onclick="select('${r.symbol}')">
        <div><div class="s">${((r.grade==='S+'||r.grade==='S')&&r.verdict==='BUY')?'<span class="pep">🔥</span> ':''}${r.symbol}${ANOMSET.has(r.symbol)?' <span class="anbadge" title="anomalie détectée">⚡</span>':''}</div><div class="muted">$${r.price} <span class="${r.change>=0?'up':'dn'}">${r.change>=0?'+':''}${r.change}%</span></div></div>
        <div class="r"><div class="spk">${SPK(r.symbol)}</div><span class="sc" style="color:${clr(r.score)}">${r.score}</span><span class="badge ${bcls(r.verdict)}">${r.grade||r.verdict[0]}</span></div>
      </div>`).join('');
    if(!SEL && d.rows && d.rows.length){select(d.rows[0].symbol)}
    else if(SEL && DETAIL[SEL]){render()}
  }catch(e){document.getElementById('scanerr').textContent='⚠ '+e}
}
function select(sym){SEL=sym;render();document.querySelectorAll('.wl').forEach(w=>w.classList.toggle('sel',w.querySelector('.s').textContent===sym))}
window.select=select;
function showTab(t){
  document.getElementById('detail').style.display=t==='sym'?'block':'none';
  document.getElementById('pf').style.display=t==='pf'?'block':'none';
  document.getElementById('opt').style.display=t==='opt'?'block':'none';
  document.getElementById('tSym').classList.toggle('active',t==='sym');
  document.getElementById('tPf').classList.toggle('active',t==='pf');
  document.getElementById('tOpt').classList.toggle('active',t==='opt');
  if(t==='pf'){renderPortfolio();renderIbkr();}
  if(t==='opt')renderOptions();
}
window.showTab=showTab;
async function renderIbkr(force){
  const el=document.getElementById('ibkrpanel');if(!el)return;
  if(!el.dataset.loaded||force){
    el.innerHTML='<div class="panel"><h4>💼 COMPTE IBKR · <span style="color:#EF4444">LECTURE SEULE</span> <button class="ibtn" onclick="renderIbkr(true)">⟳ Connecter / Rafraîchir</button></h4><div id="ibkrbody" class="muted" style="padding:10px 0">connexion à TWS / IB Gateway…</div></div>';
    el.dataset.loaded='1';
  }
  try{
    const d=await(await fetch('/ibkr')).json();const b=document.getElementById('ibkrbody');if(!b)return;
    if(!d.connected){b.innerHTML='<div class="dn">⛔ '+(d.error||'non connecté')+'</div><div class="muted" style="margin-top:6px;font-size:11px">Lance TWS ou IB Gateway, active l\'API (Global Configuration › API › Settings). Puis clique ⟳. Connexion en LECTURE SEULE — aucun ordre ne sera jamais passé.</div>';return;}
    const pos=(d.positions||[]).map(p=>`<tr><td class="sym">${p.symbol}</td><td>${p.qty}</td><td>$${p.avg_cost}</td><td class="muted">${p.sectype}</td></tr>`).join('')||'<tr><td colspan="4" class="muted">aucune position</td></tr>';
    b.innerHTML=`<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px"><span class="badge b-achat">✓ ${d.mode}</span>
      <div class="kpi" style="margin:0"><div class="l">VALEUR NETTE</div><div class="v">${d.net_liq!=null?'$'+d.net_liq.toLocaleString('fr-FR'):'—'}</div></div>
      <div class="kpi" style="margin:0"><div class="l">CASH</div><div class="v">${d.cash!=null?'$'+d.cash.toLocaleString('fr-FR'):'—'}</div></div>
      <div class="kpi" style="margin:0"><div class="l">POUVOIR D'ACHAT</div><div class="v">${d.buying_power!=null?'$'+d.buying_power.toLocaleString('fr-FR'):'—'}</div></div>
      <div class="kpi" style="margin:0"><div class="l">P&L LATENT</div><div class="v ${(d.upnl||0)>=0?'up':'dn'}">${d.upnl!=null?'$'+d.upnl.toLocaleString('fr-FR'):'—'}</div></div></div>
      <table><thead><tr><th class="l">TITRE</th><th>QTÉ</th><th>PRIX MOYEN</th><th>TYPE</th></tr></thead><tbody>${pos}</tbody></table>`;
  }catch(e){const b=document.getElementById('ibkrbody');if(b)b.innerHTML='<span class="dn">erreur: '+e+'</span>';}
}
window.renderIbkr=renderIbkr;
function renderPortfolio(){
  const p=PF, el=document.getElementById('pfbody');
  if(!p){el.innerHTML='<p class="muted" style="padding:30px">backtest en cours…</p>';return}
  el.innerHTML=`
   <div class="kpis">
     <div class="kpi"><div class="l">BALANCE (paper)</div><div class="v">$${p.balance.toLocaleString('fr-FR')}</div></div>
     <div class="kpi"><div class="l">P&L TOTAL</div><div class="v ${p.total>=0?'up':'dn'}">${p.total>=0?'+':''}${p.total}%</div></div>
     <div class="kpi"><div class="l">SHARPE</div><div class="v">${p.sharpe}</div></div>
     <div class="kpi"><div class="l">MAX DD</div><div class="v dn">${p.maxdd}%</div></div>
     <div class="kpi"><div class="l">WIN RATE</div><div class="v">${p.winrate}% <span class="muted">${p.trades} tr.</span></div></div>
     <div class="kpi"><div class="l">ALL-TIME HIGH</div><div class="v up">+${p.ath}%</div></div>
   </div>
   <div class="panel"><h4>COURBE D'EQUITY · forward-test ${p.dates.length}j · PAPER</h4><div style="height:280px"><canvas id="eqc"></canvas></div></div>
   <div class="cols" style="grid-template-columns:1fr 2fr">
     <div class="panel"><h4>POSITIONS (top ${p.top_n})</h4>${p.holdings.length?p.holdings.map(h=>`<div class="sg"><span class="ok">● ${h}</span></div>`).join(''):'<span class="muted">cash</span>'}</div>
     <div class="panel"><h4>JOURNAL DE TRADES</h4><div style="max-height:240px;overflow:auto">${p.journal.map(j=>`<div class="sg"><span><span class="badge ${j.action==='BUY'?'b-achat':'b-evit'}">${j.action}</span> ${j.sym} <span class="muted">${j.date}</span></span><span>$${j.price} ${j.pnl!=null?`<span class="${j.pnl>=0?'up':'dn'}">${j.pnl>=0?'+':''}${j.pnl}%</span>`:''}</span></div>`).join('')}</div></div>
   </div>
   <p class="muted" style="margin-top:14px">PAPER / forward-test : top ${p.top_n} titres score≥58, rebalance quotidien, signal d'HIER (sans lookahead). Zéro argent réel, zéro ordre. NOT FINANCIAL ADVICE.</p>`;
  if(pfChart)pfChart.destroy();
  const eqEl=document.getElementById('eqc'),eqg=eqEl.getContext('2d').createLinearGradient(0,0,0,300);
  eqg.addColorStop(0,'rgba(255,140,50,.28)');eqg.addColorStop(1,'rgba(255,140,50,0)');
  pfChart=new Chart(eqEl,{type:'line',
    data:{labels:p.dates,datasets:[{data:p.equity,borderColor:'#FF8C32',backgroundColor:eqg,borderWidth:2,pointRadius:0,fill:true,tension:.15}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},
      scales:{x:{ticks:{color:'#5b6678',maxTicksLimit:8},grid:{color:'#171717'}},
              y:{ticks:{color:'#5b6678',callback:v=>'$'+(v/1000).toFixed(0)+'k'},grid:{color:'#171717'}}}}});
}
window.renderPortfolio=renderPortfolio;
function renderOptions(){
  const b=OPTB, el=document.getElementById('opt');
  if(!b||!b.length){el.innerHTML='<p class="muted" style="padding:30px">calcul des chaînes d\'options en cours (~1 min)… reviens dans un instant.</p>';return}
  const calls=b.filter(c=>c.type==='CALL').length, puts=b.filter(c=>c.type==='PUT').length;
  el.innerHTML=`
   <div class="panel"><h4>💎 MEILLEURES OPTIONS DU JOUR · classées par suitability · ${calls} calls (haussiers) · ${puts} puts (baissiers)</h4>
   <div style="overflow:auto"><table><thead><tr>
   <th class="l">Titre</th><th>Sens</th><th>Échéance</th><th>Strike</th><th>Suit.</th>
   <th>Δ</th><th>Θ/j</th><th>IV</th><th>Coût</th><th>Breakeven</th><th>OI</th><th>Spread</th><th>Pot. si TP</th>
   </tr></thead><tbody>${b.map(c=>`
     <tr>
       <td class="l sym">${c.sym}</td>
       <td><span class="badge ${c.type==='CALL'?'b-achat':'b-evit'}">${c.type==='CALL'?'CALL ▲':'PUT ▼'}</span></td>
       <td>${c.exp} <span class="muted">${c.dte}j</span></td>
       <td>$${c.strike}</td>
       <td><span style="font-weight:700;color:${clr(c.suit)}">${c.grade}</span> <span class="muted">${c.suit}</span></td>
       <td>${c.delta}</td><td class="dn">${c.theta}</td><td>${c.iv}%</td>
       <td>$${c.cost.toLocaleString('fr-FR')}</td><td>$${c.be}</td>
       <td>${c.oi.toLocaleString('fr-FR')}</td><td>${c.spread==null?'—':c.spread+'%'}</td>
       <td class="${c.pot>=0?'up':'dn'}">${c.pot>=0?'+':''}${c.pot}%</td>
     </tr>`).join('')}</tbody></table></div></div>
   <p class="muted" style="margin-top:12px">Suitability = liquidité + spread + delta adapté + échéance 6/9/12M + IV + technique (cahier §6). Filtré : spread ≤ ${'12'}%, OI ≥ 100, suit ≥ 45. « Pot. si TP » = gain de l'option si le target TP2 du titre est atteint. Greeks = Black-Scholes (yfinance différé). ANALYSE ONLY — aucun ordre.</p>`;
}
window.renderOptions=renderOptions;
let payoffChart=null;
function renderPayoff(o){
  const inf=document.getElementById('payoffInfo'),el=document.getElementById('payoff');if(!el||!inf)return;
  const bp=o.best_pick;
  if(!bp||typeof Chart==='undefined'){if(payoffChart){payoffChart.destroy();payoffChart=null;}inf.textContent=bp?'':'pas d\'option recommandée pour ce titre';return}
  const K=bp.strike,prem=bp.mid,spot=o.spot||K,lo=Math.min(spot,K)*0.78,hi=Math.max(spot,K)*1.28,xs=[],ys=[];
  for(let i=0;i<=44;i++){const S=lo+(hi-lo)*i/44;xs.push(Math.round(S));ys.push(Math.round((Math.max(S-K,0)-prem)*100));}
  if(payoffChart)payoffChart.destroy();
  payoffChart=new Chart(el,{type:'line',data:{labels:xs,datasets:[{data:ys,borderColor:'#FF8C32',borderWidth:2,pointRadius:0,fill:{value:0,above:'rgba(255,140,50,.13)',below:'rgba(207,107,122,.13)'},tension:.04}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>'P/L '+(c.parsed.y>=0?'+':'')+'$'+c.parsed.y.toLocaleString('fr-FR')+'  si '+o.sym+' $'+c.label}}},
      scales:{x:{ticks:{color:'#6b7689',maxTicksLimit:7},grid:{color:'#171717'}},y:{ticks:{color:'#6b7689',callback:v=>'$'+v},grid:{color:'#171717'}}}}});
  const gtgt=Math.round((Math.max(bp.tgt-K,0)-prem)*100);
  inf.innerHTML=`Strike <b>$${K}</b> · échéance ${eud(bp.exp)} · breakeven <b>$${bp.be}</b> · perte max <b class="dn">-$${bp.cost.toLocaleString('fr-FR')}</b> · si ${o.sym} atteint $${bp.tgt} → <b class="${gtgt>=0?'up':'dn'}">${gtgt>=0?'+':''}$${gtgt.toLocaleString('fr-FR')}</b>. Profil à l'échéance (analyse only).`;
}
window.renderPayoff=renderPayoff;
let __spkN=0;
function spark(arr,w=72,h=20,days=24){
  if(!arr||arr.length<2)return '';
  const d=arr.slice(-days).filter(v=>v!=null&&!isNaN(v));if(d.length<2)return '';
  const up=d[d.length-1]>=d[0],col=up?'#FF8C32':'#EF4444',gid='ws'+(++__spkN);
  const mn=Math.min(...d),mx=Math.max(...d),rg=(mx-mn)||1,pad=2,iw=w-pad*2,ih=h-pad*2;
  const X=i=>pad+(i/(d.length-1))*iw,Y=v=>pad+ih-((v-mn)/rg)*ih;
  const pts=d.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)),line='M'+pts.join(' L');
  const area=line+' L'+X(d.length-1).toFixed(1)+','+(h-pad)+' L'+X(0).toFixed(1)+','+(h-pad)+' Z';
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity="0.28"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs><path d="${area}" fill="url(#${gid})"/><path d="${line}" fill="none" stroke="${col}" stroke-width="1.3" stroke-linejoin="round"/></svg>`;
}
function SPK(s){const x=DETAIL[s];return (x&&x.series&&x.series.close)?spark(x.series.close):'';}
function eud(s){if(!s)return s;const m=String(s).slice(0,10).match(/^(\d{4})-(\d{2})-(\d{2})$/);return m?m[3]+'/'+m[2]+'/'+m[1]:s}
function dcol(d){return d==='Faible'?'#FF8C32':d==='Modéré'?'#FFB23F':d==='Élevé'?'#d98a52':'#EF4444'}
(function(){const q=new URLSearchParams(location.search).get('sym');if(q)SEL=q.toUpperCase();})();
setInterval(refresh,15000);refresh();
</script></body></html>"""


PAGE_DAILY = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Trading Desk"><meta name="mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0b0e14"><link rel="apple-touch-icon" href="/static/icon-180.png"><link rel="manifest" href="/manifest.webmanifest"><title>TRADING DESK · DAILY WATCHLIST</title>
<style>
*{box-sizing:border-box}html{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
body{margin:0;background:#070707;color:#cfd8e6;font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
a{color:inherit}
.muted{color:#5b6678}.up{color:#FF8C32;text-shadow:0 0 6px #FF8C3244}.dn{color:#EF4444;text-shadow:0 0 6px #EF444444}
.badge{font-size:10px;font-weight:800;padding:2px 7px;border-radius:6px;letter-spacing:.5px}
.b-achat{background:rgba(255,140,50,.10);color:#FF8C32;border:1px solid #FF8C3266}
.b-evit{background:rgba(255,43,78,.10);color:#EF4444;border:1px solid #EF444466}
.b-surv{background:rgba(255,234,0,.10);color:#FFB23F;border:1px solid #FFB23F55}
.sc{font-weight:800;text-shadow:0 0 9px currentColor}.pep{color:#FFB23F;text-shadow:0 0 8px #FFB23F99;font-weight:700}
.topbar{display:flex;align-items:center;gap:14px;padding:12px 24px;border-bottom:1px solid #FF8C321a;background:#0a0a0a}
.topbar a.back{color:#5b6678;text-decoration:none;font-size:12px;font-weight:700;letter-spacing:1px}
.topbar a.back:hover{color:#FF8C32}
.daily{max-width:1480px;margin:0 auto;padding:0 22px 60px}
.dhead{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:14px;padding:26px 4px 18px;border-bottom:1px solid #FF8C3226}
.dhead .ttl{font-size:34px;font-weight:800;letter-spacing:4px;line-height:1;color:#FF8C32;text-shadow:0 0 18px #FF8C3288,0 0 40px #FF8C3233}
.dhead .ttl .v{color:#F5B45B;text-shadow:0 0 18px #F5B45B88}
.dhead .sub{margin-top:8px;font-size:12px;letter-spacing:2px;color:#5b6678}
.dhead .meta{display:flex;gap:10px;flex-wrap:wrap;align-items:center;justify-content:flex-end}
.pill{display:inline-flex;align-items:center;gap:7px;font-size:11px;font-weight:700;letter-spacing:.6px;padding:6px 13px;border-radius:20px;border:1px solid #1a1a22;background:#111111;color:#7b8696}
.pill .pdot{width:8px;height:8px;border-radius:50%;background:#5b6678}
.pill.live{border-color:#FF8C3255;color:#FF8C32;box-shadow:0 0 12px #FF8C322e}
.pill.live .pdot{background:#FF8C32;box-shadow:0 0 8px #FF8C32;animation:p 1.6s infinite}
.pill.shut{border-color:#5a4a1a;color:#FFB23F}.pill.shut .pdot{background:#FFB23F;box-shadow:0 0 8px #FFB23Faa}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.breadth{display:flex;align-items:center;gap:16px;margin:16px 0 6px;background:#111111;border:1px solid #1a1a22;border-radius:12px;padding:12px 18px;flex-wrap:wrap}
.breadth .bl{font-size:11px;letter-spacing:1px;color:#5b6678;white-space:nowrap}
.breadth .bar{flex:1;min-width:160px;height:9px;border-radius:6px;background:#0a0a0a;overflow:hidden}
.breadth .fill{height:100%;border-radius:6px;background:linear-gradient(90deg,#EF4444 0%,#FFB23F 50%,#FF8C32 100%);box-shadow:0 0 14px #FF8C3255}
.breadth .bv{font-size:20px;font-weight:800;color:#FF8C32;text-shadow:0 0 10px #FF8C3266;min-width:54px;text-align:right}
.breadth .seg{display:flex;gap:14px;font-size:11px;color:#7b8696;letter-spacing:.4px;white-space:nowrap}
.breadth .seg b{font-weight:800}.breadth .gb{color:#FF8C32}.breadth .gy{color:#FFB23F}.breadth .gr{color:#EF4444}
.herorow{display:grid;grid-template-columns:2fr 1fr;gap:18px;margin-top:18px}
.hero{display:grid;grid-template-columns:2fr 1fr;background:#111111;border:1px solid #F5B45B33;border-radius:14px;overflow:hidden;box-shadow:0 0 22px #F5B45B14}
.heroL{padding:18px 22px}.heroR{padding:18px 22px;border-left:1px solid #1a1a22;background:rgba(255,178,63,.03)}
.hsym{font-size:24px;font-weight:800;letter-spacing:1px;color:#e6edf7}
.hplan{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;margin-top:6px}.hplan b{font-size:15px}
.chip{display:inline-flex;gap:6px;align-items:center;padding:6px 11px;border-radius:18px;font-size:12px;margin:3px;cursor:pointer;border:1px solid #1a1a22}
.chip.up{color:#FF8C32;border-color:#FF8C3255;background:rgba(255,140,50,.06)}
.chip.down{color:#EF4444;border-color:#EF444455;background:rgba(255,43,92,.06)}
.poster{display:grid;gap:18px;margin-top:18px;grid-template-columns:repeat(auto-fill,minmax(440px,1fr))}
.poster .span2{grid-column:span 2}
@media(max-width:980px){.poster{grid-template-columns:1fr}.poster .span2{grid-column:span 1}.herorow{grid-template-columns:1fr}.hero{grid-template-columns:1fr}}
.scard{background:#111111;border:1px solid #1a1a22;border-radius:14px;overflow:hidden;display:flex;flex-direction:column}
.scard .shead{display:flex;align-items:center;gap:10px;padding:13px 16px;font-size:13px;font-weight:800;letter-spacing:1.2px;border-bottom:1px solid #1a1a22;position:relative}
.scard .shead .ico{font-size:17px}
.scard .shead .cnt{margin-left:auto;font-size:10px;font-weight:700;color:#5b6678;letter-spacing:.5px}
.scard .shead::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:currentColor;box-shadow:0 0 12px currentColor}
.s-green .shead{color:#34D399;background:rgba(52,211,153,.06)}
.s-violet .shead{color:#A78BFA;background:rgba(167,139,250,.06)}
.s-yellow .shead{color:#F5B45B;background:rgba(245,180,91,.06)}
.s-red .shead{color:#F87171;background:rgba(248,113,113,.06)}
.s-cyan .shead{color:#38BDF8;background:rgba(56,189,248,.06)}
.s-gold .shead{color:#FF8C32;background:rgba(255,140,50,.06)}
.scard table{width:100%;border-collapse:collapse;font-size:12.5px}
.scard thead th{font-size:10px;letter-spacing:.7px;color:#5b6678;text-align:right;font-weight:700;padding:9px 14px 7px;border-bottom:1px solid #0a0a0a}
.scard thead th:first-child{text-align:left}
.scard tbody td{padding:9px 14px;text-align:right;border-bottom:1px solid #101016}
.scard tbody td:first-child{text-align:left}
.scard tbody tr:last-child td{border-bottom:none}
.scard tbody tr:hover{background:#171717;cursor:pointer}
.sym{font-weight:800;letter-spacing:.5px;color:#e6edf7}.sub{font-size:10.5px;color:#5b6678}
.grade{display:inline-block;min-width:24px;text-align:center;font-weight:800;font-size:11px;padding:2px 6px;border-radius:6px}
.g-sp{background:rgba(245,180,91,.12);color:#F5B45B;border:1px solid #F5B45B66;box-shadow:0 0 9px #F5B45B33}
.g-a{background:rgba(255,140,50,.10);color:#FF8C32;border:1px solid #FF8C3266}
.g-b{background:rgba(255,234,0,.10);color:#FFB23F;border:1px solid #FFB23F55}
.g-c{background:rgba(255,43,92,.10);color:#EF4444;border:1px solid #EF444455}
.rvol{font-weight:700}.rvol.hot{color:#FFB23F;text-shadow:0 0 7px #FFB23F66}.rvol.warm{color:#FF8C32}.rvol.cold{color:#5b6678}
.rb{font-weight:700;font-size:10px;padding:2px 6px;border-radius:5px}
.rb-low{color:#FF8C32;background:rgba(255,140,50,.1)}.rb-med{color:#FFB23F;background:rgba(255,234,0,.1)}.rb-high{color:#EF4444;background:rgba(255,43,92,.1)}
.dfoot{margin-top:34px;text-align:center;padding:22px;border-top:1px solid #FF8C321a}
.dfoot .slogan{font-size:18px;font-weight:800;letter-spacing:6px;background:linear-gradient(90deg,#FF8C32,#F5B45B);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.dfoot .dis{margin-top:8px;font-size:10.5px;letter-spacing:1px;color:#5b6678}
.spk{display:flex;align-items:center}svg.spark{overflow:visible}
.stitle{font-size:13px;font-weight:800;letter-spacing:2px;color:#F5B45B;text-shadow:0 0 8px #F5B45B55;margin:26px 4px 12px;display:flex;align-items:center;gap:8px}
.panorama{display:grid;grid-template-columns:300px 1fr;gap:18px;margin-top:18px}
.pcard{background:#111111;border:1px solid #1a1a22;border-radius:14px;padding:16px}
@media(max-width:980px){.panorama{grid-template-columns:1fr}}
.secgrid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(290px,1fr))}
.seccard{background:#111111;border:1px solid #1a1a22;border-radius:12px;padding:12px 14px;cursor:pointer}
.seccard:hover{background:#171717}
.seccard .sh{display:flex;align-items:center;gap:8px;font-weight:800;font-size:13px}
.seccard .sh::before{content:"";width:3px;height:15px;background:currentColor;box-shadow:0 0 10px currentColor;border-radius:2px}
.seccard .big{margin-left:auto;font-size:21px;font-weight:800;text-shadow:0 0 10px currentColor}
.seccard .dlt{font-size:10px;font-weight:700;margin-left:6px}
.seccard .mini{display:flex;gap:12px;font-size:10.5px;color:#7b8696;margin-top:8px;flex-wrap:wrap}
.seccard .segbar{display:flex;height:6px;border-radius:4px;overflow:hidden;margin-top:9px;background:#0a0a0a}
.seccard .members{margin-top:10px;display:flex;flex-wrap:wrap;gap:4px}
.mem{font-size:10px;padding:2px 6px;border-radius:5px;background:#101016;border:1px solid #1a1a22;cursor:pointer}
.mem:hover{border-color:#FF8C3255}
.anom{display:flex;align-items:center;gap:8px;padding:9px 14px;border-bottom:1px solid #101016;cursor:pointer;font-size:12px}
.anom:hover{background:#171717}.anom .sev{font-weight:800;min-width:34px;text-align:right}
.atag{font-size:9px;font-weight:800;padding:1px 6px;border-radius:4px}
.flagpill{font-size:9px;font-weight:700;padding:1px 5px;border-radius:4px;margin-left:3px;border:1px solid}
.fl-decay{color:#EF4444;border-color:#EF444466}.fl-earn{color:#F5B45B;border-color:#F5B45B66}.fl-iv{color:#FFB23F;border-color:#FFB23F55}.fl-spread{color:#5b6678;border-color:#2a2a33}
.bkt{font-size:9px;font-weight:800;padding:1px 6px;border-radius:5px}
.bkt-court{color:#FFB23F;background:rgba(255,234,0,.12)}.bkt-moyen{color:#FFB23F;background:rgba(255,178,63,.12)}.bkt-long{color:#FF8C32;background:rgba(255,140,50,.12)}
.tick{display:inline-block;width:7px;height:7px;border-radius:50%;background:#FF8C32;box-shadow:0 0 8px #FF8C32;margin-left:6px}
.tick.flash{animation:tk .5s}@keyframes tk{0%{box-shadow:0 0 16px #FF8C32;transform:scale(1.6)}100%{box-shadow:0 0 8px #FF8C32}}
/* ===== LISIBILITÉ : moins de néon, contraste max, chiffres NETS ===== */
body{color:#eaf0fa}
.muted{color:#8794ab}
.sc,.kpi .v,.up,.dn,.grade,.big,.bv,.pep,.sym,.hsym{text-shadow:none}
.up{color:#FF8C32}.dn{color:#EF4444}
.b-achat,.b-evit,.b-surv{box-shadow:none}
.scard .shead{font-size:13px}
.dhead .ttl{text-shadow:0 0 12px #FF8C3244}
.scard tbody td{padding:10px 14px;font-size:13px}
.scard thead th{font-size:10.5px;color:#8794ab}
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style id="m-fix">
html,body{overflow-x:hidden;max-width:100%}
@media(max-width:640px){
  .grid,.cols,.herorow,.hero,.panorama,.layout,.heat,.secgrid,.poster{grid-template-columns:1fr!important}
  [style*="grid-template-columns"]{grid-template-columns:1fr!important}
  .top3{grid-template-columns:1fr 1fr 1fr!important}
  .plan{grid-template-columns:1fr 1fr!important}
  td,th{padding-left:9px!important;padding-right:9px!important}
  table{font-size:11.5px!important}
  .wrap,.daily{padding-left:12px!important;padding-right:12px!important}
  .phead-top{padding:15px 16px!important}
  .htitle{font-size:25px!important}
  .hmeta{margin-left:0!important;text-align:left!important}
  .back{padding:5px 9px!important;font-size:11px!important}
}
</style>
<style id="nav-css">
#gnav{position:sticky;top:0;z-index:1000;background:rgba(10,10,12,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid #1c1c24}
.gnav-in{max-width:1500px;margin:0 auto;display:flex;align-items:center;gap:12px;padding:8px 16px;flex-wrap:wrap}
.gnav-links{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
#gnav a{color:#aab4c4;text-decoration:none;font-size:13px;font-weight:700;padding:7px 12px;border-radius:9px;white-space:nowrap}
#gnav a:hover{background:#ffffff0d;color:#fff}
#gnav a.act{background:rgba(245,180,91,.14);color:#F5B45B}
.gnav-search{display:flex;gap:6px;align-items:center}
.gnav-search input{background:#0e0e0e;border:1px solid #1c1c24;color:#e8edf5;border-radius:9px;padding:7px 11px;font-size:13px;width:210px;max-width:46vw}
.gnav-search button{background:rgba(245,180,91,.12);border:1px solid #F5B45B55;color:#F5B45B;border-radius:9px;padding:7px 13px;font-weight:800;cursor:pointer}
.back{display:none!important}
@media(max-width:640px){.gnav-in{padding:6px 10px;gap:8px}.gnav-links{order:2;overflow-x:auto;flex-wrap:nowrap}#gnav a{font-size:12px;padding:6px 9px}.gnav-search{order:1;width:100%}.gnav-search input{flex:1;max-width:none;width:auto}}
/* === systeme de design premium (toutes pages) === */
body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;background:radial-gradient(1200px 780px at 50% -8%,#0d0d12,#050506 60%)!important}
.panel,.scard,.card,.pcard,.seccard,.tile,.kpi,.fav,.pchip,.stat{background:linear-gradient(165deg,#16171c,#0d0e12)!important;border:1px solid rgba(255,255,255,.08)!important;border-radius:16px!important}
.panel,.scard,.card,.pcard,.fav,.seccard{transition:border-color .2s ease,box-shadow .2s ease,transform .18s ease!important}
.panel:hover,.scard:hover,.card:hover,.fav:hover,.seccard:hover{border-color:rgba(255,140,50,.32)!important;box-shadow:0 0 26px rgba(255,140,50,.10)!important}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#26262e;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:#3a3a44}
::selection{background:rgba(255,140,50,.28);color:#fff}
/* bandeau KPI (cockpit + autres) */
.kpiband{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin:14px 0}
.kc{background:linear-gradient(165deg,#16171c,#0d0e12);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:11px 12px;min-width:0}
.kc .kt{font-size:8.5px;letter-spacing:.6px;text-transform:uppercase;color:#8794ab;display:flex;align-items:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kv{font-size:17px;font-weight:900;margin-top:3px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .ks{font-size:9px;color:#6b7689;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kbar{height:5px;border-radius:4px;background:#0a0a0a;overflow:hidden;margin-top:6px}
.kc .kbar>div{height:100%;border-radius:4px}
@media(max-width:760px){.kpiband{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:44%;overflow-x:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;padding-bottom:2px}.kc{scroll-snap-align:start}}
/* navigation mobile sticky en bas */
#mnav{display:none}
@media(max-width:760px){
  #mnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:1200;background:rgba(8,8,10,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid rgba(255,255,255,.09);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
  #mnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;text-decoration:none;color:#8794ab;font-size:9.5px;font-weight:700;padding:5px 2px;border-radius:12px}
  #mnav a .mi{font-size:19px;line-height:1}
  #mnav a.act{color:#FF8C32;background:rgba(255,140,50,.13)}
  body{padding-bottom:68px!important}
  #gnav .gnav-links{display:none}
}
</style>
<script>
(function(){var L=[['/','🔱 Cockpit'],['/ma-page','⭐ Ma Page'],['/watchlist','📋 Watchlist'],['/entreprises','🏢 Entreprises'],['/options','💎 Options']];
function build(){if(document.getElementById('gnav'))return;var p=location.pathname;
var links=L.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'>'+x[1]+'</a>';}).join('');
var nav=document.createElement('nav');nav.id='gnav';
nav.innerHTML='<div class="gnav-in"><div class="gnav-links">'+links+'</div><form class="gnav-search" onsubmit="return gnavGo(event)"><input id="gnavq" placeholder="Rechercher un titre… (ex: NVDA)" autocomplete="off"><button type="submit">→</button></form></div>';
document.body.insertBefore(nav,document.body.firstChild);
var M=[['/','🏠','Cockpit'],['/watchlist','📡','Scan'],['/ma-page','⭐','Ma Page'],['/options','💎','Options'],['/entreprises','🏢','Titres']];
var mn=document.createElement('nav');mn.id='mnav';
mn.innerHTML=M.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'><span class="mi">'+x[1]+'</span>'+x[2]+'</a>';}).join('');
document.body.appendChild(mn);}
window.gnavGo=function(e){e.preventDefault();var v=(document.getElementById('gnavq').value||'').trim().toUpperCase();if(v)location.href='/titre/'+encodeURIComponent(v);return false;};
if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();})();
</script></head><body>
<div class="topbar"><span class="back" style="font-weight:800;letter-spacing:1px">◣ TRADING DESK</span><span id="dLive" style="font-size:11px;margin-left:14px;color:#8794ab">· connexion…</span><span class="tick" id="dTick"></span></div>
<div class="daily">
  <div class="dhead">
    <div>
      <div class="ttl">◣ TRADING DESK <span class="v">WATCHLIST</span></div>
      <div class="sub">57 LEADERS US · SCORÉS · CLASSÉS · LONG-ONLY — TON BRIEF QUOTIDIEN</div>
    </div>
    <div class="meta">
      <span class="pill" id="dSpy">SPY $—</span>
      <span class="pill shut" id="dMkt"><span class="pdot"></span>…</span>
      <span class="pill" id="dDate"><span class="pdot"></span>MAJ —</span>
    </div>
  </div>
  <div class="idxstrip" id="dIndices"></div>
  <div class="kpiband" id="dKpi"></div>
  <div id="dDetail" style="display:none;margin-bottom:14px"></div>
  <div class="stitle">📊 ÉTAT DU MARCHÉ</div>
  <div id="dVerdict" style="background:linear-gradient(135deg,#121212,#0a0a0a);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:16px 20px;margin:4px 0 14px">
    <div id="dCmd" style="margin-bottom:14px;padding-bottom:14px;border-bottom:1px solid #ffffff0d"></div>
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:14px">
      <div><div style="font-size:10px;letter-spacing:2px;color:#8794ab;font-weight:700;margin-bottom:6px">🧭 VERDICT DU JOUR</div>
      <div id="dVerdictTxt" style="font-size:17px;font-weight:800;letter-spacing:.3px">lecture du marché…</div></div>
      <div id="dTrend" style="text-align:center;min-width:158px;padding:0 6px"></div>
      <div id="dVerdictTags" style="display:flex;gap:8px;flex-wrap:wrap"></div>
    </div>
    <div id="dPartic" style="margin-top:15px;padding-top:14px;border-top:1px solid #ffffff0d"></div>
  </div>
  <div class="stitle">⭐ L'ACTION DU JOUR <span class="muted" style="font-weight:400;letter-spacing:0;font-size:11px">· le meilleur trade du jour, sélectionné par le moteur IBKR</span></div>
  <div id="dStar" style="margin-bottom:18px"></div>
  <div class="khero" id="dHero2"></div>
  <div class="scard" style="margin-bottom:14px"><div class="shead"><span class="ico">🛡️</span> RISK CENTER · santé du marché & garde-fous</div><div class="riskgrid" id="dRisk" style="padding:16px"></div></div>
  <div class="stitle">🎯 OPPORTUNITÉS DU JOUR <span class="muted" style="font-weight:400;letter-spacing:0;font-size:11px">· le meilleur setup + ce qui a changé + tous les signaux</span></div>
  <div class="herorow">
    <div class="hero" id="dHero"><span class="muted" style="padding:20px">chargement…</span></div>
    <div class="scard s-green"><div class="shead"><span class="ico">🔄</span> CE QUI A CHANGÉ DEPUIS HIER</div><div id="dChanges" style="padding:14px"></div></div>
  </div>
  <div class="stitle">📊 MARCHÉ INTERNE <span class="muted" style="font-weight:400;letter-spacing:0;font-size:11px">· participation & répartition des 57 leaders</span></div>
  <div class="breadth">
    <span class="bl">BREADTH LEADERS (% ACHAT)</span>
    <div class="bar"><div class="fill" id="dBreadthFill" style="width:0%"></div></div>
    <span class="bv" id="dBreadthVal">—</span>
    <div class="seg" id="dSeg"></div>
  </div>
  <div class="panorama" style="margin-bottom:6px">
    <div class="pcard"><div class="muted" style="font-size:11px;letter-spacing:1px;margin-bottom:8px">RÉPARTITION VERDICTS</div><canvas id="dDonut" height="168"></canvas></div>
    <div class="pcard"><div class="muted" style="font-size:11px;letter-spacing:1px;margin-bottom:10px">MARCHÉ INTERNE · 57 leaders</div><div id="dInternals"></div></div>
  </div>
  <div class="stitle">⚡ TON PLAN DU JOUR <span class="muted" style="font-weight:400;letter-spacing:0;font-size:11px">· recalculé en continu · tout l'univers + tes positions passés au crible · sur quoi travailler aujourd'hui</span></div>
  <div id="dPlan" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(265px,1fr));gap:14px;margin-bottom:22px"></div>
  <div id="dCapital" style="display:none"></div>
  <div class="stitle">🧮 IBKR DECISION BOARD <span class="muted" style="font-weight:400;letter-spacing:0;font-size:11px">· répartition des décisions du moteur</span></div>
  <div id="dDecBoard" style="margin-bottom:16px"></div>
  <div class="stitle">🎯 RECOMMANDATIONS IBKR <span class="muted" style="font-weight:400;letter-spacing:0;font-size:11px">· score /40 · niveau S+/S/A/B · timing · clic → verdict complet</span></div>
  <div class="secgrid" id="dRecs"></div>
  <div class="stitle">📊 LES PLUS GROS MOUVEMENTS <span id="dMoversSess" style="font-weight:700;letter-spacing:0;font-size:11px"></span></div>
  <div class="panorama" style="margin-bottom:14px">
    <div class="pcard"><div style="font-size:11px;letter-spacing:1px;margin-bottom:10px;color:#22C55E;font-weight:700">📈 TOP HAUSSES</div><div id="dGainers"></div></div>
    <div class="pcard"><div style="font-size:11px;letter-spacing:1px;margin-bottom:10px;color:#EF4444;font-weight:700">📉 TOP BAISSES</div><div id="dLosers"></div></div>
  </div>
  <div class="stitle">📅 AUJOURD'HUI · CATALYSEURS & ACTUS</div>
  <div class="panorama" style="margin-bottom:14px">
    <div class="pcard"><div class="muted" style="font-size:11px;letter-spacing:1px;margin-bottom:10px">📅 CATALYSEURS · RÉSULTATS À VENIR</div><div id="dCatalysts"></div></div>
    <div class="pcard"><div class="muted" style="font-size:11px;letter-spacing:1px;margin-bottom:10px">📰 ACTUS DU MARCHÉ · live</div><div id="dNews2"></div></div>
  </div>
  <div class="stitle">⭐ WATCHLIST DE LA SEMAINE <span class="muted" style="font-weight:400;letter-spacing:0;font-size:11px">· sélection figée du lundi · actions + options · clic → fiche</span></div>
  <div class="secgrid" id="dWeekly"></div>
  <div class="stitle">💼 MON PORTEFEUILLE & SUIVI</div>
  <div id="dIbkrCard" style="margin-bottom:14px"></div>
  <div style="background:#111111;border:1px solid rgba(255,255,255,.07);border-radius:14px;padding:14px 18px;margin-bottom:14px">
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <span style="font-size:10px;letter-spacing:2px;color:#8794ab;font-weight:700;white-space:nowrap">💼 MES POSITIONS · SUIVI LIVE</span>
      <input id="posIn" placeholder="ex : AAPL 195.50 x10   (ou   NVDA 180)" onkeydown="if(event.key==='Enter')addPos()" style="flex:1;min-width:200px;background:#0a0a0a;border:1px solid rgba(255,255,255,.1);border-radius:9px;padding:9px 13px;color:#eaf0fa;font-size:13px">
      <button onclick="addPos()" style="background:rgba(255,140,50,.15);color:#FF8C32;border:1px solid #FF8C3255;border-radius:9px;padding:9px 16px;font-weight:700;cursor:pointer;font-size:13px">+ Suivre</button>
    </div>
    <div id="posBody" style="margin-top:8px"></div>
  </div>
  <div class="stitle">🔥 ROTATION SECTORIELLE <span class="muted" style="font-weight:400;letter-spacing:0;font-size:11px">· classé par force · ▲/▼ vs hier · clic titre → fiche</span></div>
  <div class="secgrid" id="dSectors"></div>

  <div class="poster">
    <div class="scard s-green span2"><div class="shead"><span class="ico">🎯</span> TOP SCANNER PICKS<span class="cnt" id="dPicksCnt"></span></div>
      <table><thead><tr><th>TITRE</th><th>$ / VAR</th><th>SCORE</th><th>GRADE</th><th>VERDICT</th><th>SIG</th><th>RVOL</th><th>STOP / TP2</th></tr></thead><tbody id="dPicks"></tbody></table></div>
    <div class="scard s-violet span2"><div class="shead"><span class="ico">⚔️</span> SWING TRADES — PLAN COMPLET<span class="cnt" id="dSwingCnt"></span></div>
      <table><thead><tr><th>TITRE</th><th>GRADE·SCORE</th><th>RVOL</th><th>ENTRÉE</th><th>STOP</th><th>RISK</th><th>TP1 / TP2 / TP3</th><th>R:R</th></tr></thead><tbody id="dSwing"></tbody></table></div>
    <div class="scard s-yellow"><div class="shead"><span class="ico">⚡</span> LIVE MOMENTUM<span class="cnt">TOP HAUSSES</span></div>
      <table><thead><tr><th>TITRE</th><th>VAR J</th><th>ROC 1M</th><th>RSI</th><th>FORCE</th><th>RVOL</th></tr></thead><tbody id="dMom"></tbody></table></div>
    <div class="scard s-cyan span2"><div class="shead"><span class="ico">💎</span> OPTIONS DU JOUR · COURT / MOYEN / LONG<span class="cnt" id="dOptCnt"></span></div>
      <table><thead><tr><th>TITRE</th><th>BUCKET</th><th>ÉCHÉ.</th><th>STRIKE</th><th>QUALITÉ</th><th>PROBA</th><th>DANGER</th><th>Δ</th><th>COÛT</th><th>SI CIBLE</th><th>FLAGS</th></tr></thead><tbody id="dOpt"></tbody></table>
      <div class="muted" style="padding:8px 14px;font-size:10px">⚠️ COURT = théta violent (érosion ~2%/jour) — tactique, tenu jours/semaines, jamais jusqu'à l'échéance. yfinance différé ~15min.</div></div>
    <div class="scard s-violet span2"><div class="shead"><span class="ico">⚡</span> ANOMALIES DU JOUR · ce qui sort de l'ordinaire<span class="cnt" id="dAnomCnt"></span></div>
      <div id="dAnoms"></div></div>
    <div class="scard s-green"><div class="shead"><span class="ico">🚀</span> TOP MOVERS (leaders)<span class="cnt">VAR ↕ × VOL</span></div>
      <table><thead><tr><th>TITRE</th><th>VAR J</th><th>SCORE</th><th>RVOL</th><th>VERDICT</th></tr></thead><tbody id="dMovers"></tbody></table></div>
    <div class="scard s-violet"><div class="shead"><span class="ico">📈</span> SECOND LEG (reprise saine)<span class="cnt">non surextendu</span></div>
      <table><thead><tr><th>TITRE</th><th>SCORE·GRADE</th><th>EXT</th><th>RSI</th><th>ENTRÉE / STOP</th></tr></thead><tbody id="dSecond"></tbody></table></div>
    <div class="scard s-yellow"><div class="shead"><span class="ico">⚠️</span> GARDE-FOUS (surchauffe)<span class="cnt">prudence</span></div>
      <table><thead><tr><th>TITRE</th><th>GRADE</th><th>RSI</th><th>EXT</th><th>ALERTE</th></tr></thead><tbody id="dGuard"></tbody></table></div>
    <div class="scard s-red"><div class="shead"><span class="ico">🛑</span> À ÉVITER<span class="cnt">exclusion</span></div>
      <table><thead><tr><th>TITRE</th><th>SCORE</th><th>GRADE</th><th>RSI</th><th>RAISON</th></tr></thead><tbody id="dAvoid"></tbody></table></div>
  </div>

  <div class="dfoot">
    <div class="slogan">TRADE SMART · TRADE STRONG</div>
    <div class="dis">TRADING DESK · DONNÉES yfinance DIFFÉRÉ ~15MIN · ANALYSE ONLY — AUCUN ORDRE, AUCUNE EXÉCUTION · NOT FINANCIAL ADVICE</div>
  </div>
</div>
<script>
function q(id){return document.getElementById(id)}
function clr(s){return s>=72?'#FF8C32':s>=55?'#FFB23F':'#EF4444'}
function gcls(g){return g==='S+'||g==='S'?'g-sp':g==='A'?'g-a':g==='B'?'g-b':'g-c'}
function bcls(v){return v==='BUY'?'b-achat':(v==='WATCH'||v==='WAIT')?'b-surv':'b-evit'}
function vfr(v){return {BUY:'ACHAT',WATCH:'SURVEILLER',WAIT:'ATTENTE',AVOID:'ÉVITER'}[v]||v}
function rv(v){v=v||0;const c=v>=1.5?'hot':v>=1.0?'warm':'cold';return `<span class="rvol ${c}">${v.toFixed(2)}×</span>`}
function chg(c){c=c||0;return `<span class="${c>=0?'up':'dn'}">${c>=0?'+':''}${c}%</span>`}
function go(s){location.href='/analyse?sym='+s}
function er(n,t){return `<tr><td colspan="${n}" class="muted" style="text-align:center;padding:16px">${t}</td></tr>`}
let __spkN=0;
function spark(arr,w=82,h=22,days=24){
  if(!arr||arr.length<2)return '';
  const d=arr.slice(-days).filter(v=>v!=null&&!isNaN(v));if(d.length<2)return '';
  const up=d[d.length-1]>=d[0],col=up?'#FF8C32':'#EF4444',gid='s'+(++__spkN);
  const mn=Math.min(...d),mx=Math.max(...d),rg=(mx-mn)||1,pad=2,iw=w-pad*2,ih=h-pad*2;
  const X=i=>pad+(i/(d.length-1))*iw,Y=v=>pad+ih-((v-mn)/rg)*ih;
  const pts=d.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)),line='M'+pts.join(' L');
  const area=line+' L'+X(d.length-1).toFixed(1)+','+(h-pad)+' L'+X(0).toFixed(1)+','+(h-pad)+' Z';
  const lx=X(d.length-1).toFixed(1),ly=Y(d[d.length-1]).toFixed(1);
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs><linearGradient id="${gid}f" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity="0.30"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs><path d="${area}" fill="url(#${gid}f)"/><path d="${line}" fill="none" stroke="${col}" stroke-width="1.4" stroke-linejoin="round"/><circle cx="${lx}" cy="${ly}" r="1.8" fill="${col}"><animate attributeName="r" values="1.8;3;1.8" dur="1.6s" repeatCount="indefinite"/></circle></svg>`;
}
function bkpill(b){return `<span class="bkt bkt-${b||'long'}">${(b||'long').toUpperCase()}</span>`}
function flagpills(fl){return (fl||[]).map(f=>{const c=f.indexOf('decay')>=0?'fl-decay':f.indexOf('earn')>=0?'fl-earn':f.indexOf('IV')>=0?'fl-iv':'fl-spread';return `<span class="flagpill ${c}">${f}</span>`}).join('')}
let __donut=null,__secbar=null;
function renderCharts(d){
  if(typeof Chart==='undefined')return;
  const rows=d.rows||[];
  const nB=rows.filter(r=>r.verdict==='BUY').length,nW=rows.filter(r=>r.verdict==='WATCH'||r.verdict==='WAIT').length,nA=rows.filter(r=>r.verdict==='AVOID').length;
  const dc=q('dDonut');
  if(dc){if(__donut)__donut.destroy();__donut=new Chart(dc,{type:'doughnut',data:{labels:['ACHAT','SURV.','ÉVITER'],datasets:[{data:[nB,nW,nA],backgroundColor:['#22C55E','#FF8C32','#EF4444'],borderWidth:0}]},options:{cutout:'66%',plugins:{legend:{position:'bottom',labels:{color:'#9aa7bd',font:{size:10},boxWidth:10,padding:8}}}}});}
  const secs=d.sectors||[],sb=q('dSecBar');
  if(sb&&secs.length){if(__secbar)__secbar.destroy();__secbar=new Chart(sb,{type:'bar',data:{labels:secs.map(s=>s.sector),datasets:[{data:secs.map(s=>s.avg_score),backgroundColor:secs.map(s=>s.avg_score>=72?'#FF8C32':s.avg_score>=55?'#FFB23F':'#EF4444'),borderRadius:4}]},options:{indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{max:100,ticks:{color:'#5b6678',font:{size:9}},grid:{color:'#0a0a0a'}},y:{ticks:{color:'#9aa7bd',font:{size:10}},grid:{display:false}}}}});}
}
function renderSectors(d){
  const secs=d.sectors||[];
  q('dSectors').innerHTML=secs.map(s=>{
    const col=s.avg_score>=72?'#FF8C32':s.avg_score>=55?'#FFB23F':'#EF4444';
    const dl=s.delta==null?'':(s.delta>=0?`<span class="dlt up">▲+${s.delta}</span>`:`<span class="dlt dn">▼${s.delta}</span>`);
    const tot=Math.max(s.n,1);
    const seg=`<div class="segbar"><div style="width:${s.n_buy/tot*100}%;background:#FF8C32"></div><div style="width:${s.n_watch/tot*100}%;background:#FFB23F"></div><div style="width:${s.n_avoid/tot*100}%;background:#EF4444"></div></div>`;
    const mem=(s.members||[]).map(m=>`<span class="mem" onclick="event.stopPropagation();go('${m.symbol}')" style="color:${clr(m.score)}">${m.symbol} ${m.score}</span>`).join('');
    return `<div class="seccard" style="color:${col}">
      <div class="sh">${s.icon} ${s.sector}<span class="big" style="color:${col}">${s.avg_score}</span>${dl}</div>
      <div class="mini"><span>${s.n_buy}/${s.n} ACHAT</span><span class="${s.avg_change>=0?'up':'dn'}">${s.avg_change>=0?'+':''}${s.avg_change}%</span><span>RS ${s.avg_rs}</span><span>RVOL ${s.avg_rvol}×</span><span class="rb rb-${s.risk_band.toLowerCase()}">${s.risk_band}</span></div>
      ${seg}<div class="members">${mem}</div></div>`;
  }).join('')||'<span class="muted">secteurs en calcul…</span>';
}
function renderAnomalies(d){
  const a=d.anomalies||[];q('dAnomCnt').textContent=a.length+' SIGNAUX';
  const tint=x=>(x.dir==='WARN'||x.dir==='DOWN')?'#EF4444':x.dir==='NEUTRAL'?'#FFB23F':'#FF8C32';
  q('dAnoms').innerHTML=a.length?a.slice(0,12).map(x=>{const c=tint(x);const buy=(x.dir==='UP');return `<div class="anom" onclick="go('${x.symbol}')" style="display:block;padding:11px 14px">
    <div style="display:flex;align-items:center;gap:8px">
      <span class="sym" style="min-width:50px">${x.symbol}</span>
      <span class="atag" style="color:${c};background:${c}22">${buy?'▲ ':x.dir==='DOWN'||x.dir==='WARN'?'▼ ':''}${x.label}</span>
      <span class="muted" style="flex:1;font-size:12px">${x.note}</span>
      <span class="sev" style="color:${c};font-weight:800">${x.sev}</span></div>
    ${x.interest?`<div class="muted" style="font-size:11px;margin:6px 0 0 10px;padding-left:10px;line-height:1.55;border-left:2px solid ${c}55">${buy?'💡 Pourquoi acheter : ':'⚠️ '}${x.interest}</div>`:''}</div>`}).join(''):'<div class="muted" style="padding:16px">aucune anomalie — marché calme</div>';
}

function renderKPI(d){
  const el=q('dKpi'); if(!el) return;
  const mc=d.market_ctx||{}, br=mc.breadth||{}, secs=d.sectors||[];
  const card=(ic,t,v,vc,s,bar)=>`<div class="kc"><div class="kt">${ic} ${t}</div><div class="kv" style="color:${vc||'#e8edf5'}">${v}</div>${s?`<div class="ks">${s}</div>`:''}${bar||''}</div>`;
  const reg=mc.spy_regime, rl=reg==='TREND'?['Bull trend','#22C55E']:reg==='CHOP'?['Range','#FFB23F']:reg==='NEUTRAL'?['Neutre','#8794ab']:['—','#8794ab'];
  const ro=mc.roro, rm=ro==='RISK-ON'?['Risk-On','#22C55E']:ro==='RISK-OFF'?['Risk-Off','#EF4444']:ro?['Neutre','#FFB23F']:['—','#8794ab'];
  const vix=mc.vix, vb=mc.vix_band, vc=vb==='calme'?'#22C55E':vb==='stress'?'#EF4444':'#FFB23F';
  const b50=br.above50, b50c=b50>=55?'#22C55E':b50>=40?'#FF8C32':'#EF4444';
  const b200=br.above200, b200c=b200>=55?'#22C55E':b200>=40?'#FF8C32':'#EF4444';
  const adv=br.adv, dec=br.dec, tot=(adv!=null&&dec!=null)?adv+dec:null;
  const bs=secs[0], sn=d.scanned_n, un=d.universe_n;
  el.innerHTML=
     card('🎯','Régime',rl[0],rl[1],'tendance marché')
    +card('⚖️','Risk Mode',rm[0],rm[1],ro?('écart '+(mc.roro_gap>=0?'+':'')+(mc.roro_gap!=null?mc.roro_gap:0)):'cyclique/défensif')
    +card('🌫️','VIX',vix!=null?vix:'—',vc,vix!=null?((vb||'')+(mc.vix_chg!=null?' · '+(mc.vix_chg>=0?'+':'')+mc.vix_chg+'%':'')):'volatilité')
    +card('📈','Breadth 50',b50!=null?b50+'%':'—',b50c,'au-dessus MM50',b50!=null?`<div class="kbar"><div style="width:${b50}%;background:${b50c}"></div></div>`:'')
    +card('📉','Breadth 200',b200!=null?b200+'%':'—',b200c,'au-dessus MM200',b200!=null?`<div class="kbar"><div style="width:${b200}%;background:${b200c}"></div></div>`:'')
    +card('🟢','Participation',tot!=null?(adv+'/'+tot):'—','#e8edf5',tot!=null?(dec+' en baisse'):'avances/déclins')
    +card('🏆','Secteur leader',bs?bs.sector:'—','#FF8C32',bs?((bs.avg_change>=0?'+':'')+bs.avg_change+'% · sc '+bs.avg_score):'meilleur secteur')
    +card('📡','Scan',sn!=null?(sn+'/'+un):'—','#e8edf5',sn!=null?('analysés · MAJ '+(d.updated||'—')):'statut scan');
}
function donutScore(score,color){
  const r=30,c=2*Math.PI*r,off=c*(1-Math.max(0,Math.min(100,score))/100);
  return `<svg viewBox="0 0 80 80" style="width:80px;height:80px;flex-shrink:0"><circle cx="40" cy="40" r="${r}" fill="none" stroke="#1a1a22" stroke-width="8"/><circle cx="40" cy="40" r="${r}" fill="none" stroke="${color}" stroke-width="8" stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}" transform="rotate(-90 40 40)" style="transition:stroke-dashoffset .6s ease"/><text x="40" y="39" text-anchor="middle" font-size="21" font-weight="800" fill="#fff">${score}</text><text x="40" y="53" text-anchor="middle" font-size="8" fill="#8794ab">/100</text></svg>`;
}
function renderCmd(d){
  const el=q('dCmd'); if(!el) return;
  const mc=d.market_ctx||{}, br=mc.breadth||{};
  let s=0;
  s+=mc.spy_regime==='TREND'?35:mc.spy_regime==='NEUTRAL'?18:mc.spy_regime==='CHOP'?6:14;
  s+=mc.roro==='RISK-ON'?25:mc.roro==='RISK-OFF'?2:mc.roro?12:12;
  s+=Math.round((br.above50!=null?br.above50:50)/100*25);
  s+=mc.vix_band==='calme'?15:mc.vix_band==='stress'?2:mc.vix_band?8:8;
  s=Math.max(0,Math.min(100,Math.round(s)));
  const v=s>=65?['MARCHÉ FAVORABLE','#22C55E']:s>=40?['MARCHÉ NEUTRE','#FFB23F']:['MARCHÉ DANGEREUX','#EF4444'];
  const gap=mc.roro_gap!=null?mc.roro_gap:0, rpos=Math.max(2,Math.min(98,(gap+30)/60*100));
  el.innerHTML=`<div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">${donutScore(s,v[1])}
    <div style="flex:1;min-width:200px">
      <div style="font-size:9px;letter-spacing:2px;color:#8794ab;font-weight:700">🛰️ MARKET COMMAND CENTER · SCORE MARCHÉ</div>
      <div style="font-size:22px;font-weight:900;color:${v[1]};line-height:1.1;margin:3px 0 9px">${v[0]}</div>
      <div style="display:flex;align-items:center;gap:8px;font-size:10px;font-weight:700"><span style="color:#EF4444">RISK-OFF</span><div style="flex:1;height:8px;border-radius:5px;background:linear-gradient(90deg,#EF4444,#FFB23F,#22C55E);position:relative"><div style="position:absolute;top:-2px;left:calc(${rpos}% - 6px);width:12px;height:12px;border-radius:50%;background:#fff;border:2px solid #0b0b0d;box-shadow:0 0 7px rgba(0,0,0,.7)"></div></div><span style="color:#22C55E">RISK-ON</span></div>
    </div></div>`;
}
function renderDaily(d){
  try{renderKPI(d);renderCmd(d);}catch(e){}
  const dy=d.daily||{}, sec=dy.sections||{}, DET=d.detail||{};
  const spk=s=>spark((DET[s]||{}).series&&DET[s].series.close);
  // VERDICT DU JOUR (contexte marché)
  const mc=d.market_ctx||{};
  if(q('dVerdictTxt')){
    q('dVerdictTxt').textContent=mc.verdict||'données marché en cours…';
    const tag=(lab,val,col)=>`<span style="font-size:11px;font-weight:800;letter-spacing:.5px;padding:6px 12px;border-radius:20px;background:${col}1f;color:${col};border:1px solid ${col}55">${lab} ${val}</span>`;
    const rc=mc.spy_regime==='TREND'?'#FF8C32':mc.spy_regime==='CHOP'?'#EF4444':'#FFB23F';
    const ro=mc.roro==='RISK-ON'?'#FF8C32':mc.roro==='RISK-OFF'?'#EF4444':'#FFB23F';
    const vc=mc.vix_band==='calme'?'#FF8C32':mc.vix_band==='stress'?'#EF4444':'#FFB23F';
    q('dVerdictTags').innerHTML=(mc.spy_regime?tag('RÉGIME',mc.spy_regime==='TREND'?'TENDANCE':mc.spy_regime==='CHOP'?'RANGE':'NEUTRE',rc):'')
      +(mc.roro?tag('',mc.roro,ro):'')+(mc.vix!=null?tag('VIX',mc.vix+(mc.vix_chg!=null?` (${mc.vix_chg>=0?'+':''}${mc.vix_chg}%)`:''),vc):'');
  }
  const pq=q('dPartic');
  if(pq){const br=mc.breadth||{};const bb=(d.breadth!=null?d.breadth:(br.buy!=null?br.buy:0))||0;
    const bcol=bb>=55?'#22C55E':bb>=40?'#FF8C32':'#EF4444';
    pq.innerHTML=`<div style="display:flex;align-items:center;gap:13px;flex-wrap:wrap">`
      +`<span style="font-size:9.5px;letter-spacing:1.5px;color:#8794ab;font-weight:700;white-space:nowrap">PARTICIPATION</span>`
      +`<div style="flex:1;min-width:140px;height:9px;border-radius:6px;background:#0a0a0a;overflow:hidden"><div style="height:100%;width:${bb}%;background:linear-gradient(90deg,#EF4444,#FF8C32 55%,#22C55E);border-radius:6px"></div></div>`
      +`<span style="font-weight:800;color:${bcol};font-size:15px;min-width:88px;text-align:right">${bb}% <span style="font-size:10px;color:#8794ab">ACHAT</span></span></div>`
      +`<div class="muted" style="font-size:11px;margin-top:9px;display:flex;gap:14px;flex-wrap:wrap">`
      +(br.above50!=null?`<span>📈 <b style="color:#cfd8e6">${br.above50}%</b> > MM50</span>`:'')
      +(br.above200!=null?`<span><b style="color:#cfd8e6">${br.above200}%</b> > MM200</span>`:'')
      +(br.adv!=null?`<span><b class="up">${br.adv}↑</b> / <b class="dn">${br.dec}↓</b></span>`:'')
      +(br.nh!=null?`<span>🚀 ${br.nh} plus-hauts · ${br.nl} plus-bas (52s)</span>`:'')+`</div>`;
  }
  if(d.spy)q('dSpy').innerHTML=`SPY $${d.spy.price} ${chg(d.spy.change)}`;
  const m=d.market||{};const mk=q('dMkt');mk.className='pill '+(m.open?'live':'shut');
  mk.innerHTML=`<span class="pdot"></span>${m.open?'MARCHÉ OUVERT':'MARCHÉ FERMÉ'} · ${m.et||'—'}`;
  q('dDate').innerHTML=`<span class="pdot"></span>MAJ ${d.updated||'—'}`;
  // bandeau EN DIRECT (topbar) — géré par updateLiveBanner (cours IBKR live ou yfinance différé)
  updateLiveBanner();
  // TENDANCE DU MARCHÉ (direction live)
  const tr=q('dTrend');if(tr){const idx=(d.indices||[]).filter(i=>!i.vix);const up=idx.filter(i=>i.change>0).length,dn=idx.filter(i=>i.change<0).length;const bb=(d.breadth!=null?d.breadth:0)||0;
    let dir,dc,ar;if(up>=3&&bb>=52){dir='HAUSSIÈRE';dc='#22C55E';ar='▲';}else if(dn>=3&&bb<=48){dir='BAISSIÈRE';dc='#EF4444';ar='▼';}else if(up>dn){dir='LÉGÈRE HAUSSE';dc='#FFB23F';ar='▲';}else if(dn>up){dir='LÉGÈRE BAISSE';dc='#FFB23F';ar='▼';}else{dir='NEUTRE';dc='#FFB23F';ar='→';}
    tr.innerHTML=`<div style="font-size:9px;letter-spacing:2px;color:#8794ab;font-weight:700;margin-bottom:3px">📈 TENDANCE</div><div style="font-size:18px;font-weight:800;color:${dc};text-shadow:0 0 12px ${dc}55">${ar} ${dir}</div><div class="muted" style="font-size:10px;margin-top:2px">${up} indices ↑ · ${dn} ↓ · ampleur ${bb}%</div>`;}
  const b=(d.breadth!=null?d.breadth:(dy.meta?dy.meta.breadth:0))||0;
  q('dBreadthFill').style.width=b+'%';q('dBreadthVal').textContent=b+'%';
  const rows=d.rows||[];
  const nB=rows.filter(r=>r.verdict==='BUY').length,nW=rows.filter(r=>r.verdict==='WATCH'||r.verdict==='WAIT').length,nA=rows.filter(r=>r.verdict==='AVOID').length;
  q('dSeg').innerHTML=`<span class="gb">● ACHAT <b>${nB}</b></span><span class="gy">● SURV. <b>${nW}</b></span><span class="gr">● ÉVIT. <b>${nA}</b></span>`;

  // SETUP DU JOUR
  const pick=(sec.top_picks||[])[0], board=d.options_board||[];
  if(pick){const p=pick.plan||{};const call=board.find(c=>c.type==='CALL'&&c.sym===pick.symbol)||board.find(c=>c.type==='CALL');
    q('dHero').innerHTML=`<div class="heroL">
      <div class="hsym">${(pick.grade==='S+'||pick.grade==='S')?'🔥 ':''}${pick.symbol} <span class="grade ${gcls(pick.grade)}">${pick.grade}</span></div>
      <div class="muted" style="margin:5px 0 12px">$${pick.price} ${chg(pick.change)} · <span class="badge ${bcls(pick.verdict)}">${vfr(pick.verdict)}</span> · ${pick.sigcount}/7 signaux · RVOL ${rv(pick.rvol)}</div>
      <div class="hplan"><span>ENTRÉE <b>$${p.entry??'—'}</b></span><span class="dn">STOP <b>$${p.stop??'—'}</b></span><span class="up">TP1 $${p.tp1??'—'}</span><span class="up">TP2 $${p.tp2??'—'}</span><span class="up">TP3 $${p.tp3??'—'}</span><span>R:R <b>${p.rr??'—'}</b></span></div></div>
      <div class="heroR">${call?`<div class="muted" style="font-size:11px;letter-spacing:1px;margin-bottom:6px">💎 CALL ASSOCIÉ</div>
        <div class="hsym" style="font-size:19px">$${call.strike} <span class="muted" style="font-size:12px">${call.exp}</span></div>
        <div class="muted" style="margin-top:6px">Δ ${call.delta} · IV ${call.iv}% · <span style="color:${clr(call.suit)};font-weight:700">${call.grade}</span></div>
        <div style="margin-top:8px;font-size:21px;font-weight:800">$${(call.cost||0).toLocaleString('fr-FR')}</div>
        <div class="muted">breakeven $${call.be}</div>`:'<span class="muted">pas de call propre aujourd\'hui</span>'}</div>`;
  } else q('dHero').innerHTML='<div style="padding:22px" class="muted">Aucun BUY aujourd\'hui — marché en repli. Patience, on ne force pas.</div>';

  // CHANGES
  const ch=sec.changes||[];
  q('dChanges').innerHTML=ch.length?ch.map(c=>`<span class="chip ${c.kind}" onclick="go('${c.symbol}')"><b>${c.symbol}</b> ${c.txt}</span>`).join(''):`<span class="muted">${dy.meta&&dy.meta.has_prev?'rien de neuf depuis hier ✓':'baseline en constitution — reviens demain pour le diff'}</span>`;

  // TOP PICKS
  const tp=sec.top_picks||[];q('dPicksCnt').textContent=tp.length+' SETUPS';
  q('dPicks').innerHTML=tp.map(r=>{const p=r.plan||{};return `<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${(r.grade==='S+'||r.grade==='S')?'<span class="pep">🔥</span> ':''}${r.symbol}</span><div class="spk">${spk(r.symbol)}</div></td>
    <td>$${r.price} ${chg(r.change)}</td><td><span class="sc" style="color:${clr(r.score)}">${r.score}</span></td>
    <td><span class="grade ${gcls(r.grade)}">${r.grade}</span></td><td><span class="badge ${bcls(r.verdict)}">${vfr(r.verdict)}</span></td>
    <td class="muted">${r.sigcount}/7</td><td>${rv(r.rvol)}</td>
    <td class="sub"><span class="dn">$${p.stop??'—'}</span> · <span class="up">$${p.tp2??'—'}</span></td></tr>`}).join('')||er(8,"aucun BUY aujourd'hui — marché en repli");

  // SWING
  const sw=sec.swing_trades||[];q('dSwingCnt').textContent=sw.length+' IDÉES';
  q('dSwing').innerHTML=sw.map(r=>{const p=r.plan||{},rk=(p.entry&&p.stop)?(p.entry-p.stop).toFixed(2):'—';return `<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${r.symbol}</span><div class="sub">$${r.price} ${chg(r.change)}</div><div class="spk">${spk(r.symbol)}</div></td>
    <td><span class="grade ${gcls(r.grade)}">${r.grade}</span> <span class="sc" style="color:${clr(r.score)}">${r.score}</span></td>
    <td>${rv(r.rvol)}</td><td>$${p.entry??'—'}</td><td class="dn">$${p.stop??'—'}</td>
    <td><span class="rb rb-${(r.risk_band||'Med').toLowerCase()}">${r.risk_band||'—'}</span></td>
    <td class="up sub">$${p.tp1??'—'} · $${p.tp2??'—'} · $${p.tp3??'—'}</td><td><span class="pep">${p.rr??'—'}R</span></td></tr>`}).join('')||er(8,"pas de setup swing net (score≥65 + au-dessus MM50)");

  // LIVE MOMENTUM
  q('dMom').innerHTML=(sec.live_momentum||[]).map(r=>`<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${r.symbol}</span></td><td class="up">+${r.change}%</td>
    <td class="${r.roc>=0?'up':'dn'}">${r.roc>=0?'+':''}${r.roc}%</td><td>${Math.round(r.rsi)}</td><td>${Math.round(r.rs)}</td><td>${rv(r.rvol)}</td></tr>`).join('')||er(6,"rien en hausse aujourd'hui");

  // OPTIONS DU JOUR (court/moyen/long)
  const calls=(d.options_board||[]).filter(c=>c.type==='CALL').slice(0,12);
  q('dOptCnt').textContent=calls.length+' CONTRATS';
  q('dOpt').innerHTML=calls.length?calls.map(c=>`<tr onclick="go('${c.sym}')">
    <td><span class="sym">${c.sym}</span></td><td>${bkpill(c.bucket)}</td><td class="sub">${c.exp?c.exp.slice(8,10)+'/'+c.exp.slice(5,7):''} <span class="muted">${c.dte}j</span></td>
    <td>$${c.strike}</td>
    <td style="font-weight:800;color:${(c.quality||0)>=78?'#22C55E':(c.quality||0)>=62?'#F5B45B':'#EF4444'}">${c.quality!=null?c.quality:'—'}<span style="font-size:9px;color:#777">/100</span></td>
    <td style="font-weight:700;color:${(c.pop||0)>=50?'#22C55E':(c.pop||0)>=38?'#FF8C32':'#EF4444'}">${c.pop}%</td>
    <td style="font-weight:700;color:${c.danger==='Faible'?'#22C55E':c.danger==='Modéré'?'#FF8C32':c.danger==='Élevé'?'#d98a52':'#EF4444'}">${c.danger}</td>
    <td>${c.delta}</td><td>$${(c.cost||0).toLocaleString('fr-FR')}</td>
    <td class="sub">si $${c.tgt} <span class="${c.pot>=0?'up':'dn'}">${c.pot>=0?'+':''}${c.pot}%</span></td>
    <td class="sub">${flagpills(c.flags)}</td></tr>`).join(''):er(11,"chaînes en calcul (~1 min)…");

  // TOP MOVERS
  q('dMovers').innerHTML=(sec.top_movers||[]).map(r=>`<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${r.symbol}</span></td><td>${chg(r.change)}</td>
    <td><span class="sc" style="color:${clr(r.score)}">${r.score}</span></td><td>${rv(r.rvol)}</td><td><span class="badge ${bcls(r.verdict)}">${vfr(r.verdict)}</span></td></tr>`).join('')||er(5,'—');

  // SECOND LEG
  q('dSecond').innerHTML=(sec.second_leg||[]).map(r=>{const p=r.plan||{};return `<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${r.symbol}</span></td><td><span class="sc" style="color:${clr(r.score)}">${r.score}</span> <span class="grade ${gcls(r.grade)}">${r.grade}</span></td>
    <td>${r.ext_atr}×</td><td>${Math.round(r.rsi)}</td><td class="sub">$${p.entry??'—'} / <span class="dn">$${p.stop??'—'}</span></td></tr>`}).join('')||er(5,"pas de reprise nette");

  // GARDE-FOUS
  q('dGuard').innerHTML=(sec.guardrails||[]).map(r=>`<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${r.symbol}</span></td><td><span class="grade ${gcls(r.grade)}">${r.grade}</span></td>
    <td>${r.rsi}</td><td>${r.ext_atr}×</td><td class="sub" style="color:#FFB23F">${(r.flags||[]).join(' · ')}</td></tr>`).join('')||er(5,"rien de surchauffé ✓");

  // À ÉVITER
  q('dAvoid').innerHTML=(sec.avoid||[]).map(r=>`<tr onclick="go('${r.symbol}')">
    <td><span class="sym">${r.symbol}</span></td><td><span class="sc" style="color:${clr(r.score)}">${r.score}</span></td>
    <td><span class="grade ${gcls(r.grade)}">${r.grade}</span></td><td>${Math.round(r.rsi)}</td><td class="sub dn">${r.reason}</td></tr>`).join('')||er(5,"aucun titre à éviter");

  renderSectors(d); renderAnomalies(d); renderCharts(d);
  window.__lastD=d; renderPositions(d); renderHero2(d); renderRisk(d); renderIndices(d); renderInternals(d); renderActionDuJour(d); renderPlan(d); renderCapitalPlan(d); renderDecBoard(d); renderRecs(d); renderMovers(d);
}
function renderInternals(d){
  const el=document.getElementById('dInternals');if(!el)return;
  const b=(d.market_ctx||{}).breadth||{};
  const r=(lab,a,bb,ca,cb)=>`<div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #1a1a1a;font-size:12px"><span class="muted">${lab}</span><span><b class="${ca}">${a}</b> <span class="muted">/</span> <b class="${cb}">${bb}</b></span></div>`;
  el.innerHTML=r('Avance / Déclin',(b.adv||0),(b.dec||0),'up','dn')
   +r('Nouveaux hauts / bas 52s',(b.nh||0),(b.nl||0),'up','dn')
   +`<div style="display:flex;justify-content:space-between;padding:7px 0;font-size:12px"><span class="muted">Au-dessus MM50 / MM200</span><span><b>${b.above50!=null?b.above50:'—'}%</b> <span class="muted">/</span> <b>${b.above200!=null?b.above200:'—'}%</b></span></div>`;
}
function renderIndices(d){
  const el=document.getElementById('dIndices');if(!el)return;
  const ix=d.indices||[];
  el.innerHTML=ix.length?ix.map(i=>{const pos=i.vix?(i.change<=0):(i.change>=0);return `<div class="idx"><div class="in">${i.name}</div><div class="ip">${(i.price||0).toLocaleString('fr-FR')}</div><div class="ic ${pos?'up':'dn'}">${i.change>=0?'▲ +':'▼ '}${i.change}%</div></div>`}).join(''):'<span class="muted">indices en cours…</span>';
}
function gauge(val,label,sub,danger){
  const r=32,c=2*Math.PI*r,pct=Math.max(0,Math.min(100,val||0)),off=c*(1-pct/100);
  const col=danger?(pct>=66?'#EF4444':pct>=33?'#FF8C32':'#22C55E'):(pct>=66?'#22C55E':pct>=33?'#FF8C32':'#EF4444');
  return `<div class="gauge"><svg width="86" height="86" viewBox="0 0 86 86">
    <circle cx="43" cy="43" r="${r}" fill="none" stroke="#1a1a1a" stroke-width="7"/>
    <circle cx="43" cy="43" r="${r}" fill="none" stroke="${col}" stroke-width="7" stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}" transform="rotate(-90 43 43)" style="filter:drop-shadow(0 0 4px ${col})"/>
    <text x="43" y="49" text-anchor="middle" fill="#f4f4f4" font-size="19" font-weight="800">${Math.round(pct)}</text></svg>
    <div class="glabel">${label}</div><div class="gsub">${sub}</div></div>`;
}
function renderRisk(d){
  const el=document.getElementById('dRisk');if(!el)return;
  const mc=d.market_ctx||{},b=mc.breadth||{},rows=d.rows||[],DET=d.detail||{},secs=d.sectors||[];
  const vix=mc.vix||16,volRisk=Math.max(0,Math.min(100,(vix-10)/25*100));
  const part=b.above50!=null?b.above50:50,N=rows.length||1;
  const overheat=Math.round(100*rows.filter(r=>{const x=DET[r.symbol]||{};return (x.ext_atr||0)>=4||(x.rsi||0)>=78}).length/N);
  const totBuy=secs.reduce((s,x)=>s+(x.n_buy||0),0)||1;
  const conc=Math.round(100*Math.max(0,...secs.map(x=>x.n_buy||0))/totBuy);
  const advPct=Math.round(100*(b.adv||0)/N);
  const regHealth=mc.spy_regime==='TREND'?85:mc.spy_regime==='CHOP'?25:55;
  el.innerHTML=gauge(volRisk,'Volatilité','VIX '+vix,true)
   +gauge(part,'Participation','% > MM50',false)
   +gauge(overheat,'Surchauffe','% surextendus',true)
   +gauge(conc,'Concentration','1er secteur',true)
   +gauge(advPct,'Ampleur hausse','% en hausse',false)
   +gauge(regHealth,'Santé régime',mc.spy_regime||'—',false);
}
function renderHero2(d){
  const el=document.getElementById('dHero2');if(!el)return;
  const rows=d.rows||[],secs=d.sectors||[],sec=(d.daily||{}).sections||{};
  const pep=rows.filter(r=>(r.grade==='S+'||r.grade==='S')&&r.verdict==='BUY').length;
  const buys=rows.filter(r=>r.verdict==='BUY').length,watch=rows.filter(r=>r.verdict==='WATCH').length;
  const guard=(sec.guardrails||[]).length,ts=secs[0];
  const card=(ic,lab,val,sub)=>`<div class="kcard"><div class="kicon">${ic}</div><div style="min-width:0"><div class="klabel">${lab}</div><div class="kval">${val}</div><div class="ksub">${sub}</div></div></div>`;
  el.innerHTML=
    card('🔥','PÉPITES DU JOUR',pep,'setups S+/S en ACHAT')
   +card('🎯','SIGNAUX ACHAT',buys,`${watch} à surveiller · ${rows.length} scannés`)
   +card('🏆','MEILLEUR SECTEUR',ts?ts.sector:'—',ts?`score ${ts.avg_score} · leader ${ts.leader.symbol}`:'—')
   +card('⚠️','GARDE-FOUS',guard,'titres surchauffés à éviter');
}
function loadPos(){try{return JSON.parse(localStorage.getItem('elio_pos')||'[]')}catch(e){return []}}
function savePos(a){localStorage.setItem('elio_pos',JSON.stringify(a))}
function addPos(){
  const inp=document.getElementById('posIn'),v=(inp.value||'').trim();if(!v)return;
  const m=v.match(/^([A-Za-z.]{1,6})[ ]+([0-9]+(?:[.,][0-9]+)?)(?:[ ]*[x*][ ]*([0-9]+))?$/);
  if(!m){inp.value='';inp.placeholder='format attendu : TICKER PRIX [xQTÉ] — ex : AAPL 195.50 x10';return}
  const a=loadPos();a.push({sym:m[1].toUpperCase(),entry:parseFloat(m[2].replace(',','.')),qty:m[3]?parseInt(m[3]):0});
  savePos(a);inp.value='';renderPositions(window.__lastD||{});
}
function delPos(i){const a=loadPos();a.splice(i,1);savePos(a);renderPositions(window.__lastD||{});}
function valu(pe,med){if(!pe||!med)return null;const r=pe/med;return r>=1.3?{l:'cher (premium)',t:'dn'}:r<=0.75?{l:'décoté',t:'up'}:{l:'dans la moyenne',t:'muted'}}
function peerRank(d,sym){const secs=(d&&d.sectors)||[];for(const s of secs){const idx=(s.members||[]).findIndex(m=>m.symbol===sym);if(idx>=0)return {rank:idx+1,n:s.members.length,sector:s.sector}}return null}
function posNarr(p,x,f,med,pr){
  const px=x.price,pl=x.plan||{},pnl=(px/p.entry-1)*100;
  const reg=x.regime==='TREND'?'tendance solide':x.regime==='CHOP'?'marché agité (range)':'momentum neutre';
  let base;
  if(pl.tp2&&px>=pl.tp2)base=`🎯 <b>TP2 atteint ($${pl.tp2})</b> — encaisse une partie, sécurise.`;
  else if(pl.tp1&&px>=pl.tp1)base=`✅ <b>TP1 franchi</b> — prochain objectif TP2 $${pl.tp2}.`;
  else if(pl.stop&&px<=pl.stop)base=`🛑 <b>STOP touché ($${pl.stop})</b> — sortie discipline.`;
  else if(pnl>=0)base=`🟢 +${pnl.toFixed(1)}% · ${reg} · RSI ${Math.round(x.rsi||0)}. Stop $${pl.stop}, vise TP1 $${pl.tp1}.`;
  else base=`🔴 ${pnl.toFixed(1)}% · ${reg} · RSI ${Math.round(x.rsi||0)}. Surveille le stop $${pl.stop}.`;
  const v=valu(f&&f.pe,med),ex=[];
  if(v)ex.push(`valorisation <b class="${v.t}">${v.l}</b> (P/E ${f.pe.toFixed(0)} vs ${med} secteur)`);
  if(pr)ex.push(`${pr.rank}ᵉ/${pr.n} de son secteur (${pr.sector})`);
  if(x.regime==='CHOP')ex.push('⚠ marché en range — cassures fragiles');
  return base+(ex.length?`<div class="muted" style="font-size:11px;margin-top:4px">${ex.join(' · ')}</div>`:'');
}
function renderPositions(d){
  const a=loadPos(),DET=(d&&d.detail)||{},F=(d&&d.fundamentals)||{},FS=F.by_sym||{},FSEC=F.by_sector||{},el=document.getElementById('posBody');if(!el)return;
  if(!a.length){el.innerHTML='<span class="muted" style="font-size:12px">Tape un titre + ton prix d\'entrée pour un suivi live narré — ex : <b>AAPL 195.50 x10</b>. Stocké sur ton navigateur.</span>';return}
  el.innerHTML='<div style="overflow:auto"><table><thead><tr><th class="l">Titre</th><th>Entrée</th><th>Prix</th><th>P&L %</th><th>P&L $</th><th>P/E · SECT.</th><th>SCORE</th><th class="l">Ce qui se passe (live)</th><th></th></tr></thead><tbody>'+a.map((p,i)=>{
    const x=DET[p.sym]||{},px=x.price,f=FS[p.sym]||{},med=(FSEC[f.sector]||{}).median_pe,pr=peerRank(d,p.sym);
    if(px==null)return `<tr><td class="l sym">${p.sym}</td><td>$${p.entry}</td><td colspan="6" class="muted">hors univers scanné (57 leaders) — prix non suivi</td><td><span onclick="delPos(${i})" style="cursor:pointer;color:#8794ab">✕</span></td></tr>`;
    const pnl=(px/p.entry-1)*100, pnld=p.qty?(px-p.entry)*p.qty:null, v=valu(f.pe,med);
    return `<tr><td class="l sym">${p.sym}</td><td>$${p.entry}</td><td>$${px}</td>
      <td class="${pnl>=0?'up':'dn'}" style="font-weight:800">${pnl>=0?'+':''}${pnl.toFixed(1)}%</td>
      <td class="${pnl>=0?'up':'dn'}">${pnld!=null?(pnld>=0?'+':'')+'$'+Math.round(pnld).toLocaleString('fr-FR'):'—'}</td>
      <td>${f.pe?f.pe.toFixed(0):'—'} <span class="muted">/ ${med||'—'}</span>${v?` <span class="${v.t}" style="font-size:10px">${v.l.split(' ')[0]}</span>`:''}</td>
      <td><span class="sc" style="color:${clr(x.score||0)}">${x.score!=null?x.score:'—'}</span></td>
      <td class="l" style="font-size:12px;line-height:1.5">${posNarr(p,x,f,med,pr)}</td>
      <td><span onclick="delPos(${i})" style="cursor:pointer;color:#8794ab">✕</span></td></tr>`;
  }).join('')+'</tbody></table></div>';
}
window.addPos=addPos;window.delPos=delPos;
function renderToday(cal,news){
  const cat=document.getElementById('dCatalysts');
  if(cat){const it=(cal.items||[]).slice(0,7);
    cat.innerHTML=it.length?it.map(x=>{const soon=x.dte!=null&&x.dte<7,dd=x.date?x.date.slice(8,10)+'/'+x.date.slice(5,7):'';return `<div onclick="go('${x.sym}')" style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #1a1a1a;cursor:pointer;font-size:12.5px"><span><b>${x.sym}</b> <span class="muted">${dd}</span></span><span class="${soon?'dn':'muted'}" style="${soon?'font-weight:800':''}">${soon?'🔴 ':''}${x.dte<=0?'auj.':'J-'+x.dte}</span></div>`}).join(''):'<span class="muted">aucun résultat imminent</span>';}
  const nw=document.getElementById('dNews2');
  if(nw){const it=(news.items||[]).slice(0,6);
    nw.innerHTML=it.length?it.map(n=>`<div onclick="${n.link?`window.open('${n.link}','_blank')`:''}" style="padding:8px 0;border-bottom:1px solid #1a1a1a;cursor:pointer;font-size:12px;line-height:1.45"><span style="color:#F5B45B;font-weight:700">${n.sym}</span> ${n.fr||n.title} <span class="muted" style="font-size:10px">${n.time||''}</span></div>`).join(''):'<span class="muted">collecte des news en cours…</span>';}
}
function renderWeekly(w){
  const el=document.getElementById('dWeekly');if(!el)return;
  const picks=((w&&w.data)||{}).picks||[];
  if(!picks.length){el.innerHTML='<span class="muted" style="font-size:12px">aucun setup ne passe les filtres cette semaine — marché en range, mieux vaut attendre.</span>';return}
  el.innerHTML=picks.map(p=>{const lv=p.levels||{},op=p.option||{},od=op.exp?op.exp.slice(8,10)+'/'+op.exp.slice(5,7):'';return `<div class="seccard" onclick="go('${p.symbol}')">
    <div class="sh" style="color:#FF8C32">${p.icon||'⭐'} ${p.symbol}<span class="grade ${gcls(p.grade)}" style="margin-left:auto">${p.grade}</span></div>
    <div class="muted" style="font-size:10px;margin:5px 0">${p.sector||''} · score ${p.score} · confiance ${p.confidence}</div>
    <div style="font-size:11px;line-height:1.45;margin-bottom:7px">${(p.why||'').slice(0,100)}</div>
    <div style="display:flex;gap:9px;font-size:11px;flex-wrap:wrap"><span>entrée <b>$${lv.entry}</b></span><span class="dn">stop $${lv.stop}</span><span class="up">TP2 $${lv.tp2}</span></div>
    ${op&&op.strike?`<div style="margin-top:7px;font-size:11px;color:#F5B45B">💎 ${(op.bucket||'').toUpperCase()} $${op.strike}${od?' · '+od:''}${op.pop!=null?' · POP '+op.pop+'%':''}</div>`:''}
  </div>`}).join('');
}
function manageJS(d,entry){
  if(!d)return null;
  const price=d.price,pnl=entry?((price-entry)/entry*100):0;
  const score=d.score||0,sg=d.signals||{},a50=sg.above50,a200=sg.above200;
  const stop=(d.plan||{}).stop,bear=d.rsi_div==='bear';
  const broken=(!a200)||(score<40)||(bear&&!a50);
  let action,tone;
  if(pnl>=100){action='ALLÉGER 25-50% · laisser courir le reste';tone='win';}
  else if(pnl>=50&&(!a50||score<55)){action='ALLÉGER · le momentum faiblit';tone='win';}
  else if(pnl>=30){action='REMONTER LE STOP · sécuriser le gain';tone='win';}
  else if(broken){action='SORTIR · thèse cassée (sous tendance)';tone='exit';}
  else if(stop&&price<=stop*1.01){action='SORTIR · stop touché';tone='exit';}
  else if(pnl<=-8&&!a50){action='SORTIR · perte + sous la MM50';tone='exit';}
  else if(score>=68&&a50&&pnl>=0){action='RENFORCER possible · tendance intacte';tone='add';}
  else{action='CONSERVER · surveiller le stop';tone='hold';}
  return {action,tone,pnl:Math.round(pnl*10)/10,broken};
}
async function renderActionDuJour(d){
  const el=document.getElementById('dStar');if(!el)return;
  const recs=d.recommendations||[];
  const cand=recs.filter(r=>r.tone==='buy'||r.tone==='pullback');
  const top=cand.sort((a,b)=>((b.timing==='BUY_NOW')-(a.timing==='BUY_NOW'))||(b.score40-a.score40))[0]||recs[0];
  if(!top){el.innerHTML='<div class="scard" style="padding:16px"><span class="muted" style="font-size:12px">Aucun achat franc aujourd hui — le moteur IBKR préfère attendre un meilleur contexte.</span></div>';return}
  const nc=nivCol(top.niveau),dc=decCol(top.tone);
  const lq=(window.__live||{})[top.symbol];
  const px=lq?lq.last:top.price, chg=(lq&&lq.change!=null)?lq.change:top.change;
  const sd=(d.detail||{})[top.symbol]||{}, pl=sd.plan||{};
  const lvT=(l,v,c)=>`<div style="flex:1 1 0;min-width:88px;background:#0c0c0c;border:1px solid #18181f;border-radius:10px;padding:9px 8px;text-align:center"><div style="font-size:8px;letter-spacing:.6px;text-transform:uppercase;color:#6b7689">${l}</div><div style="font-size:14px;font-weight:800;margin-top:2px;color:${c}">${v}</div></div>`;
  const rr=(pl.entry&&pl.stop&&pl.tp2&&(pl.entry-pl.stop)>0)?((pl.tp2-pl.entry)/(pl.entry-pl.stop)).toFixed(1):null;
  const lvls=pl.entry?`<div style="display:flex;gap:8px;flex-wrap:wrap;padding:0 18px 16px">${lvT('Entrée','$'+pl.entry,'#F5B45B')}${lvT('Stop','$'+pl.stop,'#EF4444')}${lvT('Cible 1','$'+pl.tp1,'#22C55E')}${lvT('Cible 2','$'+pl.tp2,'#22C55E')}${pl.resistance?lvT('Résist.','$'+pl.resistance,'#38BDF8'):''}${rr?lvT('Ratio R:R',rr+':1','#cfd8e6'):''}</div>`:'';
  el.innerHTML=`<div class="scard" onclick="go('${top.symbol}')" style="cursor:pointer;border:1.5px solid ${nc}66;background:linear-gradient(135deg,${nc}12,#0d0d0d)"><div style="padding:18px 18px 12px;display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start">
    <div style="min-width:150px">
      <div style="font-size:30px;font-weight:800;letter-spacing:.5px">${top.symbol}</div>
      <div style="font-size:16px;font-weight:700;margin-top:2px">$${px} <span class="${chg>=0?'up':'dn'}" style="font-size:13px">${chg>=0?'+':''}${chg}%</span></div>
      <div class="muted" style="font-size:11px;margin-top:3px">${top.sector||''} · ${top.grade}</div>
      <div style="margin-top:11px;display:flex;gap:9px;align-items:center"><span style="font-size:13px;font-weight:800;color:${nc};padding:2px 11px;border-radius:8px;border:1.5px solid ${nc};background:${nc}18">${top.niveau}</span><span style="font-size:23px;font-weight:800;color:${nc}">${top.score40}<span style="font-size:12px;color:#888">/40</span></span></div>
    </div>
    <div style="flex:1;min-width:230px">
      <div style="font-size:15px;font-weight:800;color:${dc};margin-bottom:6px">${top.decision} · ${timLbl(top.timing)}</div>
      <div style="font-size:12.5px;line-height:1.5;margin-bottom:9px">${top.raison||''}</div>
      <div style="font-size:12px;line-height:1.6"><b style="color:${dc}">→ Action :</b> ${top.action||''}</div>
      <div class="muted" style="font-size:11px;margin-top:7px">Allocation max : <b style="color:${nc}">${top.alloc}</b></div>
    </div>
    <div id="dStarOpt" style="flex:1;min-width:225px;border-left:1px solid #ffffff10;padding-left:18px"><span class="muted" style="font-size:12px">chargement de l option…</span></div>
  </div>${lvls}</div>`;
  if(window.__starSym!==top.symbol){
    window.__starSym=top.symbol;
    try{window.__starOpt=await(await fetch('/options/'+top.symbol)).json();}catch(e){window.__starOpt=null;}
  }
  const o=window.__starOpt,so=document.getElementById('dStarOpt');
  if(so&&o&&o.best_pick){const bp=o.best_pick,sc=o.scenarios,qc=bp.quality>=78?'#22C55E':bp.quality>=62?'#F5B45B':'#EF4444';
    so.innerHTML=`<div style="font-size:10px;color:#F5B45B;font-weight:800;letter-spacing:1px;margin-bottom:6px">💎 OPTION RECOMMANDÉE</div>
      <div style="font-size:13px;font-weight:700">${(bp.bucket||'').toUpperCase()} · ${eud3(bp.exp)} · $${bp.strike}</div>
      <div class="muted" style="font-size:11px;margin:4px 0 9px">coût $${(bp.cost||0).toLocaleString('fr-FR')} · POP ${bp.pop}% · qualité <b style="color:${qc}">${bp.quality}/100</b></div>
      ${sc?`<div style="display:flex;gap:7px;font-size:12px;font-weight:700"><span style="color:#EF4444">${(sc.pess&&sc.pess.pnl!=null)?sc.pess.pnl+'%':'—'}</span><span class="muted">/</span><span style="color:#F5B45B">${(sc.prob&&sc.prob.pnl!=null)?'+'+sc.prob.pnl+'%':'—'}</span><span class="muted">/</span><span style="color:#22C55E">${(sc.exalt&&sc.exalt.pnl!=null)?'+'+sc.exalt.pnl+'%':'—'}</span></div><div class="muted" style="font-size:9.5px;margin-top:2px">scénarios pessimiste / probable / exceptionnel</div>`:''}`;
  }else if(so){so.innerHTML='<span class="muted" style="font-size:11px">option indisponible (hors séance)</span>';}
}
function renderCapitalPlan(d){
  const el=document.getElementById('dCapital');if(!el)return;
  const recs=d.recommendations||[],mc=d.market_ctx||{},regime=mc.spy_regime,roro=mc.roro;
  let maxPos,sizeMode,permTone,permTxt;
  if(regime==='TREND'&&roro!=='RISK-OFF'){maxPos=3;sizeMode='taille normale';permTone='#22C55E';permTxt='Marché porteur — déploiement autorisé';}
  else if(regime==='CHOP'||roro==='RISK-OFF'){maxPos=1;sizeMode='taille réduite ≈50%';permTone='#EF4444';permTxt='Marché risqué — prudence, garder du cash';}
  else{maxPos=2;sizeMode='taille modérée ≈70%';permTone='#FFB23F';permTxt='Marché neutre — déploiement sélectif';}
  const buys=recs.filter(r=>r.tone==='buy'||r.tone==='pullback').sort((a,b)=>((b.timing==='BUY_NOW')-(a.timing==='BUY_NOW'))||b.score40-a.score40);
  const priority=buys.slice(0,maxPos),reserve=buys.slice(maxPos,maxPos+4);
  const ib=window.__ibkr||{},cashTxt=ib.connected?`Cash IBKR : <b style="color:#34D399">${(ib.cash||0).toLocaleString('fr-FR')} ${ib.currency||'USD'}</b> · `:'';
  const prow=(r,i)=>{const nc=nivCol(r.niveau);return `<div onclick="go('${r.symbol}')" style="display:flex;align-items:center;gap:12px;padding:11px 0;border-bottom:1px solid #161616;cursor:pointer">
    <div style="font-size:20px;font-weight:800;color:${permTone};min-width:28px;text-align:center">${i+1}</div>
    <div style="min-width:66px"><b style="font-size:14px">${r.symbol}</b> <span style="font-size:9px;font-weight:800;color:${nc};padding:1px 6px;border:1px solid ${nc}55;border-radius:5px">${r.niveau}</span></div>
    <div style="flex:1;min-width:150px"><div style="font-size:12px;font-weight:700;color:${decCol(r.tone)}">${r.decision} · ${timLbl(r.timing)}</div><div class="muted" style="font-size:10.5px;line-height:1.4">${r.sector||''} · ${(r.raison||'').slice(0,70)}</div></div>
    <div style="text-align:right"><div style="font-size:15px;font-weight:800;color:${nc}">${r.score40}/40</div><div class="muted" style="font-size:10px">alloc ${r.alloc}</div></div></div>`;};
  el.innerHTML=`<div class="scard" style="border-color:${permTone}44">
    <div style="padding:14px 18px;background:linear-gradient(90deg,${permTone}14,transparent);border-bottom:1px solid #1a1a22;display:flex;align-items:center;gap:14px;flex-wrap:wrap">
      <div><div style="font-size:10px;letter-spacing:1px;color:#8794ab;font-weight:700">🛂 PERMISSION DU JOUR</div><div style="font-size:14px;font-weight:800;color:${permTone}">${permTxt}</div></div>
      <div style="margin-left:auto;display:flex;gap:24px;flex-wrap:wrap;text-align:center">
        <div><div class="muted" style="font-size:9px;text-transform:uppercase;letter-spacing:.5px">Max positions</div><div style="font-size:21px;font-weight:800;color:${permTone}">${maxPos}</div></div>
        <div><div class="muted" style="font-size:9px;text-transform:uppercase;letter-spacing:.5px">Taille conseillée</div><div style="font-size:13px;font-weight:800;margin-top:5px">${sizeMode}</div></div>
      </div></div>
    <div style="padding:8px 18px 14px">
      ${priority.length?`<div style="font-size:9.5px;letter-spacing:.5px;text-transform:uppercase;margin:9px 0 2px;color:${permTone};font-weight:700">À engager aujourd hui · par ordre de priorité</div>${priority.map(prow).join('')}`:'<div class="muted" style="font-size:12px;padding:12px 0">Aucun achat franc aujourd hui — le moteur recommande d attendre. Garder 100% cash.</div>'}
      ${reserve.length?`<div class="muted" style="font-size:9.5px;letter-spacing:.5px;text-transform:uppercase;margin:15px 0 4px">En réserve · surveiller, pas encore</div>${reserve.map(r=>`<div onclick="go('${r.symbol}')" style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #141414;cursor:pointer;font-size:12px"><span><b>${r.symbol}</b> <span style="color:${nivCol(r.niveau)}">${r.niveau}</span></span><span class="muted">${timLbl(r.timing)} · ${r.score40}/40</span></div>`).join('')}`:''}
      <div class="muted" style="font-size:11px;margin-top:13px;padding-top:10px;border-top:1px solid #ffffff10">${cashTxt}Ne jamais tout déployer d un coup — garder de la marge pour les replis.</div>
    </div></div>`;
}
function renderPlan(d){
  const el=document.getElementById('dPlan');if(!el)return;
  const recs=d.recommendations||[];
  if(!recs.length){el.innerHTML='<span class="muted" style="font-size:12px;padding:6px">analyse en cours…</span>';return}
  const det=d.detail||{};
  const buy=recs.filter(r=>r.tone==='buy'||r.tone==='pullback').slice(0,6);
  const watch=recs.filter(r=>r.timing==='WATCH_BREAKOUT'||(r.tone==='wait'&&r.timing!=='TOO_LATE')).slice(0,6);
  const avoid=recs.filter(r=>r.timing==='TOO_LATE').slice(0,6);
  const ibpos=((window.__ibkr||{}).positions)||[];
  const manual=(typeof loadPos==='function'?(loadPos()||[]):[]);
  const held=[];
  ibpos.forEach(p=>held.push({sym:p.symbol,entry:p.avg_cost,src:'IBKR'}));
  manual.forEach(p=>{if(p.sym&&!held.find(h=>h.sym===p.sym))held.push({sym:p.sym,entry:p.entry,src:'manuel'});});
  const recRow=r=>{const nc=nivCol(r.niveau);return `<div onclick="go('${r.symbol}')" style="display:flex;align-items:center;gap:9px;padding:9px 0;border-bottom:1px solid #161616;cursor:pointer"><b style="min-width:50px;font-size:13.5px">${r.symbol}</b><span style="font-size:9.5px;font-weight:800;color:${nc};padding:1px 7px;border:1px solid ${nc}55;border-radius:6px">${r.niveau}</span><span style="color:${nc};font-weight:800;font-size:14px">${r.score40}<span style="font-size:9px;color:#777">/40</span></span><span class="${r.change>=0?'up':'dn'}" style="margin-left:auto;font-size:11.5px;font-weight:700">${r.change>=0?'+':''}${r.change}%</span></div>`;};
  const posRow=h=>{const dd=det[h.sym];const m=manageJS(dd,h.entry);const mc=m?(m.tone==='exit'?'#EF4444':m.tone==='win'?'#22C55E':m.tone==='add'?'#34D399':'#F5B45B'):'#8794ab';return `<div onclick="go('${h.sym}')" style="padding:9px 0;border-bottom:1px solid #161616;cursor:pointer"><div style="display:flex;align-items:center;gap:8px"><b style="min-width:50px;font-size:13.5px">${h.sym}</b><span class="muted" style="font-size:9.5px">${h.src}</span><span style="margin-left:auto;font-weight:800;font-size:13px;color:${m&&m.pnl>=0?'#22C55E':'#EF4444'}">${m?(m.pnl>=0?'+':'')+m.pnl+'%':'—'}</span></div><div style="font-size:11px;color:${mc};margin-top:4px;line-height:1.35">${m?m.action:'données en cours…'}</div></div>`;};
  const empty=t=>`<div class="muted" style="font-size:11.5px;padding:10px 0;line-height:1.5">${t}</div>`;
  const col=(ic,tt,c,html,n)=>`<div class="scard" style="border-color:${c}44"><div class="shead" style="color:${c};font-size:12px;letter-spacing:.8px">${ic} ${tt}<span style="margin-left:auto;font-size:12px;font-weight:800;opacity:.7">${n}</span></div><div style="padding:6px 16px 12px">${html}</div></div>`;
  const heldHtml=held.length?held.map(posRow).join(''):empty('Aucune position détenue (compte IBKR en cash). Dès que tu ouvres une position, elle apparaît ici avec son verdict CONSERVER / RENFORCER / ALLÉGER / SORTIR.');
  el.innerHTML=col('🟢','À ACHETER','#22C55E',buy.length?buy.map(recRow).join(''):empty('Aucun achat franc aujourd hui — marché prudent, garder du cash.'),buy.length)
    +col('🔵','À SURVEILLER','#38BDF8',watch.length?watch.map(recRow).join(''):empty('Rien à guetter de net pour l instant.'),watch.length)
    +col('🟡','MES POSITIONS À GÉRER','#F5B45B',heldHtml,held.length)
    +col('🔴','NE PAS POURSUIVRE','#EF4444',avoid.length?avoid.map(recRow).join(''):empty('Aucun titre en surchauffe — pas de piège de chase.'),avoid.length);
}
function nivCol(n){return n==='S+'?'#22C55E':n==='S'?'#34D399':n==='A'?'#F5B45B':n==='B'?'#FF8C32':'#EF4444'}
function decCol(t){return t==='buy'?'#22C55E':t==='pullback'?'#34D399':t==='wait'?'#7FB3FF':'#EF4444'}
function timLbl(s){return s==='BUY_NOW'?'✅ achat propre':s==='BUY_PULLBACK'?'⏳ sur repli':s==='WATCH_BREAKOUT'?'👀 sur cassure':s==='TOO_LATE'?'🛑 trop étendu':'éviter'}
function recCat(r){
  if(r.tone==='avoid')return 'avoid';
  if(r.timing==='WATCH_BREAKOUT')return 'breakout';
  if(r.tone==='pullback'||r.timing==='BUY_PULLBACK')return 'pullback';
  if(r.tone==='buy'&&r.timing==='BUY_NOW')return 'buynow';
  return 'watch';
}
function multiDonut(segs){
  const total=segs.reduce((s,x)=>s+x.n,0)||1, r=30, c=2*Math.PI*r; let off=0;
  const arcs=segs.filter(s=>s.n>0).map(s=>{const len=s.n/total*c;const el=`<circle cx="40" cy="40" r="${r}" fill="none" stroke="${s.color}" stroke-width="9" stroke-dasharray="${len.toFixed(1)} ${(c-len).toFixed(1)}" stroke-dashoffset="${(-off).toFixed(1)}" transform="rotate(-90 40 40)"/>`;off+=len;return el;}).join('');
  return `<svg viewBox="0 0 80 80" style="width:86px;height:86px;flex-shrink:0"><circle cx="40" cy="40" r="${r}" fill="none" stroke="#1a1a22" stroke-width="9"/>${arcs}<text x="40" y="38" text-anchor="middle" font-size="18" font-weight="800" fill="#fff">${total}</text><text x="40" y="51" text-anchor="middle" font-size="7.5" fill="#8794ab">setups</text></svg>`;
}
function renderDecBoard(d){
  const el=q('dDecBoard'); if(!el) return;
  const recs=(d.recommendations||[]).filter(r=>r.score40>=18);
  const cats=[['buynow','Acheter maintenant','#22C55E'],['pullback','Attendre repli','#34D399'],['breakout','Attendre cassure','#7FB3FF'],['watch','Surveiller','#FFB23F'],['avoid','Éviter','#EF4444']];
  const by={}; cats.forEach(c=>by[c[0]]=[]);
  recs.forEach(r=>{(by[recCat(r)]=by[recCat(r)]||[]).push(r);});
  if(!recs.length){el.innerHTML='<div class="card" style="padding:16px"><span class="muted" style="font-size:12px">Aucune décision IBKR ≥18/40 — données en cours ou marché prudent.</span></div>';return;}
  const segs=cats.map(c=>({n:(by[c[0]]||[]).length,color:c[2]}));
  const legend=cats.map(c=>{const list=(by[c[0]]||[]).sort((a,b)=>b.score40-a.score40);const top=list.slice(0,5).map(r=>r.symbol).join(' · ');return `<div style="display:flex;align-items:center;gap:9px;padding:5px 0;border-bottom:1px solid #ffffff08"><span style="width:9px;height:9px;border-radius:2px;background:${c[2]};flex-shrink:0"></span><span style="font-size:11.5px;font-weight:700;color:${c[2]};min-width:138px">${c[1]}</span><b style="font-size:14px;min-width:22px">${list.length}</b><span class="muted" style="font-size:10.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${top||'—'}</span></div>`;}).join('');
  el.innerHTML=`<div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap;background:linear-gradient(165deg,#16171c,#0d0e12);border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:16px 18px">${multiDonut(segs)}<div style="flex:1;min-width:250px">${legend}</div></div>`;
}
function renderRecs(d){
  const el=document.getElementById('dRecs');if(!el)return;
  const recs=(d.recommendations||[]).filter(r=>r.score40>=22).slice(0,8);
  if(!recs.length){el.innerHTML='<span class="muted" style="font-size:12px;padding:4px">Aucun titre ne passe le seuil IBKR (≥22/40) aujourd hui — marché prudent, garder du cash.</span>';return}
  el.innerHTML=recs.map(r=>{const nc=nivCol(r.niveau),dc=decCol(r.tone);return `<div class="seccard" onclick="go('${r.symbol}')" style="border-color:${nc}44">
    <div class="sh" style="color:${nc}">${r.symbol} <span style="font-size:10px;font-weight:800;padding:1px 7px;border-radius:6px;background:${nc}22;border:1px solid ${nc}66">${r.niveau}</span><span style="margin-left:auto;font-size:19px;font-weight:800;color:${nc}">${r.score40}<span style="font-size:10px;color:#888">/40</span></span></div>
    <div style="font-size:11.5px;margin:8px 0 5px;font-weight:700;color:${dc}">${r.decision} · ${timLbl(r.timing)}</div>
    <div class="muted" style="font-size:10.5px">${r.sector||''} · ${r.grade} · <span class="${r.change>=0?'up':'dn'}">${r.change>=0?'+':''}${r.change}%</span> · alloc ${r.alloc}</div>
    <div style="font-size:11px;margin-top:6px;line-height:1.45">${r.raison||''}</div></div>`}).join('');
}
function renderMovers(d){
  const rows=(d.rows||[]).filter(r=>typeof r.change==='number');
  const ms=document.getElementById('dMoversSess');
  if(ms){const m=d.market||{},si=sessInfo(m),sess=m.session;ms.innerHTML=`<span style="color:${si[1]}">· ${sess==='pre'?'mouvements AVANT-BOURSE (live)':sess==='open'?'séance en cours (live)':sess==='after'?'mouvements APRÈS-BOURSE (live)':'dernière séance'}</span>`;}
  const g=document.getElementById('dGainers'),l=document.getElementById('dLosers');
  const ln=r=>`<div onclick="go('${r.symbol}')" style="display:flex;justify-content:space-between;align-items:center;padding:7px 2px;border-bottom:1px solid #1a1a1a;cursor:pointer;font-size:12.5px"><span><b style="color:#F5B45B">${r.symbol}</b> <span class="muted" style="font-size:11px">$${r.price}</span></span><span class="${r.change>=0?'up':'dn'}" style="font-weight:800">${r.change>=0?'+':''}${r.change}%</span></div>`;
  if(g){const up=[...rows].sort((a,b)=>b.change-a.change).slice(0,7);g.innerHTML=up.map(ln).join('')||'<span class="muted">—</span>';}
  if(l){const dn=[...rows].sort((a,b)=>a.change-b.change).slice(0,7);l.innerHTML=dn.map(ln).join('')||'<span class="muted">—</span>';}
}
async function renderIbkrDash(){
  const el=document.getElementById('dIbkrCard');if(!el)return;
  el.innerHTML=`<div class="scard" style="border-color:#34D39944"><div class="shead" style="color:#34D399"><span class="ico">🔌</span> COMPTE IBKR <span class="muted" style="margin-left:auto;font-size:11px">connexion à TWS…</span></div></div>`;
  try{
    const d=await(await fetch('/ibkr')).json();window.__ibkr=d;
    if(!d.connected){el.innerHTML=`<div class="scard" style="border-color:#5b667844"><div class="shead" style="color:#8794ab"><span class="ico">🔌</span> COMPTE IBKR <span style="margin-left:auto;cursor:pointer;color:#7FB3FF;font-size:11px" onclick="renderIbkrDash()">⟳ reconnecter</span></div><div style="padding:14px;font-size:12px" class="muted">Non connecté — ${d.error||'lance TWS + active l API'}.</div></div>`;return}
    const cur=d.currency||'USD',f=n=>n==null?'—':n.toLocaleString('fr-FR');
    const kp=(l,v,c)=>`<div style="flex:1;min-width:118px"><div class="muted" style="font-size:9.5px;letter-spacing:.5px;text-transform:uppercase">${l}</div><div style="font-size:18px;font-weight:800;margin-top:2px;color:${c||'#e6edf7'}">${v}</div></div>`;
    const pos=d.positions||[];
    const posHtml=pos.length?pos.map(p=>`<div onclick="go('${p.symbol}')" style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #1a1a1a;cursor:pointer;font-size:12.5px"><span><b style="color:#F5B45B">${p.symbol}</b> <span class="muted">${p.sectype}</span></span><span>${p.qty} @ $${p.avg_cost}</span></div>`).join(''):'<div class="muted" style="font-size:12px;padding:6px 0">aucune position ouverte — compte en cash</div>';
    el.innerHTML=`<div class="scard" style="border-color:#34D39944"><div class="shead" style="color:#34D399"><span class="ico">🔌</span> COMPTE IBKR · ${d.account||''} <span style="font-size:10px;color:#34D399;border:1px solid #34D39955;border-radius:5px;padding:1px 6px;margin-left:4px">${d.mode}</span><span style="font-size:10px;color:#EF4444;border:1px solid #EF444455;border-radius:5px;padding:1px 6px;margin-left:4px">LECTURE SEULE</span><span style="margin-left:auto;cursor:pointer;color:#7FB3FF;font-size:12px" onclick="renderIbkrDash()">⟳</span></div>
      <div style="padding:14px"><div style="display:flex;gap:18px;flex-wrap:wrap;margin-bottom:10px">${kp('Valeur nette',f(d.net_liq)+' '+cur,'#34D399')}${kp('Cash',f(d.cash)+' '+cur)}${kp('Pouvoir d achat',f(d.buying_power)+' '+cur)}${kp('P&L latent',((d.upnl||0)>=0?'+':'')+f(d.upnl)+' '+cur,(d.upnl||0)>=0?'#22C55E':'#EF4444')}</div>
      <div class="muted" style="font-size:9.5px;letter-spacing:.5px;text-transform:uppercase;margin:8px 0 2px">Positions réelles (${pos.length})</div>${posHtml}</div></div>`;
  }catch(e){el.innerHTML=`<div class="scard" style="padding:14px"><span class="dn">erreur de lecture IBKR</span></div>`;}
}
window.renderIbkrDash=renderIbkrDash;
function eud3(s){return s?s.slice(8,10)+'/'+s.slice(5,7)+'/'+s.slice(0,4):s}
async function loadDetail(sym){
  const el=document.getElementById('dDetail');if(!el)return;
  el.style.display='block';el.innerHTML='<div class="scard" style="padding:20px"><span class="muted">chargement '+sym+'…</span></div>';
  el.scrollIntoView({behavior:'smooth',block:'start'});
  try{
    const o=await(await fetch('/options/'+sym)).json();
    const d=((window.__lastD||{}).detail||{})[sym]||{};
    const dec=o.decision,dc=dec?(dec.tone==='strong'?'#22C55E':dec.tone==='buy'?'#4ade80':dec.tone==='watch'?'#FF8C32':dec.tone==='wait'?'#FFB23F':'#EF4444'):'#888';
    const lv=d.plan||{},bp=o.best_pick;
    const decCard=dec?`<div style="background:linear-gradient(135deg,${dc}14,#0d0d0d);border:1px solid ${dc}55;border-radius:14px;padding:16px 18px;margin-bottom:14px"><div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap"><div style="font-size:23px;font-weight:800;color:${dc};text-shadow:0 0 14px ${dc}55">${dec.decision}</div><div style="flex:1;min-width:130px"><div class="muted" style="font-size:10px;letter-spacing:1px">CONVICTION</div><div style="height:8px;background:#1a1a1a;border-radius:5px;margin-top:5px;overflow:hidden"><div style="height:100%;width:${dec.conviction}%;background:${dc};box-shadow:0 0 8px ${dc}"></div></div></div><div style="font-size:21px;font-weight:800;color:${dc}">${dec.conviction}<span style="font-size:12px;color:#888">/100</span></div></div><div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:14px"><div><div style="font-size:10px;letter-spacing:1px;color:#22C55E;font-weight:700;margin-bottom:6px">✓ FORCES</div>${(dec.pros||[]).map(p=>`<div style="font-size:12px;margin:4px 0"><span class="up">✓</span> ${p}</div>`).join('')}</div><div><div style="font-size:10px;letter-spacing:1px;color:#EF4444;font-weight:700;margin-bottom:6px">⚠ RISQUES</div>${(dec.cons||[]).map(c=>`<div style="font-size:12px;margin:4px 0"><span class="dn">✗</span> ${c}</div>`).join('')||'<div class="muted" style="font-size:12px">aucun risque majeur</div>'}</div></div><div style="margin-top:13px;padding-top:12px;border-top:1px solid #ffffff10;font-size:13px"><b style="color:${dc}">→ Action :</b> ${dec.action}</div></div>`:'';
    const ik=o.ibkr, ic=ik?ik.color:'#888';
    const comp=ik?Object.entries(ik.components).map(([k,v])=>`<div style="margin:5px 0"><div style="display:flex;justify-content:space-between;font-size:10.5px;margin-bottom:2px"><span class="muted">${k}</span><b>${v[0]}/${v[1]}</b></div><div style="height:5px;background:#1a1a1a;border-radius:3px;overflow:hidden"><div style="height:100%;width:${Math.round(v[0]/v[1]*100)}%;background:${ic}"></div></div></div>`).join(''):'';
    const tm=ik?ik.timing:null;
    const ncb=ik&&ik.no_chase&&ik.no_chase.length?`<div style="margin-top:10px;background:rgba(239,68,68,.08);border:1px solid #EF444444;border-radius:8px;padding:8px 11px"><div style="font-size:10px;color:#F87171;font-weight:700;letter-spacing:.5px;margin-bottom:3px">🛑 NO-CHASE</div>${ik.no_chase.map(r=>`<div style="font-size:11px;margin:2px 0">• ${r}</div>`).join('')}</div>`:'';
    const ibkrCard=ik?`<div style="background:linear-gradient(135deg,${ic}18,#0d0d0d);border:1.5px solid ${ic}66;border-radius:16px;padding:18px;margin-bottom:14px">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px">
        <span style="font-size:13px;font-weight:800;color:${ic};letter-spacing:1px">🔱 VERDICT IBKR</span>
        <span style="font-size:18px;font-weight:800;color:${ic};text-shadow:0 0 12px ${ic}66;padding:1px 11px;border-radius:9px;border:1.5px solid ${ic};background:${ic}15">${ik.niveau}</span>
        <span style="font-size:25px;font-weight:800;color:${ic}">${ik.score40}<span style="font-size:13px;color:#888">/40</span></span>
        <span style="margin-left:auto;font-size:17px;font-weight:800;color:${ic}">${ik.decision}</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">
        <div><div style="font-size:9.5px;color:${ic};font-weight:700;letter-spacing:.5px;margin-bottom:5px">SCORE IBKR /40</div>${comp}</div>
        <div>
          <div style="font-size:9.5px;color:${ic};font-weight:700;letter-spacing:.5px;margin-bottom:5px">⏱️ TIMING — ${timLbl(tm.state)}</div>
          <div style="font-size:11.5px;line-height:1.7">
            <div>Entrée optimale : <b class="up">$${tm.optimal}</b></div>
            <div>Entrée agressive (cassure) : <b>$${tm.aggressive}</b></div>
            <div>Invalidation : <b class="dn">$${tm.invalidation}</b></div>
            <div style="margin-top:4px">Allocation max : <b style="color:${ic}">${ik.alloc}</b></div>
          </div>${ncb}
        </div>
      </div>
      <div style="margin-top:13px;padding-top:12px;border-top:1px solid #ffffff12;font-size:12.5px;line-height:1.55"><b style="color:${ic}">Raison :</b> ${ik.raison}<br><b style="color:${ic}">→ Action :</b> ${ik.action}</div>
    </div>`:'';
    const k=(l,v)=>`<div class="kpi" style="margin:0;padding:11px 13px"><div style="font-size:9.5px;letter-spacing:.5px;text-transform:uppercase;color:#8a8a8a">${l}</div><div style="font-size:16px;font-weight:800;margin-top:3px">${v}</div></div>`;
    const kpis=`<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:10px;margin-bottom:14px">${k('SCORE',`<span style="color:${clr(d.score||0)}">${d.score!=null?d.score:'—'} ${d.grade||''}</span>`)}${k('RÉGIME',d.regime==='TREND'?'TENDANCE':d.regime==='CHOP'?'RANGE':'NEUTRE')}${k('RSI · RS',`${Math.round(d.rsi||0)} · ${Math.round(d.rs||0)}`)}${k('QUALITÉ SETUP',(d.setup_quality!=null?d.setup_quality:'—')+'/100')}${k('P/E · SECT.',(o.pe?o.pe.toFixed(0):'—')+' / '+(o.sector_median_pe||'—'))}${k('PLAN',`$${lv.entry} <span class="dn">$${lv.stop}</span> <span class="up">$${lv.tp2}</span>`)}</div>`;
    const sc=o.scenarios,be=o.breakeven,em=o.expected_move;
    const qparts=bp&&bp.quality_parts?Object.entries(bp.quality_parts):[];
    const qcol=bp?(bp.quality>=78?'#22C55E':bp.quality>=62?'#F5B45B':'#EF4444'):'#888';
    const scRow=(lbl,s,c)=>(s&&s.pnl!=null)?`<div style="text-align:center;flex:1"><div style="font-size:9.5px;color:#8794ab;text-transform:uppercase;letter-spacing:.5px">${lbl}</div><div style="font-size:19px;font-weight:800;color:${c}">${s.pnl>=0?'+':''}${s.pnl}%</div><div class="muted" style="font-size:10px">si $${s.px}</div></div>`:'';
    const bvc=be?(be.verdict==='réaliste'?'#22C55E':be.verdict==='agressif'?'#F5B45B':'#EF4444'):'#888';
    const opt=bp?`<div style="background:rgba(245,180,91,.05);border:1px solid #F5B45B44;border-radius:12px;padding:15px 16px">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:9px"><span style="font-size:11px;color:#F5B45B;font-weight:800;letter-spacing:1px">💎 OPTIONS DESK</span><span style="font-size:13.5px;font-weight:700">${(bp.bucket||'').toUpperCase()} · ${eud3(bp.exp)} · strike $${bp.strike}</span><span style="margin-left:auto;font-size:10px;color:#8794ab">QUALITÉ <b style="color:${qcol};font-size:17px">${bp.quality}</b>/100</span></div>
      <div class="muted" style="font-size:11.5px;margin-bottom:11px;line-height:1.5">coût $${(bp.cost||0).toLocaleString('fr-FR')} · delta ${bp.delta} · POP ${bp.pop}% · danger ${bp.danger} · OI ${(bp.oi||0).toLocaleString('fr-FR')} · vol ${bp.vol} · spread ${bp.spread!=null?bp.spread+'%':'—'} · IV ${bp.iv}%</div>
      ${sc?`<div style="display:flex;gap:6px;background:#0c0c0c;border-radius:9px;padding:11px 6px;margin-bottom:6px">${scRow('🔴 Pessimiste',sc.pess,'#EF4444')}${scRow('🟡 Probable',sc.prob,'#F5B45B')}${scRow('🟢 Exceptionnel',sc.exalt,'#22C55E')}</div><div class="muted" style="font-size:10px;text-align:center;margin-bottom:10px">scénarios à ~${sc.horizon}j · stop → TP1 → TP3 (érosion théta incluse)</div>`:''}
      <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:11.5px;margin-bottom:9px">
        ${be?`<div>🎯 Breakeven <b>$${be.be}</b> (${be.dist>=0?'+':''}${be.dist}%) · ${be.monthly}%/mois → <span style="color:${bvc};font-weight:700">${be.verdict}</span></div>`:''}
        ${em?`<div class="muted">📐 Expected move ±${em.pct}% → $${em.lo}–$${em.hi}</div>`:''}
      </div>
      <div style="display:flex;gap:5px;flex-wrap:wrap">${qparts.map(([k,v])=>`<span style="font-size:9px;padding:2px 7px;border-radius:5px;background:#161616;color:${v[0]/v[1]>=0.7?'#34D399':v[0]/v[1]>=0.4?'#8794ab':'#F87171'}">${k} ${v[0]}/${v[1]}</span>`).join('')}</div>
    </div>`:'';
    const hasChart=d.series&&d.series.close&&d.series.close.length;
    const chartBox=hasChart?`<div class="muted" style="font-size:9.5px;letter-spacing:.5px;text-transform:uppercase;margin:6px 0 4px">Cours · 120 jours <span style="color:#F5B45B">— MM20</span> <span style="color:#6b7689">— MM50</span></div><div style="height:210px;margin-bottom:14px"><canvas id="dchart"></canvas></div>`:'';
    el.innerHTML=`<div class="scard" style="border-color:${ic}55"><div class="shead" style="color:${ic}"><span class="ico">📈</span> ${sym} <span class="muted" style="font-weight:400;font-size:12px">${o.name||''}</span><span style="margin-left:auto;cursor:pointer;color:#888" onclick="document.getElementById('dDetail').style.display='none'">✕ fermer</span></div><div style="padding:16px">${ibkrCard}${decCard}${kpis}${chartBox}${opt}<div class="muted" style="font-size:11.5px;margin-top:12px;line-height:1.5">🔬 ${o.chart_read||''}</div></div></div>`;
    if(hasChart&&typeof Chart!=='undefined'){const cv=document.getElementById('dchart');if(cv){if(window.__dchart)window.__dchart.destroy();const g=cv.getContext('2d').createLinearGradient(0,0,0,210);g.addColorStop(0,'rgba(255,140,50,.28)');g.addColorStop(1,'rgba(255,140,50,0)');window.__dchart=new Chart(cv,{type:'line',data:{labels:d.series.dates,datasets:[{data:d.series.close,borderColor:'#FF8C32',backgroundColor:g,fill:true,borderWidth:2,pointRadius:0,tension:.18},{data:d.series.ema20,borderColor:'#F5B45B',borderWidth:1.1,pointRadius:0,tension:.1,borderDash:[4,3]},{data:d.series.sma50,borderColor:'#6b7689',borderWidth:1,pointRadius:0,tension:.1}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#6b6b6b',maxTicksLimit:7,font:{size:9}},grid:{color:'#141414'}},y:{ticks:{color:'#6b6b6b',font:{size:9}},grid:{color:'#141414'}}}}});}}
  }catch(e){el.innerHTML='<div class="scard" style="padding:20px"><span class="dn">erreur de chargement de '+sym+'</span></div>';}
}
window.go=function(s){location.href='/titre/'+s};window.loadDetail=loadDetail;
function overlayLive(d){
  const lq=window.__live||{};if(!d||!Object.keys(lq).length)return d;
  (d.rows||[]).forEach(r=>{const x=lq[r.symbol];if(x){r.price=x.last;r.change=x.change;r.live=true;}});
  const det=d.detail||{};for(const s in lq){if(det[s]){det[s].price=lq[s].last;det[s].change=lq[s].change;det[s].live=true;}}
  (d.recommendations||[]).forEach(r=>{const x=lq[r.symbol];if(x){r.price=x.last;r.change=x.change;r.live=true;}});
  return d;
}
function sessInfo(m){
  const s=m.session||(m.open?'open':'closed');
  return {pre:['🌅 AVANT-BOURSE','#FFB23F'],open:['🟢 SÉANCE OUVERTE','#22C55E'],after:['🌙 APRÈS-BOURSE','#A78BFA'],closed:['🌑 MARCHÉ FERMÉ','#8794ab']}[s]||['🌑 MARCHÉ FERMÉ','#8794ab'];
}
function updateLiveBanner(){
  const lv=q('dLive');if(!lv)return;
  const meta=window.__liveMeta||{},n=Object.keys(window.__live||{}).length,m=(window.__lastD||{}).market||{};
  const si=sessInfo(m),sess=m.session||'closed';
  const tradable=sess==='pre'||sess==='open'||sess==='after';
  const dot=`<span class="${tradable?'lpulse':''}">●</span>`;
  if(meta.connected&&n>0){
    lv.innerHTML=`<span style="color:${si[1]};font-weight:800">${dot} ${si[0]}</span> · <b style="color:${meta.rt?'#22C55E':'#8794ab'}">${meta.rt?'TEMPS RÉEL IBKR':'différé'}</b> · ${n} cours ${tradable?'live':'(dernier)'} · ${m.et||''}`;
  }else{
    lv.innerHTML=`<span style="color:${si[1]};font-weight:800">${dot} ${si[0]}</span> · ${m.et||'—'} · cours yfinance différé ~15min · MAJ ${(window.__lastD||{}).updated||'—'}`;
  }
}
async function liveTick(){try{
  const r=await(await fetch('/quotes')).json();window.__live=r.quotes||{};window.__liveMeta=r.meta||{};
  const d=window.__lastD;
  if(d&&Object.keys(window.__live).length){overlayLive(d);renderActionDuJour(d);renderPlan(d);renderMovers(d);renderRecs(d);if(typeof renderPositions==='function')renderPositions(d);}
  updateLiveBanner();
}catch(e){}}
async function dailyTick(){try{
  const [d,cal,news,weekly,quotes]=await Promise.all([
    fetch('/scan').then(r=>r.json()),
    fetch('/cal-feed').then(r=>r.json()).catch(()=>({})),
    fetch('/news-feed').then(r=>r.json()).catch(()=>({})),
    fetch('/weekly-feed').then(r=>r.json()).catch(()=>({})),
    fetch('/quotes').then(r=>r.json()).catch(()=>({}))]);
  window.__live=(quotes&&quotes.quotes)||window.__live||{};window.__liveMeta=(quotes&&quotes.meta)||window.__liveMeta||{};
  overlayLive(d);
  renderDaily(d);renderToday(cal,news);renderWeekly(weekly);
  const t=q('dTick');if(t){t.classList.remove('flash');void t.offsetWidth;t.classList.add('flash');}
}catch(e){}}
setInterval(dailyTick,15000);dailyTick();renderIbkrDash();setInterval(renderIbkrDash,60000);
setInterval(liveTick,7000);
const _qs=new URLSearchParams(location.search).get('sym');if(_qs)setTimeout(()=>{try{loadDetail(_qs.toUpperCase());}catch(e){}},1600);
</script></body></html>"""


NAV = ('<div class="nav"><a href="/" class="navb">📈 Terminal</a>'
       '<a href="/daily" class="navb">📅 Daily Watchlist</a>'
       '<a href="/sectors" class="navb active">🔥 Secteurs</a>'
       '<span class="navtick" id="navTick"></span></div>')

PAGE_SECTORS = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Trading Desk"><meta name="mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0b0e14"><link rel="apple-touch-icon" href="/static/icon-180.png"><link rel="manifest" href="/manifest.webmanifest"><title>TRADING DESK · SECTEURS</title>
<style>
*{box-sizing:border-box}html{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
body{margin:0;background:#070707;color:#eaf0fa;font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
.muted{color:#8794ab}.up{color:#FF8C32}.dn{color:#EF4444}
.nav{display:flex;align-items:center;gap:6px;padding:11px 24px;background:#0a0a0a;border-bottom:1px solid #ffffff10}
.navb{color:#8794ab;text-decoration:none;font-weight:700;font-size:12.5px;letter-spacing:.5px;padding:7px 14px;border-radius:9px;transition:.15s}
.navb:hover{color:#eaf0fa;background:#ffffff08}
.navb.active{color:#FF8C32;background:rgba(255,140,50,.08);box-shadow:inset 0 0 0 1px #FF8C3233}
.navtick{margin-left:auto;width:7px;height:7px;border-radius:50%;background:#FF8C32}
.navtick.flash{animation:tk .5s}@keyframes tk{0%{transform:scale(1.7);box-shadow:0 0 12px #FF8C32}100%{transform:scale(1)}}
.wrap{max-width:1500px;margin:0 auto;padding:0 22px 60px}
.head{padding:24px 4px 10px;border-bottom:1px solid #ffffff12;margin-bottom:18px}
.head h1{margin:0;font-size:30px;font-weight:800;letter-spacing:3px;color:#FF8C32}
.head h1 span{color:#F5B45B}
.head .sub{color:#8794ab;font-size:12px;letter-spacing:1px;margin-top:6px}
.heat{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));margin-bottom:26px}
.tile{background:#111111;border:1px solid #ffffff12;border-radius:14px;padding:14px 16px;cursor:pointer;transition:.15s}
.tile:hover{border-color:#ffffff26;transform:translateY(-2px)}
.tile .th{display:flex;align-items:center;gap:8px;font-weight:800;font-size:14px}
.tile .big{margin-left:auto;font-size:26px;font-weight:800}
.tile .dlt{font-size:11px;font-weight:700;margin-left:5px}
.tile .mini{display:flex;gap:12px;font-size:11px;color:#8794ab;margin-top:9px;flex-wrap:wrap}
.tile .segbar{display:flex;height:7px;border-radius:5px;overflow:hidden;margin-top:10px;background:#1a1b22}
.secblock{margin-bottom:24px}
.sech{font-size:15px;font-weight:800;letter-spacing:1px;margin:0 2px 9px;display:flex;align-items:center;gap:9px}
.sech .leadtag{font-size:11px;font-weight:700;color:#8794ab}
table{width:100%;border-collapse:collapse;background:#111111;border:1px solid #ffffff12;border-radius:12px;overflow:hidden}
thead th{font-size:10.5px;letter-spacing:.6px;color:#8794ab;text-align:right;font-weight:700;padding:10px 14px;border-bottom:1px solid #ffffff12}
thead th:first-child,thead th:nth-child(2){text-align:left}
tbody td{padding:10px 14px;text-align:right;border-bottom:1px solid #ffffff08;font-size:13px}
tbody td:first-child,tbody td:nth-child(2){text-align:left}
tbody tr:last-child td{border-bottom:none}tbody tr:hover{background:#ffffff06;cursor:pointer}
.sym{font-weight:800}.sc{font-weight:800}
.badge{font-size:10px;font-weight:800;padding:2px 7px;border-radius:6px}
.b-achat{background:rgba(255,140,50,.10);color:#FF8C32}.b-evit{background:rgba(255,43,78,.10);color:#EF4444}.b-surv{background:rgba(255,234,0,.10);color:#FFB23F}
.grade{display:inline-block;min-width:24px;text-align:center;font-weight:800;font-size:11px;padding:2px 6px;border-radius:6px}
.g-sp{background:rgba(245,180,91,.12);color:#F5B45B}.g-a{background:rgba(255,140,50,.10);color:#FF8C32}.g-b{background:rgba(255,234,0,.10);color:#FFB23F}.g-c{background:rgba(255,43,92,.10);color:#EF4444}
.rvol{font-weight:700}.rvol.hot{color:#FFB23F}.rvol.warm{color:#FF8C32}.rvol.cold{color:#8794ab}
.spk{display:flex}svg.spark{overflow:visible}
.foot{margin-top:30px;text-align:center;color:#8794ab;font-size:10.5px;letter-spacing:.6px;border-top:1px solid #ffffff10;padding-top:18px}
</style><style id="m-fix">
html,body{overflow-x:hidden;max-width:100%}
@media(max-width:640px){
  .grid,.cols,.herorow,.hero,.panorama,.layout,.heat,.secgrid,.poster{grid-template-columns:1fr!important}
  [style*="grid-template-columns"]{grid-template-columns:1fr!important}
  .top3{grid-template-columns:1fr 1fr 1fr!important}
  .plan{grid-template-columns:1fr 1fr!important}
  td,th{padding-left:9px!important;padding-right:9px!important}
  table{font-size:11.5px!important}
  .wrap,.daily{padding-left:12px!important;padding-right:12px!important}
  .phead-top{padding:15px 16px!important}
  .htitle{font-size:25px!important}
  .hmeta{margin-left:0!important;text-align:left!important}
  .back{padding:5px 9px!important;font-size:11px!important}
}
</style>
<style id="nav-css">
#gnav{position:sticky;top:0;z-index:1000;background:rgba(10,10,12,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid #1c1c24}
.gnav-in{max-width:1500px;margin:0 auto;display:flex;align-items:center;gap:12px;padding:8px 16px;flex-wrap:wrap}
.gnav-links{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
#gnav a{color:#aab4c4;text-decoration:none;font-size:13px;font-weight:700;padding:7px 12px;border-radius:9px;white-space:nowrap}
#gnav a:hover{background:#ffffff0d;color:#fff}
#gnav a.act{background:rgba(245,180,91,.14);color:#F5B45B}
.gnav-search{display:flex;gap:6px;align-items:center}
.gnav-search input{background:#0e0e0e;border:1px solid #1c1c24;color:#e8edf5;border-radius:9px;padding:7px 11px;font-size:13px;width:210px;max-width:46vw}
.gnav-search button{background:rgba(245,180,91,.12);border:1px solid #F5B45B55;color:#F5B45B;border-radius:9px;padding:7px 13px;font-weight:800;cursor:pointer}
.back{display:none!important}
@media(max-width:640px){.gnav-in{padding:6px 10px;gap:8px}.gnav-links{order:2;overflow-x:auto;flex-wrap:nowrap}#gnav a{font-size:12px;padding:6px 9px}.gnav-search{order:1;width:100%}.gnav-search input{flex:1;max-width:none;width:auto}}
/* === systeme de design premium (toutes pages) === */
body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;background:radial-gradient(1200px 780px at 50% -8%,#0d0d12,#050506 60%)!important}
.panel,.scard,.card,.pcard,.seccard,.tile,.kpi,.fav,.pchip,.stat{background:linear-gradient(165deg,#16171c,#0d0e12)!important;border:1px solid rgba(255,255,255,.08)!important;border-radius:16px!important}
.panel,.scard,.card,.pcard,.fav,.seccard{transition:border-color .2s ease,box-shadow .2s ease,transform .18s ease!important}
.panel:hover,.scard:hover,.card:hover,.fav:hover,.seccard:hover{border-color:rgba(255,140,50,.32)!important;box-shadow:0 0 26px rgba(255,140,50,.10)!important}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#26262e;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:#3a3a44}
::selection{background:rgba(255,140,50,.28);color:#fff}
/* bandeau KPI (cockpit + autres) */
.kpiband{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin:14px 0}
.kc{background:linear-gradient(165deg,#16171c,#0d0e12);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:11px 12px;min-width:0}
.kc .kt{font-size:8.5px;letter-spacing:.6px;text-transform:uppercase;color:#8794ab;display:flex;align-items:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kv{font-size:17px;font-weight:900;margin-top:3px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .ks{font-size:9px;color:#6b7689;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kbar{height:5px;border-radius:4px;background:#0a0a0a;overflow:hidden;margin-top:6px}
.kc .kbar>div{height:100%;border-radius:4px}
@media(max-width:760px){.kpiband{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:44%;overflow-x:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;padding-bottom:2px}.kc{scroll-snap-align:start}}
/* navigation mobile sticky en bas */
#mnav{display:none}
@media(max-width:760px){
  #mnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:1200;background:rgba(8,8,10,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid rgba(255,255,255,.09);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
  #mnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;text-decoration:none;color:#8794ab;font-size:9.5px;font-weight:700;padding:5px 2px;border-radius:12px}
  #mnav a .mi{font-size:19px;line-height:1}
  #mnav a.act{color:#FF8C32;background:rgba(255,140,50,.13)}
  body{padding-bottom:68px!important}
  #gnav .gnav-links{display:none}
}
</style>
<script>
(function(){var L=[['/','🔱 Cockpit'],['/ma-page','⭐ Ma Page'],['/watchlist','📋 Watchlist'],['/entreprises','🏢 Entreprises'],['/options','💎 Options']];
function build(){if(document.getElementById('gnav'))return;var p=location.pathname;
var links=L.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'>'+x[1]+'</a>';}).join('');
var nav=document.createElement('nav');nav.id='gnav';
nav.innerHTML='<div class="gnav-in"><div class="gnav-links">'+links+'</div><form class="gnav-search" onsubmit="return gnavGo(event)"><input id="gnavq" placeholder="Rechercher un titre… (ex: NVDA)" autocomplete="off"><button type="submit">→</button></form></div>';
document.body.insertBefore(nav,document.body.firstChild);
var M=[['/','🏠','Cockpit'],['/watchlist','📡','Scan'],['/ma-page','⭐','Ma Page'],['/options','💎','Options'],['/entreprises','🏢','Titres']];
var mn=document.createElement('nav');mn.id='mnav';
mn.innerHTML=M.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'><span class="mi">'+x[1]+'</span>'+x[2]+'</a>';}).join('');
document.body.appendChild(mn);}
window.gnavGo=function(e){e.preventDefault();var v=(document.getElementById('gnavq').value||'').trim().toUpperCase();if(v)location.href='/titre/'+encodeURIComponent(v);return false;};
if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();})();
</script></head><body>
__NAV__
<div class="wrap">
  <div class="head"><h1>🔥 SECTEURS <span>EN FORCE</span></h1>
    <div class="sub">57 LEADERS US · 9 SECTEURS · CLASSÉS PAR FORCE · ▲/▼ vs hier · clic = fiche · ANALYSE ONLY · yfinance différé ~15min</div></div>
  <div class="heat" id="heat"></div>
  <div id="secdetail"></div>
  <div class="foot">TRADING DESK · les secteurs sont calculés depuis les 57 leaders scannés · aucune donnée inventée</div>
</div>
<script>
function q(i){return document.getElementById(i)}
function clr(s){return s>=72?'#FF8C32':s>=55?'#FFB23F':'#EF4444'}
function gcls(g){return g==='S+'||g==='S'?'g-sp':g==='A'?'g-a':g==='B'?'g-b':'g-c'}
function bcls(v){return v==='BUY'?'b-achat':(v==='WATCH'||v==='WAIT')?'b-surv':'b-evit'}
function vfr(v){return {BUY:'ACHAT',WATCH:'SURVEILLER',WAIT:'ATTENTE',AVOID:'ÉVITER'}[v]||v}
function rv(v){v=v||0;const c=v>=1.5?'hot':v>=1?'warm':'cold';return `<span class="rvol ${c}">${v.toFixed(2)}×</span>`}
function go(s){location.href='/analyse?sym='+s}
let __n=0;
function spark(arr,w=78,h=22,days=24){
  if(!arr||arr.length<2)return '';
  const d=arr.slice(-days).filter(v=>v!=null&&!isNaN(v));if(d.length<2)return '';
  const up=d[d.length-1]>=d[0],col=up?'#FF8C32':'#EF4444',g='ss'+(++__n);
  const mn=Math.min(...d),mx=Math.max(...d),rg=(mx-mn)||1,p=2,iw=w-4,ih=h-4;
  const X=i=>p+(i/(d.length-1))*iw,Y=v=>p+ih-((v-mn)/rg)*ih;
  const pts=d.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)),ln='M'+pts.join(' L');
  const ar=ln+' L'+X(d.length-1).toFixed(1)+','+(h-p)+' L'+X(0).toFixed(1)+','+(h-p)+' Z';
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs><linearGradient id="${g}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".28"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs><path d="${ar}" fill="url(#${g})"/><path d="${ln}" fill="none" stroke="${col}" stroke-width="1.3"/></svg>`;
}
function renderSec(d){
  const secs=d.sectors||[], DET=d.detail||{};
  q('heat').innerHTML=secs.map(s=>{
    const c=clr(s.avg_score),tot=Math.max(s.n,1);
    const dl=s.delta==null?'':(s.delta>=0?`<span class="dlt up">▲+${s.delta}</span>`:`<span class="dlt dn">▼${s.delta}</span>`);
    return `<div class="tile" onclick="document.getElementById('blk_${s.sector.replace(/[^a-zA-Z]/g,'')}').scrollIntoView({behavior:'smooth'})">
      <div class="th">${s.icon} ${s.sector}<span class="big" style="color:${c}">${s.avg_score}</span>${dl}</div>
      <div class="mini"><span>${s.n_buy}/${s.n} ACHAT</span><span class="${s.avg_change>=0?'up':'dn'}">${s.avg_change>=0?'+':''}${s.avg_change}%</span><span>RS ${s.avg_rs}</span><span>RVOL ${s.avg_rvol}×</span></div>
      <div class="segbar"><div style="width:${s.n_buy/tot*100}%;background:#FF8C32"></div><div style="width:${s.n_watch/tot*100}%;background:#FFB23F"></div><div style="width:${s.n_avoid/tot*100}%;background:#EF4444"></div></div>
      <div class="mini" style="margin-top:8px"><span class="up">⭐ ${s.leader.symbol} ${s.leader.score}</span><span class="muted">faible : ${s.laggard.symbol} ${s.laggard.score}</span></div>
    </div>`;
  }).join('')||'<span class="muted">secteurs en calcul…</span>';

  q('secdetail').innerHTML=secs.map(s=>{
    const rows=(s.members||[]).map(m=>{const x=DET[m.symbol]||{};return `<tr onclick="go('${m.symbol}')">
      <td class="sym">${m.symbol}</td>
      <td><div class="spk">${spark((x.series||{}).close)}</div></td>
      <td>$${x.price!=null?x.price:'—'}</td>
      <td class="${(m.change||0)>=0?'up':'dn'}">${(m.change||0)>=0?'+':''}${m.change}%</td>
      <td><span class="sc" style="color:${clr(m.score)}">${m.score}</span></td>
      <td><span class="grade ${gcls(m.grade)}">${m.grade}</span></td>
      <td><span class="badge ${bcls(m.verdict)}">${vfr(m.verdict)}</span></td>
      <td>${Math.round(x.rs||0)}</td><td>${rv(m.rvol)}</td><td>${Math.round(x.rsi||0)}</td></tr>`}).join('');
    return `<div class="secblock" id="blk_${s.sector.replace(/[^a-zA-Z]/g,'')}">
      <div class="sech" style="color:${clr(s.avg_score)}">${s.icon} ${s.sector} <span class="muted" style="font-weight:400">· score moyen ${s.avg_score} · ${s.n_buy}/${s.n} en achat · MM50 ${s.b50}% · MM200 ${s.b200}%</span></div>
      <table><thead><tr><th>TITRE</th><th>30 J</th><th>PRIX</th><th>VAR J</th><th>SCORE</th><th>GRADE</th><th>VERDICT</th><th>RS</th><th>RVOL</th><th>RSI</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }).join('');
}
async function tick(){try{const d=await(await fetch('/scan')).json();renderSec(d);const t=q('navTick');if(t){t.classList.remove('flash');void t.offsetWidth;t.classList.add('flash');}}catch(e){}}
setInterval(tick,15000);tick();
</script></body></html>""".replace('__NAV__', NAV)


# ════════════ PAGES NEWS / OPTIONS / CALENDRIER (style commun) ════════════
_BASE_CSS = """
*{box-sizing:border-box}html{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
body{margin:0;background:#070707;color:#eaf0fa;font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
.muted{color:#8794ab}.up{color:#FF8C32}.dn{color:#EF4444}
.wrap{max-width:1320px;margin:0 auto;padding:22px 24px 60px}
.head{padding:6px 2px 16px;border-bottom:1px solid #ffffff12;margin-bottom:20px;display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:12px}
.head h1{margin:0;font-size:26px;font-weight:800;letter-spacing:2px}
.head .sub{color:#8794ab;font-size:12px;margin-top:6px}
.live{display:inline-flex;align-items:center;gap:8px;font-size:12px;font-weight:700;color:#FF8C32;background:rgba(255,140,50,.1);border:1px solid rgba(255,140,50,.3);border-radius:20px;padding:7px 14px}
.live .d{width:8px;height:8px;border-radius:50%;background:#FF8C32;box-shadow:0 0 8px #FF8C32;animation:pp 1.4s infinite}
@keyframes pp{0%,100%{opacity:1}50%{opacity:.3}}
.card{background:#111111;border:1px solid #ffffff12;border-radius:12px;overflow:hidden}
table{width:100%;border-collapse:collapse}
thead th{font-size:10.5px;letter-spacing:.5px;color:#8794ab;text-align:right;font-weight:700;padding:11px 14px;border-bottom:1px solid #ffffff12}
thead th:first-child{text-align:left}
tbody td{padding:11px 14px;text-align:right;border-bottom:1px solid #ffffff08;font-size:13px}
tbody td:first-child{text-align:left}
tbody tr:last-child td{border-bottom:none}tbody tr:hover{background:#ffffff06;cursor:pointer}
.sym{font-weight:800}.badge{font-size:10px;font-weight:800;padding:2px 7px;border-radius:6px}
.b-achat{background:rgba(255,140,50,.12);color:#FF8C32}.b-evit{background:rgba(226,86,107,.12);color:#EF4444}.b-surv{background:rgba(214,169,63,.14);color:#FFB23F}
.grade{display:inline-block;min-width:24px;text-align:center;font-weight:800;font-size:11px;padding:2px 6px;border-radius:6px}
.g-sp{background:rgba(255,140,50,.16);color:#F5B45B}.g-a{background:rgba(255,140,50,.12);color:#FF8C32}.g-b{background:rgba(214,169,63,.14);color:#FFB23F}.g-c{background:rgba(226,86,107,.12);color:#EF4444}
.bkt{font-size:10px;font-weight:800;padding:2px 7px;border-radius:6px}
.bkt-court{color:#FFB23F;background:rgba(214,169,63,.12)}.bkt-moyen{color:#FFB23F;background:rgba(255,178,63,.12)}.bkt-long{color:#FF8C32;background:rgba(255,140,50,.12)}
.nrow{display:flex;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid #ffffff08;cursor:pointer}
.nrow:hover{background:#ffffff06}.nrow .ntit{flex:1;font-size:13.5px}.nrow .nmeta{font-size:11px;white-space:nowrap}
.flagp{font-size:9px;font-weight:700;padding:1px 6px;border-radius:4px;margin-left:3px;border:1px solid #2a2a33;color:#8794ab}
"""
_BASE_JS = """
function clr(s){return s>=72?'#FF8C32':s>=55?'#FFB23F':'#EF4444'}
function gcls(g){return g==='S+'||g==='S'?'g-sp':g==='A'?'g-a':g==='B'?'g-b':'g-c'}
function bcls(v){return v==='BUY'?'b-achat':(v==='WATCH'||v==='WAIT')?'b-surv':'b-evit'}
function vfr(v){return {BUY:'ACHAT',WATCH:'SURVEILLER',WAIT:'ATTENTE',AVOID:'ÉVITER'}[v]||v}
function eud(s){if(!s)return s;const m=String(s).slice(0,10).match(/^(\d{4})-(\d{2})-(\d{2})$/);return m?m[3]+'/'+m[2]+'/'+m[1]:s}
function dcol(d){return d==='Faible'?'#FF8C32':d==='Modéré'?'#FFB23F':d==='Élevé'?'#d98a52':'#EF4444'}
function go(s){location.href='/analyse?sym='+s}
"""

PAGE_NEWS = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Trading Desk"><meta name="mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0b0e14"><link rel="apple-touch-icon" href="/static/icon-180.png"><link rel="manifest" href="/manifest.webmanifest"><title>TRADING DESK · News</title>
<style>__CSS__</style><style id="m-fix">
html,body{overflow-x:hidden;max-width:100%}
@media(max-width:640px){
  .grid,.cols,.herorow,.hero,.panorama,.layout,.heat,.secgrid,.poster{grid-template-columns:1fr!important}
  [style*="grid-template-columns"]{grid-template-columns:1fr!important}
  .top3{grid-template-columns:1fr 1fr 1fr!important}
  .plan{grid-template-columns:1fr 1fr!important}
  td,th{padding-left:9px!important;padding-right:9px!important}
  table{font-size:11.5px!important}
  .wrap,.daily{padding-left:12px!important;padding-right:12px!important}
  .phead-top{padding:15px 16px!important}
  .htitle{font-size:25px!important}
  .hmeta{margin-left:0!important;text-align:left!important}
  .back{padding:5px 9px!important;font-size:11px!important}
}
</style>
<style id="nav-css">
#gnav{position:sticky;top:0;z-index:1000;background:rgba(10,10,12,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid #1c1c24}
.gnav-in{max-width:1500px;margin:0 auto;display:flex;align-items:center;gap:12px;padding:8px 16px;flex-wrap:wrap}
.gnav-links{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
#gnav a{color:#aab4c4;text-decoration:none;font-size:13px;font-weight:700;padding:7px 12px;border-radius:9px;white-space:nowrap}
#gnav a:hover{background:#ffffff0d;color:#fff}
#gnav a.act{background:rgba(245,180,91,.14);color:#F5B45B}
.gnav-search{display:flex;gap:6px;align-items:center}
.gnav-search input{background:#0e0e0e;border:1px solid #1c1c24;color:#e8edf5;border-radius:9px;padding:7px 11px;font-size:13px;width:210px;max-width:46vw}
.gnav-search button{background:rgba(245,180,91,.12);border:1px solid #F5B45B55;color:#F5B45B;border-radius:9px;padding:7px 13px;font-weight:800;cursor:pointer}
.back{display:none!important}
@media(max-width:640px){.gnav-in{padding:6px 10px;gap:8px}.gnav-links{order:2;overflow-x:auto;flex-wrap:nowrap}#gnav a{font-size:12px;padding:6px 9px}.gnav-search{order:1;width:100%}.gnav-search input{flex:1;max-width:none;width:auto}}
/* === systeme de design premium (toutes pages) === */
body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;background:radial-gradient(1200px 780px at 50% -8%,#0d0d12,#050506 60%)!important}
.panel,.scard,.card,.pcard,.seccard,.tile,.kpi,.fav,.pchip,.stat{background:linear-gradient(165deg,#16171c,#0d0e12)!important;border:1px solid rgba(255,255,255,.08)!important;border-radius:16px!important}
.panel,.scard,.card,.pcard,.fav,.seccard{transition:border-color .2s ease,box-shadow .2s ease,transform .18s ease!important}
.panel:hover,.scard:hover,.card:hover,.fav:hover,.seccard:hover{border-color:rgba(255,140,50,.32)!important;box-shadow:0 0 26px rgba(255,140,50,.10)!important}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#26262e;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:#3a3a44}
::selection{background:rgba(255,140,50,.28);color:#fff}
/* bandeau KPI (cockpit + autres) */
.kpiband{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin:14px 0}
.kc{background:linear-gradient(165deg,#16171c,#0d0e12);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:11px 12px;min-width:0}
.kc .kt{font-size:8.5px;letter-spacing:.6px;text-transform:uppercase;color:#8794ab;display:flex;align-items:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kv{font-size:17px;font-weight:900;margin-top:3px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .ks{font-size:9px;color:#6b7689;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kbar{height:5px;border-radius:4px;background:#0a0a0a;overflow:hidden;margin-top:6px}
.kc .kbar>div{height:100%;border-radius:4px}
@media(max-width:760px){.kpiband{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:44%;overflow-x:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;padding-bottom:2px}.kc{scroll-snap-align:start}}
/* navigation mobile sticky en bas */
#mnav{display:none}
@media(max-width:760px){
  #mnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:1200;background:rgba(8,8,10,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid rgba(255,255,255,.09);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
  #mnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;text-decoration:none;color:#8794ab;font-size:9.5px;font-weight:700;padding:5px 2px;border-radius:12px}
  #mnav a .mi{font-size:19px;line-height:1}
  #mnav a.act{color:#FF8C32;background:rgba(255,140,50,.13)}
  body{padding-bottom:68px!important}
  #gnav .gnav-links{display:none}
}
</style>
<script>
(function(){var L=[['/','🔱 Cockpit'],['/ma-page','⭐ Ma Page'],['/watchlist','📋 Watchlist'],['/entreprises','🏢 Entreprises'],['/options','💎 Options']];
function build(){if(document.getElementById('gnav'))return;var p=location.pathname;
var links=L.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'>'+x[1]+'</a>';}).join('');
var nav=document.createElement('nav');nav.id='gnav';
nav.innerHTML='<div class="gnav-in"><div class="gnav-links">'+links+'</div><form class="gnav-search" onsubmit="return gnavGo(event)"><input id="gnavq" placeholder="Rechercher un titre… (ex: NVDA)" autocomplete="off"><button type="submit">→</button></form></div>';
document.body.insertBefore(nav,document.body.firstChild);
var M=[['/','🏠','Cockpit'],['/watchlist','📡','Scan'],['/ma-page','⭐','Ma Page'],['/options','💎','Options'],['/entreprises','🏢','Titres']];
var mn=document.createElement('nav');mn.id='mnav';
mn.innerHTML=M.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'><span class="mi">'+x[1]+'</span>'+x[2]+'</a>';}).join('');
document.body.appendChild(mn);}
window.gnavGo=function(e){e.preventDefault();var v=(document.getElementById('gnavq').value||'').trim().toUpperCase();if(v)location.href='/titre/'+encodeURIComponent(v);return false;};
if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();})();
</script></head><body>
<div class="wrap">
  <div class="head"><div><h1>📰 NEWS · FLUX MARCHÉ LIVE</h1><div class="sub">Rafraîchi automatiquement chaque minute · traduit en français si la clé IA est active</div></div>
    <span class="live"><span class="d"></span><span id="nfresh">connexion…</span></span></div>
  <div id="nfeed" class="card"></div>
  <p class="muted" style="margin-top:12px;font-size:11px">Sources : yfinance / Yahoo Finance (différé). Clic = ouvre l'article. ANALYSE ONLY.</p>
</div>
<script>__JS__
async function nf(){
  try{const d=await(await fetch('/news-feed')).json();
    document.getElementById('nfresh').textContent='mis à jour à '+(d.updated||'…')+(d.ai_on?' · FR':' · EN');
    const it=d.items||[];
    document.getElementById('nfeed').innerHTML=it.length?it.map(n=>`<div class="nrow" onclick="${n.link?`window.open('${n.link}','_blank')`:''}">
      <span class="badge" style="background:rgba(255,140,50,.16);color:#F5B45B;min-width:46px;text-align:center">${n.sym}</span>
      <span class="ntit">${n.fr||n.title}</span>
      <span class="muted nmeta">${n.pub||''}${n.time?' · '+n.time:''}</span></div>`).join(''):'<div class="muted" style="padding:24px;text-align:center">chargement des news du marché… (1ère collecte ~1 min)</div>';
  }catch(e){}
}
setInterval(nf,20000);nf();
</script></body></html>""".replace('__CSS__', _BASE_CSS).replace('__JS__', _BASE_JS)

PAGE_OPTIONS = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Trading Desk"><meta name="mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0b0e14"><link rel="apple-touch-icon" href="/static/icon-180.png"><link rel="manifest" href="/manifest.webmanifest"><title>TRADING DESK · Options</title>
<style>__CSS__</style><style id="m-fix">
html,body{overflow-x:hidden;max-width:100%}
@media(max-width:640px){
  .grid,.cols,.herorow,.hero,.panorama,.layout,.heat,.secgrid,.poster{grid-template-columns:1fr!important}
  [style*="grid-template-columns"]{grid-template-columns:1fr!important}
  .top3{grid-template-columns:1fr 1fr 1fr!important}
  .plan{grid-template-columns:1fr 1fr!important}
  td,th{padding-left:9px!important;padding-right:9px!important}
  table{font-size:11.5px!important}
  .wrap,.daily{padding-left:12px!important;padding-right:12px!important}
  .phead-top{padding:15px 16px!important}
  .htitle{font-size:25px!important}
  .hmeta{margin-left:0!important;text-align:left!important}
  .back{padding:5px 9px!important;font-size:11px!important}
}
</style>
<style id="nav-css">
#gnav{position:sticky;top:0;z-index:1000;background:rgba(10,10,12,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid #1c1c24}
.gnav-in{max-width:1500px;margin:0 auto;display:flex;align-items:center;gap:12px;padding:8px 16px;flex-wrap:wrap}
.gnav-links{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
#gnav a{color:#aab4c4;text-decoration:none;font-size:13px;font-weight:700;padding:7px 12px;border-radius:9px;white-space:nowrap}
#gnav a:hover{background:#ffffff0d;color:#fff}
#gnav a.act{background:rgba(245,180,91,.14);color:#F5B45B}
.gnav-search{display:flex;gap:6px;align-items:center}
.gnav-search input{background:#0e0e0e;border:1px solid #1c1c24;color:#e8edf5;border-radius:9px;padding:7px 11px;font-size:13px;width:210px;max-width:46vw}
.gnav-search button{background:rgba(245,180,91,.12);border:1px solid #F5B45B55;color:#F5B45B;border-radius:9px;padding:7px 13px;font-weight:800;cursor:pointer}
.back{display:none!important}
@media(max-width:640px){.gnav-in{padding:6px 10px;gap:8px}.gnav-links{order:2;overflow-x:auto;flex-wrap:nowrap}#gnav a{font-size:12px;padding:6px 9px}.gnav-search{order:1;width:100%}.gnav-search input{flex:1;max-width:none;width:auto}}
/* === systeme de design premium (toutes pages) === */
body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;background:radial-gradient(1200px 780px at 50% -8%,#0d0d12,#050506 60%)!important}
.panel,.scard,.card,.pcard,.seccard,.tile,.kpi,.fav,.pchip,.stat{background:linear-gradient(165deg,#16171c,#0d0e12)!important;border:1px solid rgba(255,255,255,.08)!important;border-radius:16px!important}
.panel,.scard,.card,.pcard,.fav,.seccard{transition:border-color .2s ease,box-shadow .2s ease,transform .18s ease!important}
.panel:hover,.scard:hover,.card:hover,.fav:hover,.seccard:hover{border-color:rgba(255,140,50,.32)!important;box-shadow:0 0 26px rgba(255,140,50,.10)!important}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#26262e;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:#3a3a44}
::selection{background:rgba(255,140,50,.28);color:#fff}
/* bandeau KPI (cockpit + autres) */
.kpiband{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin:14px 0}
.kc{background:linear-gradient(165deg,#16171c,#0d0e12);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:11px 12px;min-width:0}
.kc .kt{font-size:8.5px;letter-spacing:.6px;text-transform:uppercase;color:#8794ab;display:flex;align-items:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kv{font-size:17px;font-weight:900;margin-top:3px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .ks{font-size:9px;color:#6b7689;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kbar{height:5px;border-radius:4px;background:#0a0a0a;overflow:hidden;margin-top:6px}
.kc .kbar>div{height:100%;border-radius:4px}
@media(max-width:760px){.kpiband{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:44%;overflow-x:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;padding-bottom:2px}.kc{scroll-snap-align:start}}
/* navigation mobile sticky en bas */
#mnav{display:none}
@media(max-width:760px){
  #mnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:1200;background:rgba(8,8,10,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid rgba(255,255,255,.09);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
  #mnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;text-decoration:none;color:#8794ab;font-size:9.5px;font-weight:700;padding:5px 2px;border-radius:12px}
  #mnav a .mi{font-size:19px;line-height:1}
  #mnav a.act{color:#FF8C32;background:rgba(255,140,50,.13)}
  body{padding-bottom:68px!important}
  #gnav .gnav-links{display:none}
}
</style>
<script>
(function(){var L=[['/','🔱 Cockpit'],['/ma-page','⭐ Ma Page'],['/watchlist','📋 Watchlist'],['/entreprises','🏢 Entreprises'],['/options','💎 Options']];
function build(){if(document.getElementById('gnav'))return;var p=location.pathname;
var links=L.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'>'+x[1]+'</a>';}).join('');
var nav=document.createElement('nav');nav.id='gnav';
nav.innerHTML='<div class="gnav-in"><div class="gnav-links">'+links+'</div><form class="gnav-search" onsubmit="return gnavGo(event)"><input id="gnavq" placeholder="Rechercher un titre… (ex: NVDA)" autocomplete="off"><button type="submit">→</button></form></div>';
document.body.insertBefore(nav,document.body.firstChild);
var M=[['/','🏠','Cockpit'],['/watchlist','📡','Scan'],['/ma-page','⭐','Ma Page'],['/options','💎','Options'],['/entreprises','🏢','Titres']];
var mn=document.createElement('nav');mn.id='mnav';
mn.innerHTML=M.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'><span class="mi">'+x[1]+'</span>'+x[2]+'</a>';}).join('');
document.body.appendChild(mn);}
window.gnavGo=function(e){e.preventDefault();var v=(document.getElementById('gnavq').value||'').trim().toUpperCase();if(v)location.href='/titre/'+encodeURIComponent(v);return false;};
if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();})();
</script></head><body>
<div class="wrap">
  <div class="head"><div><h1>💎 OPTIONS · LES MEILLEURES À TRAVAILLER</h1><div class="sub">Calls sélectionnés selon le marché · court / moyen / long · projection de gain si la cible est atteinte</div></div>
    <span class="live"><span class="d"></span><span id="ofresh">live</span></span></div>
  <div class="card"><table><thead><tr><th>TITRE</th><th>HORIZON</th><th>ÉCHÉANCE</th><th>STRIKE</th><th>SUIT.</th><th>Δ</th><th>PROBA PROFIT</th><th>DANGER</th><th>COÛT</th><th>BID / ASK</th><th>SI CIBLE → GAIN</th><th>ALERTES</th></tr></thead><tbody id="obody"></tbody></table></div>
  <p class="muted" style="margin-top:12px;font-size:11px">« SI CIBLE ATTEINTE » = gain de l'option si le sous-jacent atteint sa cible (TP2 du plan Track) avant l'échéance — possibilité indicative, JAMAIS une promesse. COURT = théta violent (tenu jours/semaines). yfinance différé ~15min · hors séance = IV recalculée (indicatif). ANALYSE ONLY.</p>
</div>
<script>__JS__
function bkpill(b){return `<span class="bkt bkt-${b||'long'}">${(b||'long').toUpperCase()}</span>`}
function fl(a){return (a||[]).map(f=>`<span class="flagp">${f}</span>`).join('')}
function eud(s){if(!s)return s;const m=String(s).slice(0,10).match(/^(\d{4})-(\d{2})-(\d{2})$/);return m?m[3]+'/'+m[2]+'/'+m[1]:s}
function dcol(d){return d==='Faible'?'#FF8C32':d==='Modéré'?'#FFB23F':d==='Élevé'?'#d98a52':'#EF4444'}
async function ro(){
  try{const d=await(await fetch('/scan')).json();
    const b=(d.options_board||[]).filter(c=>c.type==='CALL');
    document.getElementById('ofresh').textContent='maj '+(d.updated||'…');
    document.getElementById('obody').innerHTML=b.length?b.map(c=>`<tr onclick="go('${c.sym}')">
      <td><span class="sym">${c.sym}</span></td><td>${bkpill(c.bucket)}</td>
      <td>${eud(c.exp)} <span class="muted">${c.dte}j</span></td><td>$${c.strike}</td>
      <td><span style="color:${clr(c.suit)};font-weight:800">${c.suit}</span> <span class="grade ${gcls(c.grade)}">${c.grade}</span></td>
      <td>${c.delta}</td>
      <td><span style="font-weight:800;color:${(c.pop||0)>=50?'#FF8C32':(c.pop||0)>=38?'#FFB23F':'#EF4444'}">${c.pop}%</span></td>
      <td><span style="font-weight:700;color:${dcol(c.danger)}">${c.danger}</span></td>
      <td>$${(c.cost||0).toLocaleString('fr-FR')}</td>
      <td class="muted" style="font-size:11px">${(c.bid||c.ask)?(c.bid+' / '+c.ask):'hors séance'}</td>
      <td>si $${c.tgt} → <span class="${c.pot>=0?'up':'dn'}" style="font-weight:800">${c.pot>=0?'+':''}${c.pot}%</span> <span class="muted">(${c.p_tgt}%)</span></td>
      <td>${fl(c.flags)}</td></tr>`).join(''):'<tr><td colspan="12" class="muted" style="text-align:center;padding:24px">board en calcul (~1 min)…</td></tr>';
  }catch(e){}
}
setInterval(ro,15000);ro();
</script></body></html>""".replace('__CSS__', _BASE_CSS).replace('__JS__', _BASE_JS)

PAGE_CALENDAR = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Trading Desk"><meta name="mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0b0e14"><link rel="apple-touch-icon" href="/static/icon-180.png"><link rel="manifest" href="/manifest.webmanifest"><title>TRADING DESK · Calendrier</title>
<style>__CSS__</style><style id="m-fix">
html,body{overflow-x:hidden;max-width:100%}
@media(max-width:640px){
  .grid,.cols,.herorow,.hero,.panorama,.layout,.heat,.secgrid,.poster{grid-template-columns:1fr!important}
  [style*="grid-template-columns"]{grid-template-columns:1fr!important}
  .top3{grid-template-columns:1fr 1fr 1fr!important}
  .plan{grid-template-columns:1fr 1fr!important}
  td,th{padding-left:9px!important;padding-right:9px!important}
  table{font-size:11.5px!important}
  .wrap,.daily{padding-left:12px!important;padding-right:12px!important}
  .phead-top{padding:15px 16px!important}
  .htitle{font-size:25px!important}
  .hmeta{margin-left:0!important;text-align:left!important}
  .back{padding:5px 9px!important;font-size:11px!important}
}
</style>
<style id="nav-css">
#gnav{position:sticky;top:0;z-index:1000;background:rgba(10,10,12,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid #1c1c24}
.gnav-in{max-width:1500px;margin:0 auto;display:flex;align-items:center;gap:12px;padding:8px 16px;flex-wrap:wrap}
.gnav-links{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
#gnav a{color:#aab4c4;text-decoration:none;font-size:13px;font-weight:700;padding:7px 12px;border-radius:9px;white-space:nowrap}
#gnav a:hover{background:#ffffff0d;color:#fff}
#gnav a.act{background:rgba(245,180,91,.14);color:#F5B45B}
.gnav-search{display:flex;gap:6px;align-items:center}
.gnav-search input{background:#0e0e0e;border:1px solid #1c1c24;color:#e8edf5;border-radius:9px;padding:7px 11px;font-size:13px;width:210px;max-width:46vw}
.gnav-search button{background:rgba(245,180,91,.12);border:1px solid #F5B45B55;color:#F5B45B;border-radius:9px;padding:7px 13px;font-weight:800;cursor:pointer}
.back{display:none!important}
@media(max-width:640px){.gnav-in{padding:6px 10px;gap:8px}.gnav-links{order:2;overflow-x:auto;flex-wrap:nowrap}#gnav a{font-size:12px;padding:6px 9px}.gnav-search{order:1;width:100%}.gnav-search input{flex:1;max-width:none;width:auto}}
/* === systeme de design premium (toutes pages) === */
body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;background:radial-gradient(1200px 780px at 50% -8%,#0d0d12,#050506 60%)!important}
.panel,.scard,.card,.pcard,.seccard,.tile,.kpi,.fav,.pchip,.stat{background:linear-gradient(165deg,#16171c,#0d0e12)!important;border:1px solid rgba(255,255,255,.08)!important;border-radius:16px!important}
.panel,.scard,.card,.pcard,.fav,.seccard{transition:border-color .2s ease,box-shadow .2s ease,transform .18s ease!important}
.panel:hover,.scard:hover,.card:hover,.fav:hover,.seccard:hover{border-color:rgba(255,140,50,.32)!important;box-shadow:0 0 26px rgba(255,140,50,.10)!important}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#26262e;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:#3a3a44}
::selection{background:rgba(255,140,50,.28);color:#fff}
/* bandeau KPI (cockpit + autres) */
.kpiband{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin:14px 0}
.kc{background:linear-gradient(165deg,#16171c,#0d0e12);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:11px 12px;min-width:0}
.kc .kt{font-size:8.5px;letter-spacing:.6px;text-transform:uppercase;color:#8794ab;display:flex;align-items:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kv{font-size:17px;font-weight:900;margin-top:3px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .ks{font-size:9px;color:#6b7689;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kbar{height:5px;border-radius:4px;background:#0a0a0a;overflow:hidden;margin-top:6px}
.kc .kbar>div{height:100%;border-radius:4px}
@media(max-width:760px){.kpiband{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:44%;overflow-x:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;padding-bottom:2px}.kc{scroll-snap-align:start}}
/* navigation mobile sticky en bas */
#mnav{display:none}
@media(max-width:760px){
  #mnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:1200;background:rgba(8,8,10,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid rgba(255,255,255,.09);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
  #mnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;text-decoration:none;color:#8794ab;font-size:9.5px;font-weight:700;padding:5px 2px;border-radius:12px}
  #mnav a .mi{font-size:19px;line-height:1}
  #mnav a.act{color:#FF8C32;background:rgba(255,140,50,.13)}
  body{padding-bottom:68px!important}
  #gnav .gnav-links{display:none}
}
</style>
<script>
(function(){var L=[['/','🔱 Cockpit'],['/ma-page','⭐ Ma Page'],['/watchlist','📋 Watchlist'],['/entreprises','🏢 Entreprises'],['/options','💎 Options']];
function build(){if(document.getElementById('gnav'))return;var p=location.pathname;
var links=L.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'>'+x[1]+'</a>';}).join('');
var nav=document.createElement('nav');nav.id='gnav';
nav.innerHTML='<div class="gnav-in"><div class="gnav-links">'+links+'</div><form class="gnav-search" onsubmit="return gnavGo(event)"><input id="gnavq" placeholder="Rechercher un titre… (ex: NVDA)" autocomplete="off"><button type="submit">→</button></form></div>';
document.body.insertBefore(nav,document.body.firstChild);
var M=[['/','🏠','Cockpit'],['/watchlist','📡','Scan'],['/ma-page','⭐','Ma Page'],['/options','💎','Options'],['/entreprises','🏢','Titres']];
var mn=document.createElement('nav');mn.id='mnav';
mn.innerHTML=M.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'><span class="mi">'+x[1]+'</span>'+x[2]+'</a>';}).join('');
document.body.appendChild(mn);}
window.gnavGo=function(e){e.preventDefault();var v=(document.getElementById('gnavq').value||'').trim().toUpperCase();if(v)location.href='/titre/'+encodeURIComponent(v);return false;};
if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();})();
</script></head><body>
<div class="wrap">
  <div class="head"><div><h1>🗓️ CALENDRIER · RÉSULTATS (EARNINGS)</h1><div class="sub">Prochaines publications des 57 leaders · crucial pour les options (risque IV-crush)</div></div>
    <span class="live"><span class="d"></span><span id="cfresh">…</span></span></div>
  <div class="card"><table><thead><tr><th>TITRE</th><th>DATE</th><th>DANS</th><th>SCORE</th><th>GRADE</th><th>VERDICT</th></tr></thead><tbody id="cbody"></tbody></table></div>
  <p class="muted" style="margin-top:12px;font-size:11px">🔴 = dans moins de 10 jours (éviter d'ouvrir une option courte juste avant). Dates yfinance, peuvent bouger. ANALYSE ONLY.</p>
</div>
<script>__JS__
async function rc(){
  try{const d=await(await fetch('/cal-feed')).json();
    document.getElementById('cfresh').textContent='maj '+(d.updated||'…');
    const it=d.items||[];
    document.getElementById('cbody').innerHTML=it.length?it.map(x=>{const soon=x.dte!=null&&x.dte<10;return `<tr onclick="go('${x.sym}')">
      <td><span class="sym">${x.sym}</span></td><td>${eud(x.date)}</td>
      <td class="${soon?'dn':''}" style="${soon?'font-weight:800':''}">${soon?'🔴 ':''}${x.dte!=null?(x.dte<=0?"aujourd'hui":'J-'+x.dte):'—'}</td>
      <td><span style="color:${clr(x.score||0)};font-weight:800">${x.score!=null?x.score:'—'}</span></td>
      <td>${x.grade?`<span class="grade ${gcls(x.grade)}">${x.grade}</span>`:'—'}</td>
      <td>${x.verdict?`<span class="badge ${bcls(x.verdict)}">${vfr(x.verdict)}</span>`:'—'}</td></tr>`}).join(''):'<tr><td colspan="6" class="muted" style="text-align:center;padding:24px">calendrier en collecte… (~1 min)</td></tr>';
  }catch(e){}
}
setInterval(rc,60000);rc();
</script></body></html>""".replace('__CSS__', _BASE_CSS).replace('__JS__', _BASE_JS)


# ════════════ PAGE SEMAINE — watchlist hebdo FIGÉE (actions + options) ════════════
_WEEKLY_CSS = _BASE_CSS + """
.wrap{max-width:1180px}
.hero{display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin-bottom:6px}
.wk{font-size:12px;font-weight:800;letter-spacing:1.5px;color:#F5B45B;background:rgba(255,140,50,.10);border:1px solid rgba(255,140,50,.22);border-radius:8px;padding:5px 11px}
.frozen{font-size:11px;font-weight:700;color:#8794ab;background:#0e0f15;border:1px solid #ffffff12;border-radius:8px;padding:5px 11px;display:inline-flex;align-items:center;gap:6px}
.frozen .lk{color:#FFB23F}
.statline{display:flex;gap:20px;flex-wrap:wrap;color:#8794ab;font-size:12px;margin:14px 2px 22px}
.statline b{color:#eaf0fa}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(530px,1fr))}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
.pk{background:#111111;border:1px solid #ffffff10;border-radius:16px;padding:0;overflow:hidden;transition:.15s}
.pk:hover{border-color:#ffffff1f}
.pk .top{display:flex;align-items:center;gap:11px;padding:15px 18px 13px;cursor:pointer;border-bottom:1px solid #ffffff0a}
.pk .rk{width:22px;height:22px;border-radius:7px;background:#15161d;color:#8794ab;font-size:11px;font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.pk .nm{font-size:17px;font-weight:800;letter-spacing:.4px}
.pk .sec{font-size:11px;color:#8794ab}
.pk .px{margin-left:auto;text-align:right}
.pk .px .p{font-size:16px;font-weight:800}
.pk .px .c{font-size:11.5px;font-weight:700}
.pk .body{padding:13px 18px 16px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:11px}
.chip{font-size:10.5px;font-weight:700;padding:3px 8px;border-radius:6px;background:#13141b;color:#aab4c7;border:1px solid #ffffff0c}
.chip.tr{color:#FF8C32;border-color:rgba(255,140,50,.2)}.chip.rs{color:#FFB23F}.chip.q{color:#F5B45B}
.why{font-size:12px;color:#c4ccda;line-height:1.5;margin:0 0 12px}
.why b{color:#eaf0fa;font-weight:700}
.lv{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:#ffffff0a;border:1px solid #ffffff0c;border-radius:10px;overflow:hidden;margin-bottom:12px}
.lv .c{background:#0e0f15;padding:9px 10px;text-align:center}
.lv .c .k{font-size:9.5px;letter-spacing:.5px;color:#8794ab;font-weight:700}
.lv .c .v{font-size:14px;font-weight:800;margin-top:2px}
.lv .c.stop .v{color:#EF4444}.lv .c.tp .v{color:#FF8C32}.lv .c.ent .v{color:#eaf0fa}.lv .c.res .v{color:#FFB23F}
.sub2{display:flex;gap:16px;font-size:11px;color:#8794ab;margin-bottom:12px;flex-wrap:wrap}
.sub2 b{color:#c4ccda}
.opt{background:#0a1410;border:1px solid rgba(255,140,50,.16);border-radius:11px;padding:12px 14px}
.opt.noopt{background:#0e0f15;border-color:#ffffff0c}
.opt .ohd{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:800;letter-spacing:.5px;color:#FF8C32;margin-bottom:9px}
.opt .ogr{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-bottom:9px}
.opt .ogr .oc .k{font-size:9.5px;color:#8794ab;font-weight:700}
.opt .ogr .oc .v{font-size:13.5px;font-weight:800;margin-top:1px}
.opt .owhy{font-size:11px;color:#9aa6b8;line-height:1.45}
.warn{display:flex;flex-wrap:wrap;gap:6px;margin-top:11px}
.warnp{font-size:10px;font-weight:700;color:#FFB23F;background:rgba(214,169,63,.08);border:1px solid rgba(214,169,63,.18);border-radius:6px;padding:2px 7px}
.status{font-size:10px;font-weight:800;padding:2px 8px;border-radius:6px;margin-left:8px}
.st-ent{background:rgba(255,178,63,.12);color:#FFB23F}.st-tp{background:rgba(255,140,50,.12);color:#FF8C32}.st-stop{background:rgba(226,86,107,.12);color:#EF4444}.st-wait{background:#15161d;color:#8794ab}
.regen{font-size:11px;font-weight:700;color:#8794ab;background:transparent;border:1px solid #ffffff14;border-radius:8px;padding:6px 12px;cursor:pointer;transition:.14s}
.regen:hover{color:#eaf0fa;border-color:#ffffff2a}
.empty{padding:60px 20px;text-align:center;color:#8794ab}
"""

PAGE_WEEKLY = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Trading Desk"><meta name="mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0b0e14"><link rel="apple-touch-icon" href="/static/icon-180.png"><link rel="manifest" href="/manifest.webmanifest"><title>TRADING DESK · Semaine</title>
<style>__CSS__</style><style id="m-fix">
html,body{overflow-x:hidden;max-width:100%}
@media(max-width:640px){
  .grid,.cols,.herorow,.hero,.panorama,.layout,.heat,.secgrid,.poster{grid-template-columns:1fr!important}
  [style*="grid-template-columns"]{grid-template-columns:1fr!important}
  .top3{grid-template-columns:1fr 1fr 1fr!important}
  .plan{grid-template-columns:1fr 1fr!important}
  td,th{padding-left:9px!important;padding-right:9px!important}
  table{font-size:11.5px!important}
  .wrap,.daily{padding-left:12px!important;padding-right:12px!important}
  .phead-top{padding:15px 16px!important}
  .htitle{font-size:25px!important}
  .hmeta{margin-left:0!important;text-align:left!important}
  .back{padding:5px 9px!important;font-size:11px!important}
}
</style>
<style id="nav-css">
#gnav{position:sticky;top:0;z-index:1000;background:rgba(10,10,12,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid #1c1c24}
.gnav-in{max-width:1500px;margin:0 auto;display:flex;align-items:center;gap:12px;padding:8px 16px;flex-wrap:wrap}
.gnav-links{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
#gnav a{color:#aab4c4;text-decoration:none;font-size:13px;font-weight:700;padding:7px 12px;border-radius:9px;white-space:nowrap}
#gnav a:hover{background:#ffffff0d;color:#fff}
#gnav a.act{background:rgba(245,180,91,.14);color:#F5B45B}
.gnav-search{display:flex;gap:6px;align-items:center}
.gnav-search input{background:#0e0e0e;border:1px solid #1c1c24;color:#e8edf5;border-radius:9px;padding:7px 11px;font-size:13px;width:210px;max-width:46vw}
.gnav-search button{background:rgba(245,180,91,.12);border:1px solid #F5B45B55;color:#F5B45B;border-radius:9px;padding:7px 13px;font-weight:800;cursor:pointer}
.back{display:none!important}
@media(max-width:640px){.gnav-in{padding:6px 10px;gap:8px}.gnav-links{order:2;overflow-x:auto;flex-wrap:nowrap}#gnav a{font-size:12px;padding:6px 9px}.gnav-search{order:1;width:100%}.gnav-search input{flex:1;max-width:none;width:auto}}
/* === systeme de design premium (toutes pages) === */
body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;background:radial-gradient(1200px 780px at 50% -8%,#0d0d12,#050506 60%)!important}
.panel,.scard,.card,.pcard,.seccard,.tile,.kpi,.fav,.pchip,.stat{background:linear-gradient(165deg,#16171c,#0d0e12)!important;border:1px solid rgba(255,255,255,.08)!important;border-radius:16px!important}
.panel,.scard,.card,.pcard,.fav,.seccard{transition:border-color .2s ease,box-shadow .2s ease,transform .18s ease!important}
.panel:hover,.scard:hover,.card:hover,.fav:hover,.seccard:hover{border-color:rgba(255,140,50,.32)!important;box-shadow:0 0 26px rgba(255,140,50,.10)!important}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#26262e;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:#3a3a44}
::selection{background:rgba(255,140,50,.28);color:#fff}
/* bandeau KPI (cockpit + autres) */
.kpiband{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin:14px 0}
.kc{background:linear-gradient(165deg,#16171c,#0d0e12);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:11px 12px;min-width:0}
.kc .kt{font-size:8.5px;letter-spacing:.6px;text-transform:uppercase;color:#8794ab;display:flex;align-items:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kv{font-size:17px;font-weight:900;margin-top:3px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .ks{font-size:9px;color:#6b7689;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kbar{height:5px;border-radius:4px;background:#0a0a0a;overflow:hidden;margin-top:6px}
.kc .kbar>div{height:100%;border-radius:4px}
@media(max-width:760px){.kpiband{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:44%;overflow-x:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;padding-bottom:2px}.kc{scroll-snap-align:start}}
/* navigation mobile sticky en bas */
#mnav{display:none}
@media(max-width:760px){
  #mnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:1200;background:rgba(8,8,10,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid rgba(255,255,255,.09);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
  #mnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;text-decoration:none;color:#8794ab;font-size:9.5px;font-weight:700;padding:5px 2px;border-radius:12px}
  #mnav a .mi{font-size:19px;line-height:1}
  #mnav a.act{color:#FF8C32;background:rgba(255,140,50,.13)}
  body{padding-bottom:68px!important}
  #gnav .gnav-links{display:none}
}
</style>
<script>
(function(){var L=[['/','🔱 Cockpit'],['/ma-page','⭐ Ma Page'],['/watchlist','📋 Watchlist'],['/entreprises','🏢 Entreprises'],['/options','💎 Options']];
function build(){if(document.getElementById('gnav'))return;var p=location.pathname;
var links=L.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'>'+x[1]+'</a>';}).join('');
var nav=document.createElement('nav');nav.id='gnav';
nav.innerHTML='<div class="gnav-in"><div class="gnav-links">'+links+'</div><form class="gnav-search" onsubmit="return gnavGo(event)"><input id="gnavq" placeholder="Rechercher un titre… (ex: NVDA)" autocomplete="off"><button type="submit">→</button></form></div>';
document.body.insertBefore(nav,document.body.firstChild);
var M=[['/','🏠','Cockpit'],['/watchlist','📡','Scan'],['/ma-page','⭐','Ma Page'],['/options','💎','Options'],['/entreprises','🏢','Titres']];
var mn=document.createElement('nav');mn.id='mnav';
mn.innerHTML=M.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'><span class="mi">'+x[1]+'</span>'+x[2]+'</a>';}).join('');
document.body.appendChild(mn);}
window.gnavGo=function(e){e.preventDefault();var v=(document.getElementById('gnavq').value||'').trim().toUpperCase();if(v)location.href='/titre/'+encodeURIComponent(v);return false;};
if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();})();
</script></head><body>
<div class="wrap">
  <div class="head"><div>
    <div class="hero"><h1 style="margin:0">🎯 WATCHLIST DE LA SEMAINE</h1>
      <span class="wk" id="wkid">…</span>
      <span class="frozen"><span class="lk">🔒</span><span id="frozen">sélection figée du lundi</span></span></div>
    <div class="sub">Les meilleurs setups swing à suivre cette semaine + l'option associée · roster GELÉ lundi, niveaux & options live</div></div>
    <button class="regen" id="regen" title="Régénère la sélection (nouvelle semaine ou gros changement marché)">↻ régénérer</button>
  </div>
  <div class="statline" id="stat"></div>
  <div class="grid" id="grid"><div class="empty">construction de la sélection hebdo… (options réelles ~1 min)</div></div>
  <p class="muted" style="margin-top:18px;font-size:11px">Sélection = meilleurs setups (qualité + confiance + score), régime TREND/NEUTRAL, au-dessus MM50, verdict ACHAT/SURVEILLER, earnings de la semaine écartés, max 2 titres/secteur. Le ROSTER est figé pour la semaine (snapshot du lundi) ; les prix, niveaux du plan et options sont rafraîchis. « SI CIBLE → GAIN » de l'option = possibilité indicative si le sous-jacent atteint sa cible avant l'échéance, JAMAIS une promesse. yfinance différé ~15min. ANALYSE ONLY — aucun ordre.</p>
</div>
<script>__JS__
function eud(s){if(!s)return s;const m=String(s).slice(0,10).match(/^(\\d{4})-(\\d{2})-(\\d{2})$/);return m?m[3]+'/'+m[2]+'/'+m[1]:s}
function dcol(d){return d==='Faible'?'#FF8C32':d==='Modéré'?'#FFB23F':d==='Élevé'?'#d98a52':'#EF4444'}
let __n=0;
function spark(arr,w=120,h=30,days=40){
  if(!arr||arr.length<2)return '';
  const d=arr.slice(-days).filter(v=>v!=null&&!isNaN(v));if(d.length<2)return '';
  const up=d[d.length-1]>=d[0],col=up?'#FF8C32':'#EF4444',g='ws'+(++__n);
  const mn=Math.min(...d),mx=Math.max(...d),rg=(mx-mn)||1,p=2,iw=w-4,ih=h-4;
  const X=i=>p+(i/(d.length-1))*iw,Y=v=>p+ih-((v-mn)/rg)*ih;
  const pts=d.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)),ln='M'+pts.join(' L');
  const ar=ln+' L'+X(d.length-1).toFixed(1)+','+(h-p)+' L'+X(0).toFixed(1)+','+(h-p)+' Z';
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="overflow:visible"><defs><linearGradient id="${g}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".22"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs><path d="${ar}" fill="url(#${g})"/><path d="${ln}" fill="none" stroke="${col}" stroke-width="1.3"/></svg>`;
}
function statusPill(s){
  if(!s||s==='—')return '';
  const cls=s.indexOf('STOP')>=0?'st-stop':s.indexOf('TP')>=0?'st-tp':s.indexOf('au-dessus')>=0?'st-ent':'st-wait';
  return `<span class="status ${cls}">${s}</span>`;
}
function optBlock(o){
  if(!o)return `<div class="opt noopt"><div class="ohd" style="color:#8794ab">⚪ OPTION — aucune chaîne propre (illiquide ou hors séance)</div><div class="owhy">Aucun contrat ne passe les filtres de liquidité pour l'instant. Réessaie en séance.</div></div>`;
  const bk={court:'COURT (1-3M, tactique)',long:'LONG (6-12M, robuste)',moyen:'MOYEN (~3M)'}[o.bucket]||(o.bucket||'').toUpperCase();
  const flags=(o.flags||[]).map(f=>`<span class="flagp">${f}</span>`).join('');
  return `<div class="opt">
    <div class="ohd">💎 OPTION RECOMMANDÉE · <span class="bkt bkt-${o.bucket||'long'}">${bk}</span>${o.stale?'<span class="flagp">indicatif hors séance</span>':''}</div>
    <div class="ogr">
      <div class="oc"><div class="k">CALL ${eud(o.exp)}</div><div class="v">$${o.strike} <span class="muted" style="font-size:10px">${o.dte}j</span></div></div>
      <div class="oc"><div class="k">PROBA PROFIT</div><div class="v" style="color:${(o.pop||0)>=50?'#FF8C32':(o.pop||0)>=38?'#FFB23F':'#EF4444'}">${o.pop}%</div></div>
      <div class="oc"><div class="k">DANGER</div><div class="v" style="color:${dcol(o.danger)}">${o.danger}</div></div>
      <div class="oc"><div class="k">COÛT</div><div class="v">$${(o.cost||0).toLocaleString('fr-FR')}</div></div>
    </div>
    <div class="owhy">Δ ${o.delta} · suit. ${o.suit}/100 · si $${o.tgt} → <b class="${o.pot>=0?'up':'dn'}">${o.pot>=0?'+':''}${o.pot}%</b> <span class="muted">(${o.p_tgt}% d'y arriver)</span>${flags?' · '+flags:''}</div>
  </div>`;
}
function card(p,i){
  const L=p.levels||{}, live=p.live||{};
  const px=live.price!=null?live.price:p.price, ch=live.change!=null?live.change:p.change;
  const why=(p.why||[]).map(w=>`<b>${w}</b>`).join(' · ');
  const warns=(p.warnings||[]).map(w=>`<span class="warnp">⚠ ${w}</span>`).join('');
  const earn=p.earnings_dte!=null?`<span class="chip" style="color:#FFB23F">📅 résultats J-${p.earnings_dte}</span>`:'';
  return `<div class="pk">
    <div class="top" onclick="go('${p.symbol}')">
      <span class="rk">${i+1}</span>
      <div><div class="nm">${p.symbol}${statusPill(live.status)}</div><div class="sec">${p.icon} ${p.sector}</div></div>
      <div style="margin-left:14px">${spark(p.series)}</div>
      <div class="px"><div class="p">$${px}</div><div class="c ${ch>=0?'up':'dn'}">${ch>=0?'+':''}${ch}%</div></div>
    </div>
    <div class="body">
      <div class="chips">
        <span class="chip" style="color:${clr(p.score)}">score ${p.score}</span>
        <span class="chip"><span class="grade ${gcls(p.grade)}">${p.grade}</span></span>
        <span class="chip"><span class="badge ${bcls(p.verdict)}">${vfr(p.verdict)}</span></span>
        ${p.regime==='TREND'?'<span class="chip tr">TREND ADX '+p.adx+'</span>':'<span class="chip">'+p.regime+'</span>'}
        <span class="chip q">setup ${p.setup_quality}/100</span>
        <span class="chip rs">RS ${p.rs}</span>
        <span class="chip">RSI ${p.rsi}</span>${earn}
      </div>
      <p class="why">${why}</p>
      <div class="lv">
        <div class="c ent"><div class="k">ENTRÉE</div><div class="v">$${L.entry}</div></div>
        <div class="c stop"><div class="k">STOP</div><div class="v">$${L.stop}</div></div>
        <div class="c tp"><div class="k">TP1 · TP2</div><div class="v">$${L.tp1}<span class="muted" style="font-size:10px"> · ${L.tp2}</span></div></div>
        <div class="c res"><div class="k">RÉSIST.</div><div class="v">$${L.resistance}</div></div>
      </div>
      <div class="sub2"><span>risque <b>${L.risk_pct!=null?L.risk_pct+'%':'—'}</b> au stop</span><span>R:R vers résist. <b>${L.rr_res}</b></span><span>stop sur <b>${L.stop_type||'—'}</b></span><span>TP3 <b>$${L.tp3}</b></span></div>
      ${optBlock(p.option)}
      ${warns?`<div class="warn">${warns}</div>`:''}
    </div>
  </div>`;
}
async function rw(){
  try{const r=await(await fetch('/weekly-feed')).json(); const d=r.data;
    if(!d){return;}
    document.getElementById('wkid').textContent=d.week||'…';
    document.getElementById('frozen').textContent='figée · lundi '+eud(d.monday)+' → '+eud(d.friday);
    const m=d.meta||{};
    document.getElementById('stat').innerHTML=
      `<span><b>${m.n||0}</b> titres suivis</span>`+
      `<span><b>${m.n_options||0}</b> options recommandées</span>`+
      `<span><b>${m.n_sectors||0}</b> secteurs <span class="muted">(${(m.sectors||[]).join(' · ')})</span></span>`+
      `<span class="muted">snapshot du ${d.generated_at||'—'}</span>`+
      `<span class="muted">maj live ${r.updated||'…'}</span>`;
    const picks=d.picks||[];
    document.getElementById('grid').innerHTML=picks.length
      ? picks.map((p,i)=>card(p,i)).join('')
      : '<div class="empty">Aucun setup ne passe les filtres cette semaine (marché en range, ou pas de TREND propre au-dessus MM50). Honnête : mieux vaut rester à l\\'écart.</div>';
  }catch(e){}
}
document.getElementById('regen').onclick=async function(){
  this.textContent='↻ régénération…';this.disabled=true;
  try{await fetch('/weekly-regen',{method:'POST'});await rw();}catch(e){}
  this.textContent='↻ régénérer';this.disabled=false;
};
setInterval(rw,15000);rw();
</script></body></html>""".replace('__CSS__', _WEEKLY_CSS).replace('__JS__', _BASE_JS)


# ════════════ COLONNE DE NAVIGATION (rail gauche, commune) ════════════
_RAIL_CSS = """<style>
body{padding-left:174px}
.rail{position:fixed;left:0;top:0;bottom:0;width:174px;background:#070707;border-right:1px solid #ffffff10;padding:14px 10px;display:flex;flex-direction:column;gap:3px;z-index:1000;overflow-y:auto}
.rail .rlogo{font-size:16px;font-weight:800;letter-spacing:2px;color:#dfe7f3;padding:6px 12px 16px}
.rail .rlogo b{color:#FF8C32}
.rail .rb{display:flex;align-items:center;gap:11px;padding:11px 13px;border-radius:10px;color:#8794ab;text-decoration:none;font-weight:600;font-size:13.5px;transition:.14s}
.rail .rb:hover{background:#ffffff0a;color:#eaf0fa}
.rail .rb.on{background:rgba(255,140,50,.12);color:#FF8C32;box-shadow:inset 0 0 0 1px rgba(255,140,50,.3)}
.rail .rfoot{margin-top:auto;display:flex;align-items:center;gap:8px;padding:12px 13px;color:#8794ab;font-size:11px}
.rail .rfoot .d{width:7px;height:7px;border-radius:50%;background:#FF8C32;box-shadow:0 0 7px #FF8C32;animation:rp 1.6s infinite}
@keyframes rp{0%,100%{opacity:1}50%{opacity:.35}}
@media(max-width:760px){body{padding-left:0}.rail{position:static;width:auto;flex-direction:row;flex-wrap:wrap;bottom:auto}}
/* ═══ TRADING DESK — thème ORANGE / GOLD premium (override global toutes pages) ═══ */
body{background:#070707}
.up{color:#22C55E !important}
.dn{color:#EF4444 !important}
.b-achat{background:rgba(34,197,94,.12)!important;color:#22C55E!important;border:1px solid rgba(34,197,94,.38)!important}
.b-evit{background:rgba(239,68,68,.12)!important;color:#EF4444!important;border:1px solid rgba(239,68,68,.38)!important}
.b-surv{background:rgba(255,140,50,.12)!important;color:#FF8C32!important;border:1px solid rgba(255,140,50,.38)!important}
.panel,.scard,.card,.kpi,.tile,.seccard,.pcard{border:1px solid rgba(255,165,40,.13);transition:box-shadow .2s ease,border-color .2s ease}
.panel:hover,.scard:hover,.card:hover,.tile:hover,.seccard:hover{border-color:rgba(255,140,50,.34);box-shadow:0 0 24px rgba(255,140,50,.09)}
.brand{color:#FF8C32!important;text-shadow:0 0 14px rgba(255,140,50,.45)!important}
.rail{border-right:1px solid rgba(255,140,50,.14);background:linear-gradient(180deg,#0a0a0a,#070707)}
.rail .rb.on{background:linear-gradient(90deg,rgba(255,140,50,.18),rgba(255,140,50,.05))!important;color:#FFB23F!important;box-shadow:inset 0 0 0 1px rgba(255,140,50,.4),0 0 14px rgba(255,140,50,.12)!important}
.tick,.live .d,.navtick,.pill.live .pdot{background:#FF8C32!important;box-shadow:0 0 8px rgba(255,140,50,.7)!important}
.ibtn{color:#FF8C32!important;border-color:rgba(255,140,50,.4)!important;background:rgba(255,140,50,.1)!important}
.sc{text-shadow:0 0 10px rgba(255,140,50,.16)}
::selection{background:rgba(255,140,50,.32);color:#fff}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:#070707}::-webkit-scrollbar-thumb{background:#2a2118;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:rgba(255,140,50,.45)}
/* ═══ cartes & KPI premium (style références gold) ═══ */
.panel,.scard,.card,.pcard,.seccard,.tile{background:linear-gradient(165deg,#151515,#0d0d0d)!important;border-radius:14px!important}
.kpi{background:linear-gradient(160deg,#171717,#0d0d0d)!important;border:1px solid rgba(255,165,40,.1)!important;border-radius:12px!important;padding:13px 15px!important;position:relative;overflow:hidden}
.kpi::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:linear-gradient(180deg,#FFB23F,#C77700)}
.kpi .l{font-size:10px!important;letter-spacing:1px;text-transform:uppercase;color:#8a8a8a!important}
.kpi .v{font-size:20px!important;font-weight:800!important}
.panel h4{letter-spacing:1px}
.panel h4::first-letter{font-size:1.05em}
table thead th{text-transform:uppercase;letter-spacing:.5px;font-size:10px!important}
table tbody tr:hover{background:rgba(255,140,50,.05)!important}
.badge{letter-spacing:.5px}
/* ═══ KPI HERO façon référence (icône ronde gold) ═══ */
.khero{display:grid;grid-template-columns:repeat(auto-fit,minmax(178px,1fr));gap:14px;margin:16px 0}
.kcard{display:flex;align-items:center;gap:14px;background:linear-gradient(160deg,#171717,#0c0c0c);border:1px solid rgba(255,165,40,.15);border-radius:14px;padding:15px 17px;transition:.2s ease}
.kcard:hover{border-color:rgba(255,140,50,.42);box-shadow:0 0 26px rgba(255,140,50,.11);transform:translateY(-2px)}
.kicon{width:46px;height:46px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0;background:radial-gradient(circle at 30% 28%,#3c2c11,#191207);border:1px solid rgba(255,140,50,.5);box-shadow:0 0 16px rgba(255,140,50,.2),inset 0 0 12px rgba(255,140,50,.12)}
.kcard .klabel{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:#8a8a8a}
.kcard .kval{font-size:22px;font-weight:800;margin-top:2px;line-height:1.1;color:#f4f4f4}
.kcard .ksub{font-size:10px;color:#6b6b6b;margin-top:3px}
/* ═══ Risk Center : jauges circulaires ═══ */
.riskgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:10px}
.gauge{display:flex;flex-direction:column;align-items:center;text-align:center;padding:6px}
.gauge .glabel{font-size:9.5px;letter-spacing:.6px;text-transform:uppercase;color:#9a9a9a;margin-top:6px;font-weight:700}
.gauge .gsub{font-size:9px;color:#6b6b6b;margin-top:2px}
/* ═══ Greeks ═══ */
.greeks{display:grid;grid-template-columns:repeat(auto-fit,minmax(92px,1fr));gap:9px}
.greek{background:linear-gradient(160deg,#161616,#0d0d0d);border:1px solid rgba(255,165,40,.12);border-radius:10px;padding:11px 12px;text-align:center}
.greek .gk{font-size:11px;color:#9a9a9a;text-transform:uppercase;letter-spacing:.5px}
.greek .gv{font-size:18px;font-weight:800;margin-top:3px}
/* ═══ GLOW FUTURISTE (cockpit lumineux orange/gold) ═══ */
.kcard .kval{text-shadow:0 0 12px rgba(255,140,50,.14)}
.kcard:hover .kicon{box-shadow:0 0 24px rgba(255,140,50,.4),inset 0 0 14px rgba(255,140,50,.2)}
.kicon{transition:box-shadow .25s}
.sc{text-shadow:0 0 9px rgba(255,140,50,.22)}
.stitle{text-shadow:0 0 12px rgba(255,140,50,.3)}
.dhead .ttl,.head h1{text-shadow:0 0 18px rgba(255,140,50,.3)}
.gauge svg circle:nth-child(2){filter:drop-shadow(0 0 5px currentColor)}
.grade.g-sp{box-shadow:0 0 12px rgba(245,180,91,.28)}
.kpi:hover{box-shadow:0 0 18px rgba(255,140,50,.1)}
.live .d,.tick,.navtick,.rail .rfoot .d,.pill.live .pdot{box-shadow:0 0 10px rgba(255,140,50,.85)!important}
/* ═══ bande INDICES PRINCIPAUX (haut du dashboard) ═══ */
.idxstrip{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.idx{background:linear-gradient(160deg,#171717,#0c0c0c);border:1px solid rgba(56,189,248,.16);border-top:2px solid rgba(56,189,248,.4);border-radius:12px;padding:12px 16px;transition:.2s}
.idx:hover{border-color:rgba(56,189,248,.45);box-shadow:0 0 18px rgba(56,189,248,.12)}
.idx .in{font-size:10.5px;color:#7FB3FF;text-transform:uppercase;letter-spacing:.7px;font-weight:700}
.idx .ip{font-size:19px;font-weight:800;margin-top:4px;color:#f4f4f4}
.idx .ic{font-size:12px;font-weight:700;margin-top:2px}
</style>"""
_RAIL_ITEMS = [('/', '🏠', 'Dashboard'), ('/analyse', '📈', 'Analyse titre'), ('/semaine', '🎯', 'Semaine'),
               ('/options', '💎', 'Options'), ('/sectors', '🔥', 'Secteurs'), ('/news', '📰', 'News'),
               ('/calendar', '🗓️', 'Calendrier')]


def _rail(active):
    btns = ''.join('<a href="%s" class="rb %s">%s <span>%s</span></a>' % (h, 'on' if h == active else '', i, l)
                   for h, i, l in _RAIL_ITEMS)
    return _RAIL_CSS + '<div class="rail"><div class="rlogo">◣ <b>TRADING DESK</b></div>' + btns + '<div class="rfoot"><span class="d"></span>live · maj 15s</div></div>'


PAGE = PAGE.replace('<body>', '<body>' + _rail('/analyse'), 1)
_READ_CSS = ('<style>'
             'body{padding-left:0!important;line-height:1.45}'
             '.daily{max-width:1580px;padding:0 28px 72px}'
             '.stitle{font-size:14px;margin:32px 4px 15px}'
             '.scard .shead{padding:15px 18px;font-size:12.5px}'
             '.scard tbody td{padding:13px 17px;font-size:13px}'
             '.scard thead th{padding:11px 17px}'
             '.secgrid{gap:15px}'
             '.seccard{padding:15px 17px}'
             '.pcard{padding:16px 18px}'
             '.kpi{padding:15px 17px!important}'
             # ── polish esthétique ──
             '.scard,.seccard,.kpi,.pcard,.idx{transition:transform .18s ease,box-shadow .22s ease,border-color .22s ease}'
             '.scard:hover,.seccard:hover,.pcard:hover{transform:translateY(-2px)}'
             '.kpi:hover,.idx:hover{transform:translateY(-1px)}'
             '.stitle{position:relative;padding-left:15px}'
             '.stitle::before{content:"";position:absolute;left:0;top:50%;transform:translateY(-50%);width:4px;height:17px;'
             'background:linear-gradient(#FF8C32,#F5B45B);border-radius:2px;box-shadow:0 0 9px rgba(255,140,50,.5)}'
             '@keyframes fadeUp{from{opacity:0;transform:translateY(9px)}to{opacity:1;transform:none}}'
             '.daily>div{animation:fadeUp .4s ease both}'
             '@keyframes livepulse{0%,100%{opacity:1;text-shadow:0 0 6px currentColor}50%{opacity:.35;text-shadow:none}}'
             '.lpulse{animation:livepulse 1.5s ease-in-out infinite;display:inline-block}'
             '::-webkit-scrollbar{width:10px;height:10px}::-webkit-scrollbar-track{background:#0a0a0a}'
             '::-webkit-scrollbar-thumb{background:#2a2a30;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:#3a3a44}'
             '.scard tbody tr{transition:background .15s}.scard tbody tr:hover{background:rgba(255,140,50,.05)}'
             '</style>')
PAGE_DAILY = PAGE_DAILY.replace('<body>', '<body>' + _RAIL_CSS + _READ_CSS, 1)
PAGE_SECTORS = PAGE_SECTORS.replace('<body>', '<body>' + _rail('/sectors'), 1)
PAGE_NEWS = PAGE_NEWS.replace('<body>', '<body>' + _rail('/news'), 1)
PAGE_OPTIONS = PAGE_OPTIONS.replace('<body>', '<body>' + _rail('/options'), 1)
PAGE_CALENDAR = PAGE_CALENDAR.replace('<body>', '<body>' + _rail('/calendar'), 1)
PAGE_WEEKLY = PAGE_WEEKLY.replace('<body>', '<body>' + _rail('/semaine'), 1)


# ─── DAILY WATCHLIST (page poster auto, style intelligence quotidienne) ──────
PAGE_WATCHLIST = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Trading Desk"><meta name="mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0b0e14"><link rel="apple-touch-icon" href="/static/icon-180.png"><link rel="manifest" href="/manifest.webmanifest">
<title>Daily Watchlist · Trading Desk</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(1200px 600px at 50% -10%,#161616,#070707 60%);color:#e8edf5;font-family:'Segoe UI',system-ui,sans-serif;padding:22px;font-variant-numeric:tabular-nums}
.wrap{max-width:1180px;margin:0 auto}
.phead-top{border:2px solid #FF8C3255;border-radius:18px;padding:20px 26px;background:linear-gradient(135deg,#15110a,#0b0b0b);display:flex;align-items:center;gap:22px;flex-wrap:wrap;box-shadow:0 0 40px rgba(255,140,50,.08)}
.logo{font-size:40px}
.htitle{font-size:38px;font-weight:900;letter-spacing:1px;line-height:1;background:linear-gradient(180deg,#fff,#9aa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hsub{color:#FF8C32;font-weight:800;font-size:15px;letter-spacing:1px;margin-top:6px}
.hmeta{margin-left:auto;text-align:right;font-size:11px;color:#8794ab;line-height:1.7}
.hmeta b{color:#F5B45B}
.disc{color:#5b6678;font-size:10px;margin-top:4px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
.panel{border:1px solid #1c1c24;border-radius:14px;background:linear-gradient(165deg,#131313,#0c0c0c);overflow:hidden;animation:fu .4s ease both}
@keyframes fu{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.ph{display:flex;align-items:center;gap:11px;padding:13px 16px;font-size:14px;font-weight:800;letter-spacing:.5px;border-bottom:1px solid #1c1c24}
.pn{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:900;flex-shrink:0;color:#070707}
.span2{grid-column:1/-1}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:7px 16px;font-size:9.5px;letter-spacing:.5px;color:#6b7689;text-transform:uppercase;font-weight:700}
td{padding:8px 16px;border-top:1px solid #141414}
tbody tr{transition:background .15s}tbody tr:hover{background:rgba(255,140,50,.05)}
.sym{font-weight:800;color:#fff}
.up{color:#22C55E;font-weight:700}.dn{color:#EF4444;font-weight:700}
.muted{color:#6b7689}
.sc{font-weight:800}
.top3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;padding:14px 16px}
.t3{border:1px solid #1c1c24;border-radius:11px;padding:12px;text-align:center;background:#0e0e0e}
.t3 .rk{font-size:12px;font-weight:900}.t3 .tk{font-size:20px;font-weight:900;color:#22C55E;margin:3px 0}
.t3 .mv{font-size:15px;font-weight:800;color:#22C55E}.t3 .px{font-size:11px;color:#8794ab;margin-top:5px}
.foot{text-align:center;color:#5b6678;font-size:11px;margin:22px 0 6px}
.foot b{color:#FF8C32}
.pill{font-size:9px;font-weight:800;padding:1px 7px;border-radius:5px}
.back{position:fixed;top:14px;left:14px;background:#111;border:1px solid #FF8C3255;color:#F5B45B;padding:7px 13px;border-radius:9px;text-decoration:none;font-size:12px;font-weight:700;z-index:9}
.src{font-size:9px;color:#454e5e;padding:5px 16px 10px;font-style:italic}
@media print{.back{display:none}body{padding:0}}
</style><style id="m-fix">
html,body{overflow-x:hidden;max-width:100%}
@media(max-width:640px){
  .grid,.cols,.herorow,.hero,.panorama,.layout,.heat,.secgrid,.poster{grid-template-columns:1fr!important}
  [style*="grid-template-columns"]{grid-template-columns:1fr!important}
  .top3{grid-template-columns:1fr 1fr 1fr!important}
  .plan{grid-template-columns:1fr 1fr!important}
  td,th{padding-left:9px!important;padding-right:9px!important}
  table{font-size:11.5px!important}
  .wrap,.daily{padding-left:12px!important;padding-right:12px!important}
  .phead-top{padding:15px 16px!important}
  .htitle{font-size:25px!important}
  .hmeta{margin-left:0!important;text-align:left!important}
  .back{padding:5px 9px!important;font-size:11px!important}
}
</style>
<style id="nav-css">
#gnav{position:sticky;top:0;z-index:1000;background:rgba(10,10,12,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid #1c1c24}
.gnav-in{max-width:1500px;margin:0 auto;display:flex;align-items:center;gap:12px;padding:8px 16px;flex-wrap:wrap}
.gnav-links{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
#gnav a{color:#aab4c4;text-decoration:none;font-size:13px;font-weight:700;padding:7px 12px;border-radius:9px;white-space:nowrap}
#gnav a:hover{background:#ffffff0d;color:#fff}
#gnav a.act{background:rgba(245,180,91,.14);color:#F5B45B}
.gnav-search{display:flex;gap:6px;align-items:center}
.gnav-search input{background:#0e0e0e;border:1px solid #1c1c24;color:#e8edf5;border-radius:9px;padding:7px 11px;font-size:13px;width:210px;max-width:46vw}
.gnav-search button{background:rgba(245,180,91,.12);border:1px solid #F5B45B55;color:#F5B45B;border-radius:9px;padding:7px 13px;font-weight:800;cursor:pointer}
.back{display:none!important}
@media(max-width:640px){.gnav-in{padding:6px 10px;gap:8px}.gnav-links{order:2;overflow-x:auto;flex-wrap:nowrap}#gnav a{font-size:12px;padding:6px 9px}.gnav-search{order:1;width:100%}.gnav-search input{flex:1;max-width:none;width:auto}}
/* === systeme de design premium (toutes pages) === */
body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;background:radial-gradient(1200px 780px at 50% -8%,#0d0d12,#050506 60%)!important}
.panel,.scard,.card,.pcard,.seccard,.tile,.kpi,.fav,.pchip,.stat{background:linear-gradient(165deg,#16171c,#0d0e12)!important;border:1px solid rgba(255,255,255,.08)!important;border-radius:16px!important}
.panel,.scard,.card,.pcard,.fav,.seccard{transition:border-color .2s ease,box-shadow .2s ease,transform .18s ease!important}
.panel:hover,.scard:hover,.card:hover,.fav:hover,.seccard:hover{border-color:rgba(255,140,50,.32)!important;box-shadow:0 0 26px rgba(255,140,50,.10)!important}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#26262e;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:#3a3a44}
::selection{background:rgba(255,140,50,.28);color:#fff}
/* bandeau KPI (cockpit + autres) */
.kpiband{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin:14px 0}
.kc{background:linear-gradient(165deg,#16171c,#0d0e12);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:11px 12px;min-width:0}
.kc .kt{font-size:8.5px;letter-spacing:.6px;text-transform:uppercase;color:#8794ab;display:flex;align-items:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kv{font-size:17px;font-weight:900;margin-top:3px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .ks{font-size:9px;color:#6b7689;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kbar{height:5px;border-radius:4px;background:#0a0a0a;overflow:hidden;margin-top:6px}
.kc .kbar>div{height:100%;border-radius:4px}
@media(max-width:760px){.kpiband{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:44%;overflow-x:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;padding-bottom:2px}.kc{scroll-snap-align:start}}
/* navigation mobile sticky en bas */
#mnav{display:none}
@media(max-width:760px){
  #mnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:1200;background:rgba(8,8,10,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid rgba(255,255,255,.09);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
  #mnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;text-decoration:none;color:#8794ab;font-size:9.5px;font-weight:700;padding:5px 2px;border-radius:12px}
  #mnav a .mi{font-size:19px;line-height:1}
  #mnav a.act{color:#FF8C32;background:rgba(255,140,50,.13)}
  body{padding-bottom:68px!important}
  #gnav .gnav-links{display:none}
}
</style>
<script>
(function(){var L=[['/','🔱 Cockpit'],['/ma-page','⭐ Ma Page'],['/watchlist','📋 Watchlist'],['/entreprises','🏢 Entreprises'],['/options','💎 Options']];
function build(){if(document.getElementById('gnav'))return;var p=location.pathname;
var links=L.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'>'+x[1]+'</a>';}).join('');
var nav=document.createElement('nav');nav.id='gnav';
nav.innerHTML='<div class="gnav-in"><div class="gnav-links">'+links+'</div><form class="gnav-search" onsubmit="return gnavGo(event)"><input id="gnavq" placeholder="Rechercher un titre… (ex: NVDA)" autocomplete="off"><button type="submit">→</button></form></div>';
document.body.insertBefore(nav,document.body.firstChild);
var M=[['/','🏠','Cockpit'],['/watchlist','📡','Scan'],['/ma-page','⭐','Ma Page'],['/options','💎','Options'],['/entreprises','🏢','Titres']];
var mn=document.createElement('nav');mn.id='mnav';
mn.innerHTML=M.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'><span class="mi">'+x[1]+'</span>'+x[2]+'</a>';}).join('');
document.body.appendChild(mn);}
window.gnavGo=function(e){e.preventDefault();var v=(document.getElementById('gnavq').value||'').trim().toUpperCase();if(v)location.href='/titre/'+encodeURIComponent(v);return false;};
if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();})();
</script></head><body>
<a class="back" href="/">← cockpit</a>
<div class="wrap">
  <div class="phead-top">
    <span class="logo">🔱</span>
    <div>
      <div class="htitle">DAILY WATCHLIST</div>
      <div class="hsub" id="hdate">…</div>
    </div>
    <div class="hmeta">
      <div>TRADING DESK · <b id="hsess">…</b></div>
      <div>Données <b id="hsrc">…</b></div>
      <div class="disc">Analyse éducative — pas un conseil financier.</div>
    </div>
  </div>
  <div class="grid" id="grid"></div>
  <div class="foot">Univers : <b>57 leaders US</b> · scoré, classé, live · <b>scstradingdesk</b> · ⛔ analyse only — aucun ordre</div>
</div>
<script>
const C={g:'#22C55E',r:'#EF4444',o:'#FF8C32',gold:'#F5B45B',blue:'#38BDF8',vio:'#A78BFA',cy:'#34D399'};
function niv(n){return n==='S+'?C.g:n==='S'?C.cy:n==='A'?C.gold:n==='B'?C.o:C.r}
function tim(s){return s==='BUY_NOW'?'✅ achat':s==='BUY_PULLBACK'?'⏳ repli':s==='WATCH_BREAKOUT'?'👀 cassure':s==='TOO_LATE'?'🛑 étendu':'éviter'}
function eu(s){return s?s.slice(8,10)+'/'+s.slice(5,7):''}
function fmt(n){return n==null?'—':(Math.abs(n)>=1e9?(n/1e9).toFixed(1)+'B':Math.abs(n)>=1e6?(n/1e6).toFixed(1)+'M':Math.abs(n)>=1e3?(n/1e3).toFixed(0)+'K':n)}
function panel(n,col,title,inner,src,span){return `<div class="panel ${span?'span2':''}"><div class="ph" style="background:linear-gradient(90deg,${col}24,transparent 72%)"><span class="pn" style="background:${col}">${n}</span><span style="color:#fff;text-shadow:0 1px 2px #000">${title}</span></div>${inner}${src?`<div class="src">${src}</div>`:''}</div>`}
function tbl(head,rows){return `<table><thead><tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')||'<tr><td colspan="9" class="muted" style="padding:14px">—</td></tr>'}</tbody></table>`}
async function load(){
  let s={},q={},cal={},an=[];
  try{[s,q,cal]=await Promise.all([fetch('/scan').then(r=>r.json()),fetch('/quotes').then(r=>r.json()).catch(()=>({})),fetch('/cal-feed').then(r=>r.json()).catch(()=>({}))]);}catch(e){}
  const ql=(q&&q.quotes)||{};
  const det=s.detail||{};
  const rows=(s.rows||[]).map(r=>{const x=ql[r.symbol],d=det[r.symbol]||{};return Object.assign({},d,r,{price:x?x.last:r.price,change:(x&&x.change!=null)?x.change:r.change,live:!!x});});
  const recs=s.recommendations||[];
  const anoms=s.anomalies||[];
  const m=s.market||{};
  const SE={pre:'🌅 AVANT-BOURSE',open:'🟢 SÉANCE OUVERTE',after:'🌙 APRÈS-BOURSE',closed:'🌑 FERMÉ'};
  document.getElementById('hdate').textContent=new Date().toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long',year:'numeric'}).toUpperCase();
  document.getElementById('hsess').textContent=(SE[m.session]||'—')+' · '+(m.et||'');
  document.getElementById('hsrc').textContent=((q&&q.meta&&q.meta.rt)?'TEMPS RÉEL IBKR':'yfinance différé ~15min')+(s.scanned_n?' · '+s.scanned_n+'/'+s.universe_n+' titres analysés':'');
  const byChg=rows.filter(r=>typeof r.change==='number').sort((a,b)=>b.change-a.change);
  const out=[];
  // 0 — VUE MARCHÉ : bande indices + panneau macro (en-tête plateforme)
  const mctx=s.market_ctx||{}, mbreadth=mctx.breadth||{}, idxs=s.indices||[];
  const idxStrip=idxs.map(i=>{const pos=i.vix?(i.change<=0):(i.change>=0);const cc=pos?C.g:C.r;return `<div style="flex:1 1 100px;min-width:94px;background:#0c0c0c;border:1px solid #161616;border-radius:10px;padding:9px 12px"><div class="muted" style="font-size:9px;letter-spacing:.5px;text-transform:uppercase">${i.name}</div><div style="font-size:15px;font-weight:800;margin-top:2px">${(i.price||0).toLocaleString('fr-FR')}</div><div style="font-size:11px;font-weight:700;color:${cc}">${i.change>=0?'▲ +':'▼ '}${i.change}%</div></div>`}).join('');
  if(idxStrip) out.push(`<div class="span2" style="display:flex;flex-wrap:wrap;gap:9px">${idxStrip}</div>`);
  const mvReg=mctx.spy_regime, mvRegTxt=mvReg==='TREND'?'EN TENDANCE':mvReg==='CHOP'?'RANGE AGITÉ':mvReg==='NEUTRAL'?'NEUTRE':'—', mvRegC=mvReg==='TREND'?C.g:mvReg==='CHOP'?C.r:C.gold;
  const mvVb=mctx.vix_band, mvVbC=mvVb==='calme'?C.g:mvVb==='normal'?C.gold:mvVb==='stress'?C.r:'#8794ab';
  const mvRr=mctx.roro, mvRrC=mvRr==='RISK-ON'?C.g:mvRr==='RISK-OFF'?C.r:C.gold;
  const mvStat=(l,v,sub)=>`<div style="flex:1 1 30%;min-width:140px;background:#0c0c0c;border:1px solid #161616;border-radius:10px;padding:10px 13px"><div class="muted" style="font-size:9px;letter-spacing:.5px;text-transform:uppercase">${l}</div><div style="font-size:15px;font-weight:800;margin-top:3px">${v}</div>${sub?`<div class="muted" style="font-size:10px;margin-top:2px">${sub}</div>`:''}</div>`;
  const mvBody=`<div style="display:flex;flex-wrap:wrap;gap:9px;padding:14px">
    ${mvStat('Régime marché',`<span style="color:${mvRegC}">${mvRegTxt}</span>`,mctx.spy_adx!=null?`ADX ${mctx.spy_adx}`:'')}
    ${mvStat('VIX · volatilité',`<span style="color:${mvVbC}">${mctx.vix!=null?mctx.vix:'—'}</span> <span class="muted" style="font-size:11px">${mvVb||''}</span>`,mctx.vix_chg!=null?`${mctx.vix_chg>=0?'+':''}${mctx.vix_chg}% vs hier`:'')}
    ${mvStat('Risk-On / Risk-Off',`<span style="color:${mvRrC}">${mvRr||'—'}</span>`,mctx.roro_gap!=null?`écart cyclique/défensif ${mctx.roro_gap>=0?'+':''}${mctx.roro_gap}`:'')}
    ${mvStat('Participation',`${mbreadth.above50!=null?mbreadth.above50:'—'}% <span class="muted" style="font-size:11px">&gt; MM50</span>`,`${mbreadth.above200!=null?mbreadth.above200:'—'}% &gt; MM200`)}
    ${mvStat('Hausse / Baisse',`<span style="color:${C.g}">${mbreadth.adv!=null?mbreadth.adv:'—'}↑</span> · <span style="color:${C.r}">${mbreadth.dec!=null?mbreadth.dec:'—'}↓</span>`,'séance du jour')}
    ${mvStat('Plus-hauts / Plus-bas',`<span style="color:${C.g}">${mbreadth.nh!=null?mbreadth.nh:'—'}</span> · <span style="color:${C.r}">${mbreadth.nl!=null?mbreadth.nl:'—'}</span>`,'sur 52 semaines')}
  </div>`;
  out.push(`<div class="panel span2" style="border-color:#38BDF833"><div class="ph" style="background:linear-gradient(90deg,#38BDF824,transparent 72%)"><span class="pn" style="background:#38BDF8">📊</span><span style="color:#fff;text-shadow:0 1px 2px #000">VUE MARCHÉ · MACRO</span><span style="margin-left:auto;font-size:10.5px;color:#8794ab;text-align:right">${mctx.verdict||''}</span></div>${mvBody}</div>`);
  // 0b — HEATMAP / PALMARÈS DU JOUR (vue d'ensemble visuelle)
  const hmRows=[...rows].filter(r=>typeof r.change==='number').sort((a,b)=>b.change-a.change);
  const hmCol=(c)=>{const a=Math.min(0.9,0.16+Math.abs(c)/6);return c>=0?`rgba(34,197,94,${a})`:`rgba(239,68,68,${a})`;};
  const hmTiles=hmRows.map(r=>`<div title="${r.symbol}" style="flex:1 1 70px;min-width:62px;background:${hmCol(r.change)};border:1px solid #ffffff14;border-radius:8px;padding:8px 5px;text-align:center"><div style="font-size:12px;font-weight:800;color:#fff;text-shadow:0 1px 2px #000a">${r.symbol}</div><div style="font-size:10.5px;font-weight:700;color:#fff;text-shadow:0 1px 2px #000a">${r.change>=0?'+':''}${r.change}%</div></div>`).join('');
  if(hmTiles){const upN=hmRows.filter(r=>r.change>=0).length;out.push(`<div class="panel span2" style="border-color:#22C55E33"><div class="ph" style="background:linear-gradient(90deg,#22C55E24,transparent 72%)"><span class="pn" style="background:#22C55E">🔥</span><span style="color:#fff;text-shadow:0 1px 2px #000">HEATMAP · PALMARÈS DU JOUR</span><span style="margin-left:auto;font-size:10.5px;color:#8794ab"><span style="color:${C.g}">${upN}↑</span> · <span style="color:${C.r}">${hmRows.length-upN}↓</span></span></div><div style="display:flex;flex-wrap:wrap;gap:6px;padding:14px">${hmTiles}</div><div class="src">Classé de la plus forte hausse à la plus forte baisse · intensité de la couleur = ampleur du mouvement</div></div>`);}
  // 1 — TOP 3
  const t3=byChg.slice(0,3).map((r,i)=>{const md=['#F5B45B','#c0c0c0','#cd7f32'][i];return `<div class="t3" style="border-color:${md}55"><div class="rk" style="color:${md}">#${i+1}</div><div class="tk">${r.symbol}</div><div class="mv ${r.change>=0?'up':'dn'}">${r.change>=0?'+':''}${r.change}%</div><div class="px">$${r.price} · RVOL ${(r.volx||1).toFixed(1)}x · ${(r.regime==='TREND'?'tendance':r.regime==='CHOP'?'range':'neutre')}</div></div>`;}).join('');
  out.push(panel(1,C.gold,'TOP 3 DU JOUR',`<div class="top3">${t3}</div>`,'Plus fortes hausses · cours '+((q&&q.meta&&q.meta.rt)?'live':'différé'),true));
  // 2 — MOMENTUM SCANNER
  const mom=[...rows].sort((a,b)=>(b.score||0)-(a.score||0)).slice(0,9).map(r=>`<tr><td class="sym">${r.symbol}</td><td>$${r.price}</td><td class="${r.change>=0?'up':'dn'}">${r.change>=0?'+':''}${r.change}%</td><td>${(r.volx||1).toFixed(1)}x</td><td class="sc" style="color:${(r.score||0)>=72?C.g:(r.score||0)>=55?C.gold:C.r}">${r.score||'—'}</td><td class="muted">${r.regime==='TREND'?'Tendance':r.regime==='CHOP'?'Range':'Neutre'}</td></tr>`);
  out.push(panel(2,C.g,'MOMENTUM SCANNER',tbl(['Ticker','Prix','Var','RVOL','Score','Régime'],mom),'Classé par score technique'));
  // 3 — RUNNERS / CONTINUATION
  const run=[...rows].filter(r=>(r.signals||{}).above50).sort((a,b)=>(b.rs||0)-(a.rs||0)).slice(0,9).map(r=>{const p=r.plan||{};return `<tr><td class="sym">${r.symbol}</td><td>$${r.price}</td><td class="sc" style="color:${C.gold}">${r.score||'—'}</td><td class="muted">$${p.stop||'—'}</td><td style="color:${C.blue}">$${p.resistance||p.tp1||'—'}</td><td class="muted">RS ${Math.round(r.rs||0)}</td></tr>`;});
  out.push(panel(3,C.blue,'RUNNERS / CONTINUATION',tbl(['Ticker','Prix','Score','Support','Résistance','Force'],run),'Au-dessus MM50 · classé par force relative'));
  // 4 — PICKS IBKR
  const pk=recs.filter(r=>r.score40>=22).slice(0,9).map(r=>`<tr><td class="sym">${r.symbol}</td><td><span class="pill" style="color:${niv(r.niveau)};border:1px solid ${niv(r.niveau)}66">${r.niveau}</span></td><td class="sc" style="color:${niv(r.niveau)}">${r.score40}/40</td><td style="color:${r.tone==='buy'?C.g:r.tone==='pullback'?C.cy:r.tone==='wait'?C.blue:C.r};font-weight:700">${r.decision}</td><td class="muted">${tim(r.timing)}</td></tr>`);
  out.push(panel(4,C.o,'PICKS IBKR · SCORE /40',tbl(['Ticker','Niveau','Score','Décision','Timing'],pk),'Moteur de décision IBKR · clic cockpit pour le détail'));
  // 5 — TOP BAISSES / À ÉVITER
  const lo=byChg.slice(-6).reverse().map(r=>`<tr><td class="sym">${r.symbol}</td><td>$${r.price}</td><td class="dn">${r.change}%</td><td>${(r.volx||1).toFixed(1)}x</td><td class="muted">RSI ${Math.round(r.rsi||0)}</td></tr>`);
  out.push(panel(5,C.r,'PLUS FORTES BAISSES',tbl(['Ticker','Prix','Var','RVOL','RSI'],lo),'Sous pression aujourd hui'));
  // 6 — CATALYST RADAR (earnings)
  const cr=((cal.items)||[]).slice(0,8).map(x=>{const soon=x.dte!=null&&x.dte<7;return `<tr><td class="sym">${x.sym}</td><td>📊 Résultats</td><td class="muted">${eu(x.date)}</td><td class="${soon?'dn':'muted'}" style="${soon?'font-weight:800':''}">${soon?'🔴 ':''}${x.dte<=0?'auj.':'J-'+x.dte}</td></tr>`;});
  out.push(panel(6,C.vio,'CATALYST RADAR · RÉSULTATS',tbl(['Ticker','Catalyseur','Date','Échéance'],cr),'Risque IV-crush sur les options à proximité'));
  // 7 — ANOMALIES / FORENSICS
  const af=anoms.slice(0,9).map(a=>`<tr><td class="sym">${a.symbol}</td><td style="color:${a.dir==='UP'?C.g:a.dir==='DOWN'||a.dir==='WARN'?C.r:C.gold}">${a.label}</td><td class="sc" style="color:${a.sev>=70?C.r:C.gold}">${a.sev}</td><td class="muted" style="font-size:10.5px">${(a.note||'').slice(0,42)}</td></tr>`);
  out.push(panel(7,C.cy,'FORENSICS · ANOMALIES DU JOUR',tbl(['Ticker','Signal','Sévérité','Détail'],af),'Ce qui sort de l ordinaire · volume, cassures, divergences',true));
  // 8 — LONG WATCHLIST
  const lw=[...rows].filter(r=>(r.score||0)>=60&&(r.signals||{}).above200).sort((a,b)=>(b.score||0)-(a.score||0)).slice(0,10).map(r=>`<tr><td class="sym">${r.symbol}</td><td class="sc" style="color:${(r.score||0)>=72?C.g:C.gold}">${r.score}</td><td>${r.grade||''}</td><td class="muted">Tendance + base</td><td class="muted">${r.regime==='TREND'?'directionnel':'consolidation'}</td></tr>`);
  out.push(panel(8,C.gold,'LONG WATCHLIST · 1-6 MOIS',tbl(['Ticker','Score','Grade','Setup','Régime'],lw),'Au-dessus MM200 · candidats swing/LEAPS',true));
  // 9 — SMART MONEY / SWING FLOW (bullish/bearish)
  const big=['MU','MSFT','AAPL','META','GOOGL','NVDA','AMD','AMZN','AVGO'];
  const sm=big.map(sy=>rows.find(r=>r.symbol===sy)).filter(Boolean).map(r=>{const bull=(r.score||0)>=58&&(r.signals||{}).above50;const flow=bull?(r.regime==='TREND'?'Flux haussier confirmé':'Haussier, prix pas prêt'):((r.signals||{}).above50?'Neutre / affaibli':'Flux baissier');return `<tr><td class="sym">${r.symbol}</td><td style="color:${bull?C.g:C.r};font-weight:800">${bull?'Bullish':'Bearish'}</td><td class="sc">${r.score||'—'}</td><td class="muted" style="font-size:10.5px">${flow} · ${bull?'setup CALL':'setup PUT'}</td></tr>`;});
  out.push(panel(9,C.gold,'SMART MONEY / SWING FLOW',tbl(['Ticker','Biais','Score','Flux'],sm),'Biais call/put sur les gros caps · régime + structure'));
  // 10 — CRYPTO / RISK-ON
  const mc=s.market_ctx||{};
  const cry=['COIN','MSTR'].map(sy=>rows.find(r=>r.symbol===sy)).filter(Boolean).map(r=>`<tr><td class="sym">${r.symbol}</td><td>$${r.price}</td><td class="${r.change>=0?'up':'dn'}">${r.change>=0?'+':''}${r.change}%</td><td class="muted">RVOL ${(r.volx||1).toFixed(1)}x</td></tr>`);
  out.push(panel(10,C.o,'CRYPTO / RISK-ON · '+(mc.roro||'—'),tbl(['Ticker','Prix','Var','Volume'],cry),'COIN/MSTR = proxy appétit pour le risque · VIX '+(mc.vix!=null?mc.vix:'—')));
  // 11 — ROTATION SECTORIELLE (détaillé : 1 carte par secteur)
  const SCc=(v)=>v>=72?C.g:v>=55?C.gold:C.r;
  const secCards=(s.sectors||[]).map((x,i)=>{
    const sc=x.avg_score||0, dl=x.delta, chg=x.avg_change||0;
    const dtxt=dl==null?'<span class="muted" style="font-size:10px">stable</span>':(dl>0?`<span style="color:${C.g};font-weight:800">▲ +${dl}</span>`:dl<0?`<span style="color:${C.r};font-weight:800">▼ ${dl}</span>`:'<span class="muted" style="font-size:10px">=</span>');
    const rb=x.risk_band, rc=rb==='Low'?C.g:rb==='Med'?C.gold:C.r, ld=x.leader||{};
    const stat=(l,v)=>`<div style="flex:1 1 44%;min-width:118px;background:#0c0c0c;border:1px solid #161616;border-radius:8px;padding:6px 9px"><div class="muted" style="font-size:8.5px;letter-spacing:.5px;text-transform:uppercase">${l}</div><div style="font-size:12.5px;font-weight:700;margin-top:1px">${v}</div></div>`;
    const mem=(x.members||[]).slice(0,12).map(m=>`<span style="font-size:9.5px;font-weight:700;padding:2px 6px;border-radius:5px;border:1px solid ${(m.change||0)>=0?C.g+'55':C.r+'55'};color:${(m.change||0)>=0?C.g:C.r}">${m.symbol}</span>`).join(' ');
    return `<div style="border:1px solid #1c1c24;border-radius:12px;background:linear-gradient(165deg,#121212,#0b0b0b);padding:13px 14px">
      <div style="display:flex;align-items:center;gap:9px">
        <span style="font-size:13px;font-weight:900;color:#5b6678">#${i+1}</span>
        <span style="font-size:18px">${x.icon||'•'}</span>
        <span style="font-size:14px;font-weight:800;color:#fff">${x.sector}</span>
        <span class="muted" style="font-size:10px">· ${x.n} val.</span>
        <span style="margin-left:auto;font-size:22px;font-weight:900;color:${SCc(sc)}">${sc}</span>
        <span style="font-size:11px">${dtxt}</span>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:7px;margin-top:11px">
        ${stat('Tendance jour',`<span style="color:${chg>=0?C.g:C.r}">${chg>=0?'+':''}${chg}%</span>`)}
        ${stat('% Achat',`${x.pct_buy}% <span class="muted" style="font-size:9.5px">(${x.n_buy}B·${x.n_watch}W·${x.n_avoid}A)</span>`)}
        ${stat('Breadth MM50',`<span style="color:${SCc(x.b50)}">${x.b50}%</span>`)}
        ${stat('Breadth MM200',`<span style="color:${SCc(x.b200)}">${x.b200}%</span>`)}
        ${stat('Force relative',`RS ${x.avg_rs}`)}
        ${stat('Volume (RVOL)',`${x.avg_rvol}x`)}
        ${stat('Risque',`<span style="color:${rc}">${rb}</span>`)}
        ${stat('Leader',`<b style="color:#fff">${ld.symbol||'—'}</b> ${ld.score!=null?`<span style="color:${SCc(ld.score)}">${ld.score}</span>`:''}`)}
      </div>
      ${mem?`<div style="margin-top:10px"><div class="muted" style="font-size:8.5px;letter-spacing:.5px;text-transform:uppercase;margin-bottom:5px">Membres · par force</div><div style="display:flex;flex-wrap:wrap;gap:5px">${mem}</div></div>`:''}
    </div>`;
  }).join('');
  out.push(`<div class="panel span2" style="border-color:#34D39933"><div class="ph" style="background:linear-gradient(90deg,#34D39924,transparent 72%)"><span class="pn" style="background:#34D399">⚡</span><span style="color:#fff;text-shadow:0 1px 2px #000">ROTATION SECTORIELLE · 9 SECTEURS</span></div><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:13px;padding:14px">${secCards||'<div class="muted" style="padding:10px">—</div>'}</div><div class="src">Classé par score moyen · ▲/▼ = variation du score vs hier · Breadth = % du secteur au-dessus de sa moyenne mobile (MM50/MM200) · RS = force relative vs marché · B/W/A = Achat/Surveiller/Éviter</div></div>`);
  // THÈMES DU MARCHÉ
  const secs=(s.sectors||[]).slice(0,4).map(x=>x.sector).join(' · ');
  const focus=mc.spy_regime==='TREND'?'Suivre la tendance · acheter la force · gap-hold':mc.spy_regime==='CHOP'?'Range agité · patience · éviter le chase':'Sélectif · attendre les confirmations';
  out.push(`<div class="panel span2" style="border-color:#FF8C3233"><div class="ph" style="background:linear-gradient(90deg,#FF8C3224,transparent 72%)"><span class="pn" style="background:#FF8C32">★</span><span style="color:#fff;text-shadow:0 1px 2px #000">THÈMES DU MARCHÉ</span></div><div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;padding:16px"><div><div style="font-size:11px;font-weight:800;color:#F5B45B;margin-bottom:6px">🏛️ Secteurs forts</div><div style="font-size:12.5px;line-height:1.5">${secs||'—'}</div></div><div><div style="font-size:11px;font-weight:800;color:#F5B45B;margin-bottom:6px">🧭 Lecture du marché</div><div style="font-size:12.5px;line-height:1.5">${mc.verdict||'—'}</div></div><div><div style="font-size:11px;font-weight:800;color:#F5B45B;margin-bottom:6px">🎯 Focus du jour</div><div style="font-size:12.5px;line-height:1.5">${focus}</div></div></div></div>`);
  // 12 — GUIDE / LÉGENDE DES INDICATEURS (comprendre le cockpit)
  const gloss=[
    ['📊 Score /100','Note technique globale du titre (tendance, momentum, structure). <b style="color:'+C.g+'">≥72</b> = fort · <b style="color:'+C.gold+'">55-71</b> = correct · <b style="color:'+C.r+'">&lt;55</b> = faible.'],
    ['🎯 Score IBKR /40','Note du moteur de décision (technique + fondamentaux + option). Plus la note est haute, plus le dossier est complet.'],
    ['🏅 Niveaux S+/S/A/B/C','Qualité du setup : <b>S+/S</b> = élite · <b>A</b> = solide · <b>B</b> = correct · <b>C</b> = à éviter.'],
    ['⚡ RVOL','Volume relatif vs la moyenne. <b>&gt;1.5x</b> = activité anormale (intérêt fort). <b>~1x</b> = volume normal.'],
    ['🌊 Régime','<b>Tendance</b> = mouvement directionnel (suivre) · <b>Range</b> = marché agité (patience) · <b>Neutre</b> = indécis.'],
    ['💪 RS (force relative)','Performance du titre vs le marché. <b>&gt;50</b> = surperforme · <b>&lt;50</b> = sous-performe.'],
    ['📈 Breadth MM50/MM200','% de valeurs au-dessus de leurs moyennes mobiles 50/200 jours = santé de la tendance de fond.'],
    ['😱 VIX','Indice de la peur. <b style="color:'+C.g+'">&lt;16 calme</b> · <b style="color:'+C.gold+'">16-22 normal</b> · <b style="color:'+C.r+'">&gt;22 stress</b>.'],
    ['⚖️ Risk-On / Risk-Off','<b style="color:'+C.g+'">Risk-On</b> = appétit pour le risque (cycliques forts) · <b style="color:'+C.r+'">Risk-Off</b> = fuite vers la sécurité.'],
    ['✅ Verdicts','<b style="color:'+C.g+'">Achat</b> = signal favorable · <b style="color:'+C.blue+'">Surveiller</b> = attendre confirmation · <b style="color:'+C.r+'">Éviter</b> = pas maintenant.'],
  ].map(g=>`<div style="flex:1 1 30%;min-width:235px;background:#0c0c0c;border:1px solid #161616;border-radius:10px;padding:11px 13px"><div style="font-size:12px;font-weight:800;color:#cfd8e6;margin-bottom:4px">${g[0]}</div><div class="muted" style="font-size:11px;line-height:1.55">${g[1]}</div></div>`).join('');
  out.push(`<div class="panel span2" style="border-color:#A78BFA33"><div class="ph" style="background:linear-gradient(90deg,#A78BFA24,transparent 72%)"><span class="pn" style="background:#A78BFA">?</span><span style="color:#fff;text-shadow:0 1px 2px #000">GUIDE · COMPRENDRE LES INDICATEURS</span></div><div style="display:flex;flex-wrap:wrap;gap:9px;padding:14px">${gloss}</div><div class="src">Repère pédagogique — analyse éducative, jamais un conseil financier. Aucun ordre passé — lecture seule.</div></div>`);
  document.getElementById('grid').innerHTML=out.join('');
}
load();setInterval(load,20000);
</script></body></html>"""


@app.route('/watchlist')
def watchlist_page():
    return PAGE_WATCHLIST


# ─── OPTIONS DESK (page complète : toutes les options à acheter / analyser) ──
PAGE_OPTIONS_DESK = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Trading Desk"><meta name="mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0b0e14"><link rel="apple-touch-icon" href="/static/icon-180.png"><link rel="manifest" href="/manifest.webmanifest">
<title>Options Desk · Trading Desk</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(1200px 600px at 50% -10%,#161616,#070707 60%);color:#e8edf5;font-family:'Segoe UI',system-ui,sans-serif;padding:20px;font-variant-numeric:tabular-nums}
.wrap{max-width:1400px;margin:0 auto}
.htop{border:2px solid #F5B45B55;border-radius:18px;padding:18px 24px;background:linear-gradient(135deg,#15110a,#0b0b0b);display:flex;align-items:center;gap:20px;flex-wrap:wrap;box-shadow:0 0 40px rgba(245,180,91,.08)}
.htitle{font-size:34px;font-weight:900;letter-spacing:1px;background:linear-gradient(180deg,#F5B45B,#FF8C32);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hsub{color:#8794ab;font-weight:700;font-size:13px;margin-top:4px}
.hmeta{margin-left:auto;text-align:right;font-size:11px;color:#8794ab;line-height:1.7}.hmeta b{color:#F5B45B}
.profile{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}
.pchip{flex:1;min-width:200px;border:1px solid #1c1c24;border-radius:11px;padding:11px 14px;background:linear-gradient(165deg,#131313,#0c0c0c)}
.pchip .t{font-size:12px;font-weight:800}.pchip .d{font-size:10.5px;color:#8794ab;margin-top:3px;line-height:1.4}
.feat{border:1.5px solid #F5B45B66;border-radius:14px;background:linear-gradient(135deg,rgba(245,180,91,.08),#0c0c0c);padding:16px 20px;margin:6px 0 16px;display:flex;gap:24px;flex-wrap:wrap;align-items:center}
.feat .big{font-size:26px;font-weight:900}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
.stat{flex:1;min-width:120px;border:1px solid #1c1c24;border-radius:11px;padding:12px 14px;text-align:center;background:#0e0e0e}
.stat .n{font-size:24px;font-weight:900}.stat .l{font-size:10px;color:#8794ab;text-transform:uppercase;letter-spacing:.5px;margin-top:2px}
.panel{border:1px solid #1c1c24;border-radius:14px;background:linear-gradient(165deg,#121212,#0a0a0a);overflow:hidden;margin-bottom:16px;animation:fu .4s ease both}
@keyframes fu{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.ph{display:flex;align-items:center;gap:11px;padding:13px 18px;font-size:15px;font-weight:900;letter-spacing:.5px;color:#fff;text-shadow:0 1px 2px #000;border-bottom:1px solid #ffffff14}
.ph .cnt{margin-left:auto;font-size:11px;font-weight:700;color:#8794ab}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:8px 12px;font-size:9px;letter-spacing:.5px;color:#6b7689;text-transform:uppercase;font-weight:700;white-space:nowrap}
td{padding:9px 12px;border-top:1px solid #141414;white-space:nowrap}
tbody tr{cursor:pointer;transition:background .15s}tbody tr:hover{background:rgba(245,180,91,.06)}
.tscroll{overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:thin}
.fbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:4px 0 14px}
.fgrp{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:#0e0e0e;border:1px solid #1c1c24;color:#cfd8e6;padding:6px 12px;border-radius:9px;font-size:12px;font-weight:700;cursor:pointer}
.chip.on{background:rgba(245,180,91,.12);border-color:#F5B45B66;color:#F5B45B}
.fsel{font-size:11px;color:#8794ab;display:flex;align-items:center;gap:6px}
.fsel select{background:#0e0e0e;border:1px solid #1c1c24;color:#e8edf5;border-radius:8px;padding:5px 8px;font-size:12px}
.swipe{display:none;font-size:10px;color:#5b6678;padding:0 18px 8px}
@media(max-width:640px){.swipe{display:block}}
.sym{font-weight:800;color:#fff}.muted{color:#6b7689}.up{color:#22C55E;font-weight:700}.dn{color:#EF4444;font-weight:700}
.fit{font-size:9.5px;font-weight:800;padding:2px 8px;border-radius:6px}
.back{position:fixed;top:13px;left:13px;background:#111;border:1px solid #F5B45B55;color:#F5B45B;padding:7px 13px;border-radius:9px;text-decoration:none;font-size:12px;font-weight:700;z-index:9}
.foot{text-align:center;color:#5b6678;font-size:11px;margin:20px 0 6px}.foot b{color:#F5B45B}
.src{font-size:9px;color:#454e5e;padding:6px 18px 10px;font-style:italic}
@media print{.back{display:none}body{padding:0}}
</style><style id="m-fix">
html,body{overflow-x:hidden;max-width:100%}
@media(max-width:640px){
  .grid,.cols,.herorow,.hero,.panorama,.layout,.heat,.secgrid,.poster{grid-template-columns:1fr!important}
  [style*="grid-template-columns"]{grid-template-columns:1fr!important}
  .top3{grid-template-columns:1fr 1fr 1fr!important}
  .plan{grid-template-columns:1fr 1fr!important}
  td,th{padding-left:9px!important;padding-right:9px!important}
  table{font-size:11.5px!important}
  .wrap,.daily{padding-left:12px!important;padding-right:12px!important}
  .phead-top{padding:15px 16px!important}
  .htitle{font-size:25px!important}
  .hmeta{margin-left:0!important;text-align:left!important}
  .back{padding:5px 9px!important;font-size:11px!important}
}
</style>
<style id="nav-css">
#gnav{position:sticky;top:0;z-index:1000;background:rgba(10,10,12,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid #1c1c24}
.gnav-in{max-width:1500px;margin:0 auto;display:flex;align-items:center;gap:12px;padding:8px 16px;flex-wrap:wrap}
.gnav-links{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
#gnav a{color:#aab4c4;text-decoration:none;font-size:13px;font-weight:700;padding:7px 12px;border-radius:9px;white-space:nowrap}
#gnav a:hover{background:#ffffff0d;color:#fff}
#gnav a.act{background:rgba(245,180,91,.14);color:#F5B45B}
.gnav-search{display:flex;gap:6px;align-items:center}
.gnav-search input{background:#0e0e0e;border:1px solid #1c1c24;color:#e8edf5;border-radius:9px;padding:7px 11px;font-size:13px;width:210px;max-width:46vw}
.gnav-search button{background:rgba(245,180,91,.12);border:1px solid #F5B45B55;color:#F5B45B;border-radius:9px;padding:7px 13px;font-weight:800;cursor:pointer}
.back{display:none!important}
@media(max-width:640px){.gnav-in{padding:6px 10px;gap:8px}.gnav-links{order:2;overflow-x:auto;flex-wrap:nowrap}#gnav a{font-size:12px;padding:6px 9px}.gnav-search{order:1;width:100%}.gnav-search input{flex:1;max-width:none;width:auto}}
/* === systeme de design premium (toutes pages) === */
body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;background:radial-gradient(1200px 780px at 50% -8%,#0d0d12,#050506 60%)!important}
.panel,.scard,.card,.pcard,.seccard,.tile,.kpi,.fav,.pchip,.stat{background:linear-gradient(165deg,#16171c,#0d0e12)!important;border:1px solid rgba(255,255,255,.08)!important;border-radius:16px!important}
.panel,.scard,.card,.pcard,.fav,.seccard{transition:border-color .2s ease,box-shadow .2s ease,transform .18s ease!important}
.panel:hover,.scard:hover,.card:hover,.fav:hover,.seccard:hover{border-color:rgba(255,140,50,.32)!important;box-shadow:0 0 26px rgba(255,140,50,.10)!important}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#26262e;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:#3a3a44}
::selection{background:rgba(255,140,50,.28);color:#fff}
/* bandeau KPI (cockpit + autres) */
.kpiband{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin:14px 0}
.kc{background:linear-gradient(165deg,#16171c,#0d0e12);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:11px 12px;min-width:0}
.kc .kt{font-size:8.5px;letter-spacing:.6px;text-transform:uppercase;color:#8794ab;display:flex;align-items:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kv{font-size:17px;font-weight:900;margin-top:3px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .ks{font-size:9px;color:#6b7689;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kbar{height:5px;border-radius:4px;background:#0a0a0a;overflow:hidden;margin-top:6px}
.kc .kbar>div{height:100%;border-radius:4px}
@media(max-width:760px){.kpiband{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:44%;overflow-x:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;padding-bottom:2px}.kc{scroll-snap-align:start}}
/* navigation mobile sticky en bas */
#mnav{display:none}
@media(max-width:760px){
  #mnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:1200;background:rgba(8,8,10,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid rgba(255,255,255,.09);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
  #mnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;text-decoration:none;color:#8794ab;font-size:9.5px;font-weight:700;padding:5px 2px;border-radius:12px}
  #mnav a .mi{font-size:19px;line-height:1}
  #mnav a.act{color:#FF8C32;background:rgba(255,140,50,.13)}
  body{padding-bottom:68px!important}
  #gnav .gnav-links{display:none}
}
</style>
<script>
(function(){var L=[['/','🔱 Cockpit'],['/ma-page','⭐ Ma Page'],['/watchlist','📋 Watchlist'],['/entreprises','🏢 Entreprises'],['/options','💎 Options']];
function build(){if(document.getElementById('gnav'))return;var p=location.pathname;
var links=L.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'>'+x[1]+'</a>';}).join('');
var nav=document.createElement('nav');nav.id='gnav';
nav.innerHTML='<div class="gnav-in"><div class="gnav-links">'+links+'</div><form class="gnav-search" onsubmit="return gnavGo(event)"><input id="gnavq" placeholder="Rechercher un titre… (ex: NVDA)" autocomplete="off"><button type="submit">→</button></form></div>';
document.body.insertBefore(nav,document.body.firstChild);
var M=[['/','🏠','Cockpit'],['/watchlist','📡','Scan'],['/ma-page','⭐','Ma Page'],['/options','💎','Options'],['/entreprises','🏢','Titres']];
var mn=document.createElement('nav');mn.id='mnav';
mn.innerHTML=M.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'><span class="mi">'+x[1]+'</span>'+x[2]+'</a>';}).join('');
document.body.appendChild(mn);}
window.gnavGo=function(e){e.preventDefault();var v=(document.getElementById('gnavq').value||'').trim().toUpperCase();if(v)location.href='/titre/'+encodeURIComponent(v);return false;};
if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();})();
</script></head><body>
<a class="back" href="/">← cockpit</a>
<div class="wrap">
  <div class="htop"><span style="font-size:38px">💎</span>
    <div><div class="htitle">OPTIONS DESK</div><div class="hsub" id="hdate">…</div></div>
    <div class="hmeta"><div>Toutes les options à <b>acheter / analyser</b></div><div id="hsrc">…</div><div style="font-size:10px;color:#5b6678;margin-top:3px">Analyse éducative — pas un conseil · ⛔ aucun ordre</div></div>
  </div>
  <div class="profile">
    <div class="pchip" style="border-color:#22C55E44"><div class="t" style="color:#22C55E">💎 LEAPS Core (long)</div><div class="d">6-18 mois · delta 0.65-0.90 · tenir 1-3 mois · théta lent · cœur de stratégie</div></div>
    <div class="pchip" style="border-color:#38BDF844"><div class="t" style="color:#38BDF8">⚡ Swing (moyen)</div><div class="d">~3 mois · delta 0.50-0.70 · mouvement rapide · taille modérée</div></div>
    <div class="pchip" style="border-color:#FFB23F44"><div class="t" style="color:#FFB23F">🎲 Tactique (court)</div><div class="d">1-2 mois · théta violent · petite taille · setup exceptionnel only</div></div>
  </div>
  <div id="feat"></div>
  <div id="alerts"></div>
  <div class="stats" id="stats"></div>
  <div id="filterbar"></div>
  <div id="sections"></div>
  <div id="simulator"></div>
  <div id="strategies"></div>
  <div class="panel" style="border-color:#A78BFA33"><div class="ph" style="background:linear-gradient(90deg,#A78BFA26,transparent 70%)"><span style="color:#fff">📚 GUIDE OPTIONS · COMPRENDRE CHAQUE COLONNE</span></div>
  <div style="display:flex;flex-wrap:wrap;gap:9px;padding:14px">
    <div style="flex:1 1 30%;min-width:235px;background:#0c0c0c;border:1px solid #161616;border-radius:10px;padding:11px 13px"><div style="font-size:12px;font-weight:800;color:#cfd8e6;margin-bottom:4px">💎 Qualité /100</div><div class="muted" style="font-size:11px;line-height:1.55">Note globale du contrat : liquidité, spread, delta, échéance, IV. <b style="color:#22C55E">≥78</b> excellent · <b style="color:#F5B45B">62-77</b> bon · <b style="color:#EF4444">&lt;50</b> à éviter.</div></div>
    <div style="flex:1 1 30%;min-width:235px;background:#0c0c0c;border:1px solid #161616;border-radius:10px;padding:11px 13px"><div style="font-size:12px;font-weight:800;color:#cfd8e6;margin-bottom:4px">Δ Delta</div><div class="muted" style="font-size:11px;line-height:1.55">Sensibilité au titre : delta 0.70 = l’option gagne ~0,70$ quand l’action monte de 1$. Plus haut = plus directionnel.</div></div>
    <div style="flex:1 1 30%;min-width:235px;background:#0c0c0c;border:1px solid #161616;border-radius:10px;padding:11px 13px"><div style="font-size:12px;font-weight:800;color:#cfd8e6;margin-bottom:4px">🎯 POP &amp; Proba cible</div><div class="muted" style="font-size:11px;line-height:1.55">POP = probabilité d’être en profit à l’échéance. Proba cible = chance d’atteindre l’objectif du titre. Plus c’est haut, mieux c’est.</div></div>
    <div style="flex:1 1 30%;min-width:235px;background:#0c0c0c;border:1px solid #161616;border-radius:10px;padding:11px 13px"><div style="font-size:12px;font-weight:800;color:#cfd8e6;margin-bottom:4px">⚠️ Danger</div><div class="muted" style="font-size:11px;line-height:1.55">Risque global combiné : érosion théta + faible POP + earnings proches + IV chère + court terme. <b style="color:#22C55E">Faible</b> → <b style="color:#EF4444">Extrême</b>.</div></div>
    <div style="flex:1 1 30%;min-width:235px;background:#0c0c0c;border:1px solid #161616;border-radius:10px;padding:11px 13px"><div style="font-size:12px;font-weight:800;color:#cfd8e6;margin-bottom:4px">⏳ Théta &amp; IV</div><div class="muted" style="font-size:11px;line-height:1.55">Théta = valeur perdue chaque jour (le temps joue contre l’acheteur). IV = volatilité implicite : haute = options chères.</div></div>
    <div style="flex:1 1 30%;min-width:235px;background:#0c0c0c;border:1px solid #161616;border-radius:10px;padding:11px 13px"><div style="font-size:12px;font-weight:800;color:#cfd8e6;margin-bottom:4px">⚖️ Breakeven &amp; Coût</div><div class="muted" style="font-size:11px;line-height:1.55">Breakeven = cours du titre pour rentrer dans tes frais. Coût = prix d’un contrat (= 100 actions). « Si cible » = gain si l’objectif est atteint.</div></div>
    <div style="flex:1 1 30%;min-width:235px;background:#0c0c0c;border:1px solid #161616;border-radius:10px;padding:11px 13px"><div style="font-size:12px;font-weight:800;color:#cfd8e6;margin-bottom:4px">📏 Move attendu</div><div class="muted" style="font-size:11px;line-height:1.55">Amplitude anticipée par le marché d’ici l’échéance (±%), déduite de l’IV. Repère pour juger si une cible est réaliste.</div></div>
    <div style="flex:1 1 30%;min-width:235px;background:#0c0c0c;border:1px solid #161616;border-radius:10px;padding:11px 13px"><div style="font-size:12px;font-weight:800;color:#cfd8e6;margin-bottom:4px">🛡️ PUTS</div><div class="muted" style="font-size:11px;line-height:1.55">Options de baisse : se protéger (couverture) ou parier sur une chute. Affichées sur les titres en signal « Éviter ».</div></div>
  </div><div class="src">Repère pédagogique — analyse éducative, jamais un conseil. Aucun ordre passé (lecture seule).</div></div>
  <div class="foot">Contrats issus des <b>vraies chaînes yfinance</b> · grecs Black-Scholes maison · Option Quality /100 · ⛔ analyse only</div>
</div>
<script>
const C={g:'#22C55E',r:'#EF4444',o:'#FF8C32',gold:'#F5B45B',blue:'#38BDF8',vio:'#A78BFA',cy:'#34D399',yl:'#FFB23F'};
function eu(s){return s?s.slice(8,10)+'/'+s.slice(5,7)+'/'+s.slice(2,4):''}
function fmt(n){return n==null?'—':(Math.abs(n)>=1e6?(n/1e6).toFixed(1)+'M':Math.abs(n)>=1e3?(n/1e3).toFixed(1)+'k':n)}
function qc(q){return q>=78?C.g:q>=62?C.gold:q>=50?C.yl:C.r}
function popc(p){return p>=50?C.g:p>=38?C.gold:C.r}
function dgc(x){return x==='Faible'?C.g:x==='Modéré'?C.gold:x==='Élevé'?C.yl:C.r}
const FIT={leaps:['✅ LEAPS',C.g],swing:['⚡ Swing',C.blue],tact:['⚠️ Tactique',C.yl],avoid:['❌ Éviter',C.r]};
function fitOf(c){const d=Math.abs(c.delta||0);
  if((c.quality||0)<50||c.danger==='Extrême'||(c.spread!=null&&c.spread>10))return'avoid';
  if(c.bucket==='long'&&d>=0.6&&(c.quality||0)>=66)return'leaps';
  if(c.bucket==='moyen')return'swing';
  if(c.bucket==='court')return'tact';
  return'swing';}
function row(c){const f=FIT[c.fit];return `<tr onclick="location.href='/titre/${c.sym}'">
  <td class="sym">${c.sym}</td>
  <td>${eu(c.exp)} <span class="muted">${c.dte}j</span></td>
  <td>$${c.strike}</td>
  <td style="font-weight:800;color:${qc(c.quality)}">${c.quality!=null?c.quality:'—'}<span class="muted" style="font-size:9px">/100</span></td>
  <td>${c.delta}</td>
  <td style="font-weight:700;color:${popc(c.pop)}">${c.pop}%</td>
  <td style="font-weight:700;color:${dgc(c.danger)}">${c.danger}</td>
  <td class="muted">${fmt(c.oi)}</td><td class="muted">${fmt(c.vol)}</td>
  <td class="muted">${c.spread!=null?c.spread+'%':'—'}</td>
  <td class="muted">${c.iv}%</td>
  <td>$${fmt(c.cost)}</td>
  <td class="muted">$${c.be}</td>
  <td class="muted">si $${c.tgt} <span class="${c.pot>=0?'up':'dn'}">${c.pot>=0?'+':''}${c.pot}%</span></td>
  <td><span class="fit" style="color:${f[1]};border:1px solid ${f[1]}66;background:${f[1]}14">${f[0]}</span></td></tr>`;}
const HEAD=['Ticker','Échéance','Strike','Qualité','Δ','POP','Danger','OI','Vol','Spread','IV','Coût','Breakeven','Si cible','Verdict'];
function section(col,title,list){return `<div class="panel"><div class="ph" style="background:linear-gradient(90deg,${col}26,transparent 70%)">${title}<span class="cnt">${list.length} contrats</span></div><div class="swipe">← glisse le tableau pour voir toutes les colonnes →</div><div class="tscroll"><table><thead><tr>${HEAD.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${list.map(row).join('')||'<tr><td colspan="15" class="muted" style="padding:16px">chaînes en calcul (~1-2 min)…</td></tr>'}</tbody></table></div></div>`;}
async function load(){
  let s={};try{s=await fetch('/scan').then(r=>r.json());}catch(e){}
  const board=(s.options_board||[]).filter(c=>c.type==='CALL');
  board.forEach(c=>c.fit=fitOf(c));
  const puts=(s.options_board||[]).filter(c=>c.type==='PUT');
  puts.forEach(c=>c.fit=fitOf(c));
  const m=s.market||{};const SE={pre:'🌅 avant-bourse',open:'🟢 séance',after:'🌙 après-bourse',closed:'🌑 fermé'};
  document.getElementById('hdate').textContent=new Date().toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long',year:'numeric'}).toUpperCase()+' · '+(SE[m.session]||'')+' '+(m.et||'');
  document.getElementById('hsrc').innerHTML='Source : <b>chaînes options réelles</b> · '+board.length+' contrats analysés';
  // featured = meilleure qualité
  const best=[...board].sort((a,b)=>(b.quality||0)-(a.quality||0))[0];
  if(best){const f=FIT[best.fit];document.getElementById('feat').innerHTML=`<div style="min-width:140px"><div style="font-size:10px;color:#F5B45B;font-weight:800;letter-spacing:1px">⭐ MEILLEURE OPTION DU JOUR</div><div class="big">${best.sym}</div><div class="muted" style="font-size:12px">${(best.bucket||'').toUpperCase()} · ${eu(best.exp)} · strike $${best.strike}</div></div>
    <div style="text-align:center"><div style="font-size:34px;font-weight:900;color:${qc(best.quality)}">${best.quality}<span style="font-size:14px;color:#777">/100</span></div><div class="muted" style="font-size:10px">QUALITÉ</div></div>
    <div style="flex:1;min-width:220px;font-size:12px;line-height:1.8"><div>POP <b style="color:${popc(best.pop)}">${best.pop}%</b> · danger <b style="color:${dgc(best.danger)}">${best.danger}</b> · delta <b>${best.delta}</b></div><div>coût <b>$${fmt(best.cost)}</b> · breakeven <b>$${best.be}</b> · si $${best.tgt} → <b class="${best.pot>=0?'up':'dn'}">${best.pot>=0?'+':''}${best.pot}%</b></div><div>OI ${fmt(best.oi)} · vol ${fmt(best.vol)} · IV ${best.iv}% · <span class="fit" style="color:${f[1]};border:1px solid ${f[1]}66">${f[0]}</span></div><div>proba cible <b style="color:${popc(best.p_tgt)}">${best.p_tgt}%</b> · move attendu <b>±${best.em_pct}%</b> · érosion théta <b style="color:${best.theta_burn>1.5?C.r:best.theta_burn>0.8?C.yl:C.g}">${best.theta_burn}%/j</b></div></div>`;}
  // stats
  const cnt=t=>board.filter(c=>c.fit===t).length;
  document.getElementById('stats').innerHTML=[['leaps','LEAPS Core',C.g],['swing','Swing',C.blue],['tact','Tactique',C.yl],['avoid','À éviter',C.r]].map(([k,l,c])=>`<div class="stat" style="border-color:${c}33"><div class="n" style="color:${c}">${cnt(k)}</div><div class="l">${l}</div></div>`).join('');
  // stockage global + briques avancees
  const spotMap={};(s.rows||[]).forEach(r=>{if(r&&r.symbol)spotMap[r.symbol]=r.price;});
  window.__board=board;window.__puts=puts;window.__spot=spotMap;
  buildAlerts();buildFilterbar();renderSections();buildSimulator();buildStrategies();
}
function sortFn(k){return k==='pop'?(a,b)=>(b.pop||0)-(a.pop||0):k==='cost'?(a,b)=>(a.cost||0)-(b.cost||0):k==='danger'?(a,b)=>(a.danger_n||0)-(b.danger_n||0):k==='dte'?(a,b)=>(a.dte||0)-(b.dte||0):(a,b)=>(b.quality||0)-(a.quality||0);}
function renderSections(){
  const minQ=window.optMinQ||0, sk=window.optSort||'quality', filt=window.optFilter||'all';
  const all=window.__board||[], pu=window.__puts||[];
  const sec=(col,title,list)=>section(col,title,[...list].filter(c=>(c.quality||0)>=minQ).sort(sortFn(sk)));
  let html='';
  if(filt==='all') html=sec(C.g,'💎 LEAPS CORE · 6-18 mois · cœur de stratégie',all.filter(c=>c.bucket==='long'))
    +sec(C.blue,'⚡ SWING · ~3 mois · mouvement rapide',all.filter(c=>c.bucket==='moyen'))
    +sec(C.yl,'🎲 TACTIQUE · 1-2 mois · théta violent',all.filter(c=>c.bucket==='court'))
    +(pu.length?sec(C.r,'🛡️ PUTS · couverture / pari baissier',pu):'');
  else if(filt==='leaps') html=sec(C.g,'💎 LEAPS CORE',all.filter(c=>c.bucket==='long'));
  else if(filt==='swing') html=sec(C.blue,'⚡ SWING',all.filter(c=>c.bucket==='moyen'));
  else if(filt==='tact') html=sec(C.yl,'🎲 TACTIQUE',all.filter(c=>c.bucket==='court'));
  else if(filt==='puts') html=sec(C.r,'🛡️ PUTS',pu);
  document.getElementById('sections').innerHTML=html||'<div class="panel"><div class="muted" style="padding:18px">Aucun contrat ne correspond au filtre.</div></div>';
}
function buildFilterbar(){
  const fb=document.getElementById('filterbar'); if(fb.dataset.on)return; fb.dataset.on='1';
  const chip=(v,l)=>`<button class="chip" data-f="${v}" onclick="setFilt('${v}')">${l}</button>`;
  fb.innerHTML=`<div class="fbar"><div class="fgrp">${chip('all','Tous')}${chip('leaps','💎 LEAPS')}${chip('swing','⚡ Swing')}${chip('tact','🎲 Tactique')}${chip('puts','🛡️ Puts')}</div>`
    +`<label class="fsel">Tri <select onchange="window.optSort=this.value;renderSections()"><option value="quality">Qualité</option><option value="pop">POP</option><option value="cost">Coût ↑</option><option value="danger">Danger ↑</option><option value="dte">Échéance</option></select></label>`
    +`<label class="fsel">Qualité min <select onchange="window.optMinQ=+this.value;renderSections()"><option value="0">Toutes</option><option value="50">≥50</option><option value="62">≥62</option><option value="78">≥78</option></select></label></div>`;
  setFilt('all');
}
function setFilt(v){window.optFilter=v;document.querySelectorAll('#filterbar .chip').forEach(b=>b.classList.toggle('on',b.dataset.f===v));renderSections();}
function buildSimulator(){
  const sim=document.getElementById('simulator');
  const all=[...(window.__board||[]),...(window.__puts||[])];
  if(!all.length){sim.innerHTML='';return;}
  if(!sim.dataset.on){sim.dataset.on='1';
    sim.innerHTML=`<div class="panel" style="border-color:#34D39933"><div class="ph" style="background:linear-gradient(90deg,#34D39926,transparent 70%)"><span style="color:#fff">🧮 SIMULATEUR DE GAIN · à l'échéance</span></div><div style="padding:14px"><div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px"><label class="fsel">Contrat <select id="simSel" onchange="runSim()"></select></label><span id="simInfo" class="muted" style="font-size:11px"></span></div><div id="simOut"></div><div class="src">P&amp;L estimé À L'ÉCHÉANCE (valeur intrinsèque, hors valeur-temps) · 1 contrat = 100 actions · indicatif.</div></div></div>`;
  }
  const sel=document.getElementById('simSel'); const prev=sel.value;
  window.__simAll=all;
  sel.innerHTML=all.map((c,i)=>`<option value="${i}">${c.sym} ${c.type} $${c.strike} · ${eu(c.exp)} · Q${c.quality}</option>`).join('');
  if(prev!==''&&+prev<all.length)sel.value=prev;
  runSim();
}
function payoffSVG(c,spot){
  const isCall=c.type==='CALL',mid=c.mid||(c.cost?c.cost/100:0),K=c.strike;
  const lo=spot*0.72,hi=spot*1.38,N=64,W=340,H=160,padT=12,padB=22;
  const X=S=>((S-lo)/(hi-lo))*W;
  const xs=[],ys=[];for(let i=0;i<=N;i++){const S=lo+(hi-lo)*i/N;const val=isCall?Math.max(S-K,0):Math.max(K-S,0);xs.push(S);ys.push((val-mid)*100);}
  const ymin=Math.min(...ys,0),ymax=Math.max(...ys,0),yr=(ymax-ymin)||1;
  const Y=v=>padT+(1-(v-ymin)/yr)*(H-padT-padB);
  const be=isCall?K+mid:K-mid,beX=X(be),zY=Y(0),spotX=X(spot);
  const red=[],green=[];xs.forEach((S,i)=>{(ys[i]<0?red:green).push([X(S),Y(ys[i])]);});
  if(red.length)red.push([beX,zY]);if(green.length)green.unshift([beX,zY]);
  const poly=(a,col)=>a.length>1?`<polyline points="${a.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ')}" fill="none" stroke="${col}" stroke-width="2.5" vector-effect="non-scaling-stroke"/>`:'';
  const cx=x=>Math.min(Math.max(x,16),W-16);
  return `<div style="background:#0a0a0a;border:1px solid #161616;border-radius:10px;padding:12px 10px 4px;margin-bottom:12px">
    <div class="muted" style="font-size:9px;letter-spacing:.5px;text-transform:uppercase;margin:0 4px 4px">Profil de gain à l'échéance · ${isCall?'CALL':'PUT'} $${K}</div>
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:160px">
      <line x1="0" y1="${zY.toFixed(1)}" x2="${W}" y2="${zY.toFixed(1)}" stroke="#444" stroke-width="1" stroke-dasharray="4 3"/>
      <line x1="${beX.toFixed(1)}" y1="${padT}" x2="${beX.toFixed(1)}" y2="${H-padB}" stroke="#F5B45B" stroke-width="1" stroke-dasharray="3 3" opacity=".55"/>
      <line x1="${spotX.toFixed(1)}" y1="${padT}" x2="${spotX.toFixed(1)}" y2="${H-padB}" stroke="#fff" stroke-width="1" stroke-dasharray="2 3" opacity=".4"/>
      ${poly(red,'#EF4444')}${poly(green,'#22C55E')}
      <text x="${cx(beX).toFixed(0)}" y="${H-6}" fill="#F5B45B" font-size="9" text-anchor="middle">BE $${be.toFixed(0)}</text>
      <text x="${cx(spotX).toFixed(0)}" y="${(padT+9).toFixed(0)}" fill="#bbb" font-size="9" text-anchor="middle">cours $${spot.toFixed(0)}</text>
    </svg>
    <div class="muted" style="font-size:9.5px;text-align:center;padding:2px 4px 6px">🟥 perte · 🟩 gain · ligne or = breakeven · ligne blanche = cours actuel</div>
  </div>`;
}
function runSim(){
  const all=window.__simAll||[]; const sel=document.getElementById('simSel'); if(!sel||!all.length)return;
  const c=all[+sel.value||0]; const spot=(window.__spot||{})[c.sym]||c.strike; const mid=c.mid||(c.cost?c.cost/100:0); const isCall=c.type==='CALL';
  document.getElementById('simInfo').textContent=`cours ≈ $${spot} · prime $${mid} · coût $${fmt(c.cost)} · breakeven $${c.be}`;
  const steps=[-0.2,-0.1,-0.05,0,0.05,0.1,0.15,0.2,0.3];
  const rws=steps.map(p=>{const S=+(spot*(1+p)).toFixed(2);const val=isCall?Math.max(S-c.strike,0):Math.max(c.strike-S,0);const pnl=Math.round((val-mid)*100);const ret=mid?Math.round((val-mid)/mid*100):0;const hl=p===0?'background:rgba(255,255,255,.04)':'';return `<tr style="${hl}"><td>$${S} <span class="muted">(${p>=0?'+':''}${Math.round(p*100)}%)</span></td><td>$${val.toFixed(2)}</td><td class="${pnl>=0?'up':'dn'}">${pnl>=0?'+':''}$${pnl}</td><td class="${ret>=0?'up':'dn'}">${ret>=0?'+':''}${ret}%</td></tr>`;}).join('');
  document.getElementById('simOut').innerHTML=payoffSVG(c,spot)+`<div class="tscroll"><table><thead><tr><th>Cours du titre</th><th>Valeur option</th><th>Gain / perte (1 contrat)</th><th>Rendement</th></tr></thead><tbody>${rws}</tbody></table></div>`;
}
function buildStrategies(){
  const el=document.getElementById('strategies'); if(el.dataset.on)return; el.dataset.on='1';
  const card=(ic,t,col,principe,profil,quand)=>`<div style="flex:1 1 45%;min-width:255px;background:#0c0c0c;border:1px solid ${col}33;border-radius:11px;padding:13px 15px"><div style="font-size:13px;font-weight:800;color:${col};margin-bottom:6px">${ic} ${t}</div><div style="font-size:11.5px;line-height:1.65"><b>Principe :</b> ${principe}<br><b>Profil :</b> ${profil}<br><b>Quand :</b> ${quand}</div></div>`;
  el.innerHTML=`<div class="panel" style="border-color:#F5B45B33"><div class="ph" style="background:linear-gradient(90deg,#F5B45B26,transparent 70%)"><span style="color:#fff">🧩 STRATÉGIES OPTIONS · au-delà de l'achat simple</span></div><div style="display:flex;flex-wrap:wrap;gap:10px;padding:14px">`
    +card('📈','Achat de CALL',C.g,'Acheter un call pour profiter de la hausse.','Risque limité à la prime · gain élevé si ça monte.','Forte conviction haussière, tendance confirmée.')
    +card('🪜','Bull Call Spread',C.blue,'Acheter un call + en vendre un plus haut.','Coût réduit (~30-50%) · gain plafonné au strike vendu.','Hausse modérée, réduire le coût et le théta.')
    +card('🛡️','Protective Put',C.cy,'Détenir 100 actions + acheter un put.','Assurance : perte plancher · coût = prime du put.','Protéger une position contre une chute.')
    +card('💰','Covered Call',C.gold,'Détenir 100 actions + vendre un call.','Encaisse une prime · upside plafonné au strike vendu.','Marché calme, générer du revenu.')
    +card('🏦','Cash-Secured Put',C.yl,'Vendre un put en gardant le cash de côté.','Encaisse une prime · achat imposé si ça baisse.','Vouloir entrer sur un titre à un prix plus bas.')
    +card('🦅','Bear Put Spread',C.r,'Acheter un put + en vendre un plus bas.','Coût réduit · gain plafonné · pari baissier.','Baisse modérée attendue, couverture peu chère.')
    +`</div><div class="src">Repère pédagogique — analyse éducative, jamais un conseil. Aucun ordre passé (lecture seule).</div></div>`;
}
function buildAlerts(){
  const all=[...(window.__board||[]),...(window.__puts||[])]; const el=document.getElementById('alerts'); if(!all.length){el.innerHTML='';return;}
  const a=[];
  [...all].filter(c=>(c.quality||0)>=78&&(c.danger==='Faible'||c.danger==='Modéré')&&(c.pop||0)>=50).sort((x,y)=>(y.quality||0)-(x.quality||0)).slice(0,2)
    .forEach(c=>a.push(['🚀','Setup exceptionnel',C.g,`${c.sym} ${c.type} $${c.strike} — qualité ${c.quality}, POP ${c.pop}%, danger ${c.danger}`]));
  const cheap=[...all].filter(c=>(c.quality||0)>=62).sort((x,y)=>(x.cost||0)-(y.cost||0))[0];
  if(cheap)a.push(['💸','Solide & abordable',C.blue,`${cheap.sym} ${cheap.type} $${cheap.strike} — qualité ${cheap.quality} pour seulement $${fmt(cheap.cost)}`]);
  const safe=[...all].filter(c=>c.danger==='Faible').sort((x,y)=>(y.pop||0)-(x.pop||0))[0];
  if(safe)a.push(['🛟','Meilleure proba / risque',C.cy,`${safe.sym} ${safe.type} $${safe.strike} — POP ${safe.pop}%, danger Faible`]);
  const trap=[...all].filter(c=>(c.quality||0)<50||c.danger==='Extrême').sort((x,y)=>(x.quality||0)-(y.quality||0))[0];
  if(trap)a.push(['⚠️','Piège à éviter',C.r,`${trap.sym} ${trap.type} $${trap.strike} — qualité ${trap.quality}, danger ${trap.danger}`]);
  if(!a.length){el.innerHTML='';return;}
  el.innerHTML=`<div class="panel" style="border-color:#FF8C3233"><div class="ph" style="background:linear-gradient(90deg,#FF8C3226,transparent 70%)"><span style="color:#fff">🔔 ALERTES OPTIONS DU JOUR</span><span class="cnt">${a.length} repérées</span></div><div style="display:flex;flex-wrap:wrap;gap:9px;padding:14px">`
    +a.map(x=>`<div style="flex:1 1 45%;min-width:250px;background:#0c0c0c;border:1px solid ${x[2]}33;border-radius:10px;padding:11px 13px"><div style="font-size:12px;font-weight:800;color:${x[2]};margin-bottom:3px">${x[0]} ${x[1]}</div><div style="font-size:11.5px;line-height:1.5">${x[3]}</div></div>`).join('')
    +`</div><div class="src">Détecté automatiquement depuis le board du jour (qualité, POP, danger, coût) · indicatif.</div></div>`;
}
load();setInterval(load,20000);
</script></body></html>"""


@app.route('/options-desk')
def options_desk_alias():
    return PAGE_OPTIONS_DESK


# ─── MA PAGE (espace perso : favoris + niveaux clés, sauvegardé sur l'appareil) ──
PAGE_ME = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Trading Desk"><meta name="mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0b0e14"><link rel="apple-touch-icon" href="/static/icon-180.png"><link rel="manifest" href="/manifest.webmanifest">
<title>Ma Page · Trading Desk</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(1200px 600px at 50% -10%,#15130a,#070707 60%);color:#e8edf5;font-family:'Segoe UI',system-ui,sans-serif;padding:20px;font-variant-numeric:tabular-nums}
.wrap{max-width:1320px;margin:0 auto}
.htop{border:2px solid #F5B45B55;border-radius:18px;padding:18px 24px;background:linear-gradient(135deg,#15110a,#0b0b0b);display:flex;align-items:center;gap:18px;flex-wrap:wrap;box-shadow:0 0 40px rgba(245,180,91,.08)}
.htitle{font-size:32px;font-weight:900;letter-spacing:1px;background:linear-gradient(180deg,#FFE9B8,#FF8C32);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hsub{color:#8794ab;font-weight:700;font-size:13px;margin-top:4px}
.hmeta{margin-left:auto;text-align:right;font-size:11px;color:#8794ab;line-height:1.7}.hmeta b{color:#F5B45B}
.addbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:16px 0 6px;background:#101010;border:1px solid #1c1c24;border-radius:12px;padding:12px 14px}
.addbar input{background:#0a0a0a;border:1px solid #1c1c24;color:#eaf0fa;border-radius:9px;padding:9px 13px;font-size:14px;flex:1;min-width:150px}
.addbar button{background:rgba(245,180,91,.14);border:1px solid #F5B45B66;color:#F5B45B;border-radius:9px;padding:9px 16px;font-weight:800;font-size:13px;cursor:pointer;white-space:nowrap}
.sug{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 4px}
.sug b{color:#6b7689;font-size:11px;align-self:center;margin-right:2px}
.sug span{background:#0e0e0e;border:1px solid #1c1c24;color:#cfd8e6;padding:5px 10px;border-radius:8px;font-size:12px;font-weight:700;cursor:pointer}
.sug span:hover{border-color:#F5B45B66;color:#F5B45B}
.favgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px;margin-top:14px}
.fav{border:1px solid #1c1c24;border-radius:14px;background:linear-gradient(165deg,#131313,#0b0b0b);padding:15px 16px;animation:fu .4s ease both}
@keyframes fu{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.fav .top{display:flex;align-items:center;gap:10px}
.fav .tk{font-size:21px;font-weight:900;color:#fff;text-decoration:none}
.fav .px{font-size:16px;font-weight:800}
.fav .rm{margin-left:auto;background:#1a1212;border:1px solid #EF444444;color:#EF4444;border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:15px;font-weight:800;flex-shrink:0}
.badge{display:inline-block;font-size:11px;font-weight:800;padding:3px 9px;border-radius:7px}
.lvls{display:grid;grid-template-columns:1fr 1fr 1fr;gap:7px;margin-top:12px}
.lvl{background:#0c0c0c;border:1px solid #161616;border-radius:9px;padding:8px 10px;text-align:center}
.lvl .l{font-size:8.5px;letter-spacing:.5px;text-transform:uppercase;color:#6b7689}
.lvl .v{font-size:14px;font-weight:800;margin-top:2px}
.rrbar{display:flex;height:7px;border-radius:5px;overflow:hidden;margin-top:12px;background:#0a0a0a}
.fav .acts{display:flex;gap:7px;margin-top:12px}
.fav .acts a{flex:1;text-align:center;text-decoration:none;font-size:12px;font-weight:700;padding:8px;border-radius:9px;border:1px solid #1c1c24;color:#cfd8e6}
.fav .acts a:hover{border-color:#F5B45B55;color:#F5B45B}
.muted{color:#6b7689}.up{color:#22C55E;font-weight:700}.dn{color:#EF4444;font-weight:700}
.empty{border:1.5px dashed #2a2a33;border-radius:16px;padding:40px 22px;text-align:center;margin-top:16px}
.empty h3{font-size:18px;color:#F5B45B;margin-bottom:8px}
.foot{text-align:center;color:#5b6678;font-size:11px;margin:22px 0 6px}.foot b{color:#F5B45B}
.back{position:fixed;top:13px;left:13px;background:#111;border:1px solid #F5B45B55;color:#F5B45B;padding:7px 13px;border-radius:9px;text-decoration:none;font-size:12px;font-weight:700;z-index:9}
</style>
<style id="m-fix">
html,body{overflow-x:hidden;max-width:100%}
@media(max-width:640px){
  .grid,.cols,.herorow,.hero,.panorama,.layout,.heat,.secgrid,.poster{grid-template-columns:1fr!important}
  [style*="grid-template-columns"]{grid-template-columns:1fr!important}
  .top3{grid-template-columns:1fr 1fr 1fr!important}
  .plan{grid-template-columns:1fr 1fr!important}
  td,th{padding-left:9px!important;padding-right:9px!important}
  table{font-size:11.5px!important}
  .wrap,.daily{padding-left:12px!important;padding-right:12px!important}
  .phead-top{padding:15px 16px!important}
  .htitle{font-size:25px!important}
  .hmeta{margin-left:0!important;text-align:left!important}
  .back{padding:5px 9px!important;font-size:11px!important}
  .lvls{grid-template-columns:1fr 1fr 1fr!important}
}
</style>
<style id="nav-css">
#gnav{position:sticky;top:0;z-index:1000;background:rgba(10,10,12,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid #1c1c24}
.gnav-in{max-width:1500px;margin:0 auto;display:flex;align-items:center;gap:12px;padding:8px 16px;flex-wrap:wrap}
.gnav-links{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
#gnav a{color:#aab4c4;text-decoration:none;font-size:13px;font-weight:700;padding:7px 12px;border-radius:9px;white-space:nowrap}
#gnav a:hover{background:#ffffff0d;color:#fff}
#gnav a.act{background:rgba(245,180,91,.14);color:#F5B45B}
.gnav-search{display:flex;gap:6px;align-items:center}
.gnav-search input{background:#0e0e0e;border:1px solid #1c1c24;color:#e8edf5;border-radius:9px;padding:7px 11px;font-size:13px;width:210px;max-width:46vw}
.gnav-search button{background:rgba(245,180,91,.12);border:1px solid #F5B45B55;color:#F5B45B;border-radius:9px;padding:7px 13px;font-weight:800;cursor:pointer}
.back{display:none!important}
@media(max-width:640px){.gnav-in{padding:6px 10px;gap:8px}.gnav-links{order:2;overflow-x:auto;flex-wrap:nowrap}#gnav a{font-size:12px;padding:6px 9px}.gnav-search{order:1;width:100%}.gnav-search input{flex:1;max-width:none;width:auto}}
/* === systeme de design premium (toutes pages) === */
body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;background:radial-gradient(1200px 780px at 50% -8%,#0d0d12,#050506 60%)!important}
.panel,.scard,.card,.pcard,.seccard,.tile,.kpi,.fav,.pchip,.stat{background:linear-gradient(165deg,#16171c,#0d0e12)!important;border:1px solid rgba(255,255,255,.08)!important;border-radius:16px!important}
.panel,.scard,.card,.pcard,.fav,.seccard{transition:border-color .2s ease,box-shadow .2s ease,transform .18s ease!important}
.panel:hover,.scard:hover,.card:hover,.fav:hover,.seccard:hover{border-color:rgba(255,140,50,.32)!important;box-shadow:0 0 26px rgba(255,140,50,.10)!important}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#26262e;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:#3a3a44}
::selection{background:rgba(255,140,50,.28);color:#fff}
/* bandeau KPI (cockpit + autres) */
.kpiband{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin:14px 0}
.kc{background:linear-gradient(165deg,#16171c,#0d0e12);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:11px 12px;min-width:0}
.kc .kt{font-size:8.5px;letter-spacing:.6px;text-transform:uppercase;color:#8794ab;display:flex;align-items:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kv{font-size:17px;font-weight:900;margin-top:3px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .ks{font-size:9px;color:#6b7689;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kbar{height:5px;border-radius:4px;background:#0a0a0a;overflow:hidden;margin-top:6px}
.kc .kbar>div{height:100%;border-radius:4px}
@media(max-width:760px){.kpiband{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:44%;overflow-x:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;padding-bottom:2px}.kc{scroll-snap-align:start}}
/* navigation mobile sticky en bas */
#mnav{display:none}
@media(max-width:760px){
  #mnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:1200;background:rgba(8,8,10,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid rgba(255,255,255,.09);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
  #mnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;text-decoration:none;color:#8794ab;font-size:9.5px;font-weight:700;padding:5px 2px;border-radius:12px}
  #mnav a .mi{font-size:19px;line-height:1}
  #mnav a.act{color:#FF8C32;background:rgba(255,140,50,.13)}
  body{padding-bottom:68px!important}
  #gnav .gnav-links{display:none}
}
</style>
<script>
(function(){var L=[['/','🔱 Cockpit'],['/ma-page','⭐ Ma Page'],['/watchlist','📋 Watchlist'],['/entreprises','🏢 Entreprises'],['/options','💎 Options']];
function build(){if(document.getElementById('gnav'))return;var p=location.pathname;
var links=L.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'>'+x[1]+'</a>';}).join('');
var nav=document.createElement('nav');nav.id='gnav';
nav.innerHTML='<div class="gnav-in"><div class="gnav-links">'+links+'</div><form class="gnav-search" onsubmit="return gnavGo(event)"><input id="gnavq" placeholder="Rechercher un titre… (ex: NVDA)" autocomplete="off"><button type="submit">→</button></form></div>';
document.body.insertBefore(nav,document.body.firstChild);
var M=[['/','🏠','Cockpit'],['/watchlist','📡','Scan'],['/ma-page','⭐','Ma Page'],['/options','💎','Options'],['/entreprises','🏢','Titres']];
var mn=document.createElement('nav');mn.id='mnav';
mn.innerHTML=M.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'><span class="mi">'+x[1]+'</span>'+x[2]+'</a>';}).join('');
document.body.appendChild(mn);}
window.gnavGo=function(e){e.preventDefault();var v=(document.getElementById('gnavq').value||'').trim().toUpperCase();if(v)location.href='/titre/'+encodeURIComponent(v);return false;};
if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();})();
</script></head><body>
<a class="back" href="/">← cockpit</a>
<div class="wrap">
  <div class="htop"><span style="font-size:36px">⭐</span>
    <div><div class="htitle">MON ESPACE</div><div class="hsub">Tes titres favoris · niveaux clés · sauvegardés sur cet appareil</div></div>
    <div class="hmeta"><div><b id="cnt">…</b> suivis</div><div>Cours <b id="src">…</b></div><div style="font-size:10px;color:#5b6678;margin-top:3px">Analyse éducative — pas un conseil · ⛔ aucun ordre</div></div>
  </div>
  <div class="addbar">
    <input id="addq" placeholder="Ajouter un titre (ex: NVDA, AAPL…)" autocomplete="off" onkeydown="if(event.key==='Enter')addInput()">
    <button onclick="addInput()">+ Ajouter</button>
  </div>
  <div class="sug" id="sug"></div>
  <div id="favs"></div>
  <div class="foot">⭐ Tes favoris sont enregistrés <b>uniquement sur cet appareil</b> (rien n'est envoyé). Niveaux = plan technique du moteur · cours yfinance différé sur le cloud.</div>
</div>
<script>
const G='#22C55E',R='#EF4444',GOLD='#F5B45B',BLUE='#38BDF8',CY='#34D399';
const SUG=['NVDA','AAPL','TSLA','MSFT','AMD','META','GOOGL','AMZN','PLTR','AVGO'];
function vcol(v){return v==='BUY'?G:v==='WATCH'?GOLD:v==='WAIT'?BLUE:v==='AVOID'?R:'#8794ab'}
function vfr(v){return {BUY:'✅ ACHAT',WATCH:'👀 SURVEILLER',WAIT:'⏳ ATTENTE',AVOID:'🛑 ÉVITER'}[v]||v||'—'}
function getFavs(){try{return JSON.parse(localStorage.getItem('myFavs')||'[]')}catch(e){return[]}}
function setFavs(a){localStorage.setItem('myFavs',JSON.stringify(a));render();}
function addFav(s){s=(s||'').trim().toUpperCase().replace(/[^A-Z0-9.]/g,'');if(!s)return;var a=getFavs();if(!a.includes(s)){a.unshift(s);setFavs(a);}}
function removeFav(s){setFavs(getFavs().filter(x=>x!==s));}
function getNotes(){try{return JSON.parse(localStorage.getItem('myNotes')||'{}')}catch(e){return{}}}
function openNote(s){window.__editing=s;render();const t=document.getElementById('nt_'+s);if(t)t.focus();}
function cancelNote(){window.__editing=null;render();}
function saveNote(s){const t=document.getElementById('nt_'+s);const v=t?t.value:'';const n=getNotes();if(v.trim())n[s]=v.trim();else delete n[s];localStorage.setItem('myNotes',JSON.stringify(n));window.__editing=null;render();}
function addInput(){var i=document.getElementById('addq');addFav(i.value);i.value='';}
let DATA={detail:{},quotes:{},rt:false};
function lvl(l,v,c){return `<div class="lvl"><div class="l">${l}</div><div class="v" style="color:${c||'#e6edf7'}">${v!=null?'$'+v:'—'}</div></div>`}
function spark(arr){
  if(!arr||arr.length<3)return '';
  const a=arr.slice(-30),min=Math.min(...a),max=Math.max(...a),rng=(max-min)||1,W=300,H=46;
  const pts=a.map((v,i)=>`${(i/(a.length-1)*W).toFixed(1)},${(H-2-((v-min)/rng)*(H-4)).toFixed(1)}`).join(' ');
  const up=a[a.length-1]>=a[0],col=up?'#22C55E':'#EF4444';
  return `<div style="margin-top:12px"><div class="muted" style="font-size:8.5px;letter-spacing:.5px;text-transform:uppercase;margin-bottom:3px">Tendance 30 jours</div><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:46px"><polyline points="${pts}" fill="none" stroke="${col}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg></div>`;
}
function alertOf(sym){
  const d=(DATA.detail||{})[sym]; if(!d)return null;
  const x=(DATA.quotes||{})[sym]; const price=x?x.last:d.price; const p=d.plan||{};
  if(price==null)return null;
  if(p.stop&&price<=p.stop)return['🛑','STOP TOUCHÉ','#EF4444','Cours sous ton stop — sortie selon le plan.'];
  if(p.tp2&&price>=p.tp2)return['🎯','CIBLE 2 ATTEINTE','#22C55E','Objectif final atteint — pense à sécuriser.'];
  if(p.tp1&&price>=p.tp1)return['🎯','CIBLE 1 ATTEINTE','#22C55E','Sécurise une partie / remonte ton stop.'];
  if(p.stop&&price<=p.stop*1.025)return['⚠️','PROCHE DU STOP','#FFB23F','À moins de 2,5% du stop — surveille.'];
  if(p.tp1&&price>=p.tp1*0.975)return['👀','PROCHE DE LA CIBLE 1','#38BDF8','Cible 1 en approche.'];
  return null;
}
function card(sym){
  const d=(DATA.detail||{})[sym], x=(DATA.quotes||{})[sym];
  const price=x?x.last:(d?d.price:null), chg=(x&&x.change!=null)?x.change:(d?d.change:null);
  const head=`<div class="top"><a class="tk" href="/titre/${sym}">${sym}</a>${price!=null?`<span class="px">$${price}</span>`:''}${chg!=null?`<span class="${chg>=0?'up':'dn'}" style="font-size:13px">${chg>=0?'+':''}${chg}%</span>`:''}<button class="rm" onclick="removeFav('${sym}')" title="Retirer">×</button></div>`;
  if(!d){return `<div class="fav">${head}<div class="muted" style="font-size:12px;margin-top:12px;line-height:1.6">Hors des 57 titres suivis en direct.<br>Ouvre la fiche pour l'analyse technique + fondamentale complète.</div><div class="acts"><a href="/titre/${sym}">📄 Voir la fiche complète</a></div></div>`;}
  const p=d.plan||{}, sc=d.score||0, scol=sc>=72?G:sc>=55?GOLD:R;
  const toStop=(price&&p.stop)?((price-p.stop)/price*100):null;
  const toTp2=(price&&p.tp2)?((p.tp2-price)/price*100):null;
  const meta=`<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:11px"><span class="badge" style="color:${scol};border:1px solid ${scol}55">Score ${sc}/100</span>${d.grade?`<span class="badge muted" style="border:1px solid #2a2a33">Grade ${d.grade}</span>`:''}<span class="badge" style="color:${vcol(d.verdict)};background:${vcol(d.verdict)}1a">${vfr(d.verdict)}</span></div>`;
  const lvls=`<div class="lvls">${lvl('Entrée',p.entry,GOLD)}${lvl('Stop',p.stop,R)}${lvl('Résist.',p.resistance,BLUE)}${lvl('Cible 1',p.tp1,G)}${lvl('Cible 2',p.tp2,G)}${lvl('RSI',d.rsi!=null?d.rsi:null,'#cfd8e6').replace('$','')}</div>`;
  const dist=(toStop!=null&&toTp2!=null)?`<div class="muted" style="font-size:11px;margin-top:10px">🛡️ Stop à <b class="dn">-${toStop.toFixed(1)}%</b> · 🎯 Cible 2 à <b class="up">+${toTp2.toFixed(1)}%</b> du cours</div>`:'';
  const sp=(d.series&&d.series.close)?spark(d.series.close):'';
  const al=alertOf(sym);
  const alB=al?`<div style="margin-top:11px;background:${al[2]}1a;border:1px solid ${al[2]}55;border-radius:9px;padding:9px 12px;display:flex;align-items:center;gap:9px"><span style="font-size:16px">${al[0]}</span><div><div style="font-size:12px;font-weight:800;color:${al[2]}">${al[1]}</div><div class="muted" style="font-size:10.5px">${al[3]}</div></div></div>`:'';
  const note=(getNotes()[sym]||'').replace(/</g,'&lt;');
  let noteEl;
  if(window.__editing===sym){
    noteEl=`<div style="margin-top:11px" onclick="event.stopPropagation()"><textarea id="nt_${sym}" placeholder="Prix d'achat, thèse, niveau à surveiller…" style="width:100%;background:#0a0a0a;border:1px solid #F5B45B55;border-radius:9px;color:#eaf0fa;font-size:12px;padding:8px 10px;min-height:54px;resize:vertical">${note}</textarea><div style="display:flex;gap:7px;margin-top:6px"><button onclick="saveNote('${sym}')" style="flex:1;background:rgba(245,180,91,.15);border:1px solid #F5B45B55;color:#F5B45B;border-radius:8px;padding:7px;font-weight:700;font-size:12px;cursor:pointer">💾 Enregistrer</button><button onclick="cancelNote()" style="background:#1a1a22;border:1px solid #2a2a33;color:#8794ab;border-radius:8px;padding:7px 12px;font-size:12px;cursor:pointer">Annuler</button></div></div>`;
  }else{
    noteEl=`<div onclick="openNote('${sym}')" style="margin-top:11px;padding:9px 12px;background:#0c0c0c;border:1px dashed #262630;border-radius:9px;font-size:11.5px;color:${note?'#cfd8e6':'#5b6678'};cursor:pointer;line-height:1.5">${note?'📝 '+note:'✎ Ajouter une note perso'}</div>`;
  }
  const acts=`<div class="acts"><a href="/titre/${sym}">📄 Fiche</a><a href="/options">💎 Options</a></div>`;
  return `<div class="fav"${al?` style="border-color:${al[2]}66"`:''}>${head}${alB}${meta}${sp}${lvls}${dist}${noteEl}${acts}</div>`;
}
function render(){
  const favs=getFavs();
  document.getElementById('cnt').textContent=favs.length;
  document.getElementById('src').innerHTML=DATA.rt?'<b style="color:#22C55E">TEMPS RÉEL IBKR</b>':'yfinance différé';
  document.getElementById('sug').innerHTML='<b>Ajout rapide :</b>'+SUG.filter(s=>!favs.includes(s)).map(s=>`<span onclick="addFav('${s}')">+ ${s}</span>`).join('');
  const host=document.getElementById('favs');
  if(!favs.length){host.innerHTML=`<div class="empty"><h3>⭐ Ta page est prête — ajoute tes titres</h3><div class="muted" style="font-size:13px;line-height:1.7;max-width:480px;margin:0 auto">Tape un ticker ci-dessus ou utilise l'ajout rapide. Pour chaque titre tu verras le cours, le score, la décision et tes niveaux clés (entrée, stop, cibles). Tes favoris restent sur ton iPhone.</div></div>`;return;}
  const alerts=favs.map(alertOf).filter(Boolean);
  const sumB=alerts.length?`<div style="background:linear-gradient(90deg,rgba(255,140,50,.14),transparent);border:1px solid #F5B45B44;border-radius:12px;padding:12px 16px;margin-top:14px;font-size:13px;font-weight:700;color:#F5B45B">🔔 ${alerts.length} alerte${alerts.length>1?'s':''} active${alerts.length>1?'s':''} sur tes favoris — voir les cartes surlignées ci-dessous</div>`:'';
  const ordered=[...favs].sort((a,b)=>(alertOf(b)?1:0)-(alertOf(a)?1:0));
  host.innerHTML=sumB+'<div class="favgrid">'+ordered.map(card).join('')+'</div>';
}
async function load(){try{const r=await Promise.all([fetch('/scan').then(r=>r.json()),fetch('/quotes').then(r=>r.json()).catch(()=>({}))]);const s=r[0]||{},q=r[1]||{};DATA={detail:s.detail||{},quotes:(q&&q.quotes)||{},rt:!!(q&&q.meta&&q.meta.rt)};}catch(e){}if(!window.__editing)render();}
render();load();setInterval(load,20000);
</script></body></html>"""


@app.route('/ma-page')
@app.route('/moi')
def my_page():
    return PAGE_ME


# ─── ANALYSE ENTREPRISE (toutes les infos live + fondamentaux des sociétés) ──
PAGE_ENTREPRISES = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Trading Desk"><meta name="mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0b0e14"><link rel="apple-touch-icon" href="/static/icon-180.png"><link rel="manifest" href="/manifest.webmanifest">
<title>Analyse Entreprise · Trading Desk</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(1200px 600px at 50% -10%,#161616,#070707 60%);color:#e8edf5;font-family:'Segoe UI',system-ui,sans-serif;padding:20px;font-variant-numeric:tabular-nums}
.wrap{max-width:1480px;margin:0 auto}
.htop{border:2px solid #38BDF855;border-radius:18px;padding:18px 24px;background:linear-gradient(135deg,#0a1015,#0b0b0b);display:flex;align-items:center;gap:20px;flex-wrap:wrap;box-shadow:0 0 40px rgba(56,189,248,.08)}
.htitle{font-size:32px;font-weight:900;letter-spacing:1px;background:linear-gradient(180deg,#7FB3FF,#38BDF8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hsub{color:#8794ab;font-weight:700;font-size:13px;margin-top:4px}
.hmeta{margin-left:auto;text-align:right;font-size:11px;color:#8794ab;line-height:1.7}.hmeta b{color:#7FB3FF}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 10px}
.sbtn{font-size:11px;font-weight:700;color:#8794ab;border:1px solid #1c1c24;background:#0e0e0e;padding:6px 12px;border-radius:8px;cursor:pointer}
.sbtn:hover{border-color:#38BDF855;color:#7FB3FF}
.sbtn.on{border-color:#38BDF8;color:#38BDF8;background:rgba(56,189,248,.08)}
.panel{border:1px solid #1c1c24;border-radius:14px;background:linear-gradient(165deg,#121212,#0a0a0a);overflow:auto}
table{width:100%;border-collapse:collapse;font-size:12px;min-width:1300px}
thead th{position:sticky;top:0;background:#0e0e0e;text-align:right;padding:11px 12px;font-size:9.5px;letter-spacing:.5px;color:#8794ab;text-transform:uppercase;font-weight:700;cursor:pointer;white-space:nowrap;border-bottom:2px solid #1c1c24}
thead th:first-child,thead th:nth-child(2){text-align:left}
thead th:hover{color:#7FB3FF}
td{padding:9px 12px;border-top:1px solid #141414;text-align:right;white-space:nowrap}
td:first-child,td:nth-child(2){text-align:left}
tbody tr{cursor:pointer;transition:background .12s}tbody tr:hover{background:rgba(56,189,248,.06)}
.sym{font-weight:800;color:#fff}.muted{color:#6b7689}.up{color:#22C55E;font-weight:700}.dn{color:#EF4444;font-weight:700}
.back{position:fixed;top:13px;left:13px;background:#111;border:1px solid #38BDF855;color:#7FB3FF;padding:7px 13px;border-radius:9px;text-decoration:none;font-size:12px;font-weight:700;z-index:9}
.foot{text-align:center;color:#5b6678;font-size:11px;margin:18px 0 6px}.foot b{color:#7FB3FF}
@media print{.back{display:none}body{padding:0}}
</style><style id="m-fix">
html,body{overflow-x:hidden;max-width:100%}
@media(max-width:640px){
  .grid,.cols,.herorow,.hero,.panorama,.layout,.heat,.secgrid,.poster{grid-template-columns:1fr!important}
  [style*="grid-template-columns"]{grid-template-columns:1fr!important}
  .top3{grid-template-columns:1fr 1fr 1fr!important}
  .plan{grid-template-columns:1fr 1fr!important}
  td,th{padding-left:9px!important;padding-right:9px!important}
  table{font-size:11.5px!important}
  .wrap,.daily{padding-left:12px!important;padding-right:12px!important}
  .phead-top{padding:15px 16px!important}
  .htitle{font-size:25px!important}
  .hmeta{margin-left:0!important;text-align:left!important}
  .back{padding:5px 9px!important;font-size:11px!important}
}
</style>
<style id="nav-css">
#gnav{position:sticky;top:0;z-index:1000;background:rgba(10,10,12,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid #1c1c24}
.gnav-in{max-width:1500px;margin:0 auto;display:flex;align-items:center;gap:12px;padding:8px 16px;flex-wrap:wrap}
.gnav-links{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
#gnav a{color:#aab4c4;text-decoration:none;font-size:13px;font-weight:700;padding:7px 12px;border-radius:9px;white-space:nowrap}
#gnav a:hover{background:#ffffff0d;color:#fff}
#gnav a.act{background:rgba(245,180,91,.14);color:#F5B45B}
.gnav-search{display:flex;gap:6px;align-items:center}
.gnav-search input{background:#0e0e0e;border:1px solid #1c1c24;color:#e8edf5;border-radius:9px;padding:7px 11px;font-size:13px;width:210px;max-width:46vw}
.gnav-search button{background:rgba(245,180,91,.12);border:1px solid #F5B45B55;color:#F5B45B;border-radius:9px;padding:7px 13px;font-weight:800;cursor:pointer}
.back{display:none!important}
@media(max-width:640px){.gnav-in{padding:6px 10px;gap:8px}.gnav-links{order:2;overflow-x:auto;flex-wrap:nowrap}#gnav a{font-size:12px;padding:6px 9px}.gnav-search{order:1;width:100%}.gnav-search input{flex:1;max-width:none;width:auto}}
/* === systeme de design premium (toutes pages) === */
body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;background:radial-gradient(1200px 780px at 50% -8%,#0d0d12,#050506 60%)!important}
.panel,.scard,.card,.pcard,.seccard,.tile,.kpi,.fav,.pchip,.stat{background:linear-gradient(165deg,#16171c,#0d0e12)!important;border:1px solid rgba(255,255,255,.08)!important;border-radius:16px!important}
.panel,.scard,.card,.pcard,.fav,.seccard{transition:border-color .2s ease,box-shadow .2s ease,transform .18s ease!important}
.panel:hover,.scard:hover,.card:hover,.fav:hover,.seccard:hover{border-color:rgba(255,140,50,.32)!important;box-shadow:0 0 26px rgba(255,140,50,.10)!important}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#26262e;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:#3a3a44}
::selection{background:rgba(255,140,50,.28);color:#fff}
/* bandeau KPI (cockpit + autres) */
.kpiband{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin:14px 0}
.kc{background:linear-gradient(165deg,#16171c,#0d0e12);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:11px 12px;min-width:0}
.kc .kt{font-size:8.5px;letter-spacing:.6px;text-transform:uppercase;color:#8794ab;display:flex;align-items:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kv{font-size:17px;font-weight:900;margin-top:3px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .ks{font-size:9px;color:#6b7689;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kbar{height:5px;border-radius:4px;background:#0a0a0a;overflow:hidden;margin-top:6px}
.kc .kbar>div{height:100%;border-radius:4px}
@media(max-width:760px){.kpiband{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:44%;overflow-x:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;padding-bottom:2px}.kc{scroll-snap-align:start}}
/* navigation mobile sticky en bas */
#mnav{display:none}
@media(max-width:760px){
  #mnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:1200;background:rgba(8,8,10,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid rgba(255,255,255,.09);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
  #mnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;text-decoration:none;color:#8794ab;font-size:9.5px;font-weight:700;padding:5px 2px;border-radius:12px}
  #mnav a .mi{font-size:19px;line-height:1}
  #mnav a.act{color:#FF8C32;background:rgba(255,140,50,.13)}
  body{padding-bottom:68px!important}
  #gnav .gnav-links{display:none}
}
</style>
<script>
(function(){var L=[['/','🔱 Cockpit'],['/ma-page','⭐ Ma Page'],['/watchlist','📋 Watchlist'],['/entreprises','🏢 Entreprises'],['/options','💎 Options']];
function build(){if(document.getElementById('gnav'))return;var p=location.pathname;
var links=L.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'>'+x[1]+'</a>';}).join('');
var nav=document.createElement('nav');nav.id='gnav';
nav.innerHTML='<div class="gnav-in"><div class="gnav-links">'+links+'</div><form class="gnav-search" onsubmit="return gnavGo(event)"><input id="gnavq" placeholder="Rechercher un titre… (ex: NVDA)" autocomplete="off"><button type="submit">→</button></form></div>';
document.body.insertBefore(nav,document.body.firstChild);
var M=[['/','🏠','Cockpit'],['/watchlist','📡','Scan'],['/ma-page','⭐','Ma Page'],['/options','💎','Options'],['/entreprises','🏢','Titres']];
var mn=document.createElement('nav');mn.id='mnav';
mn.innerHTML=M.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'><span class="mi">'+x[1]+'</span>'+x[2]+'</a>';}).join('');
document.body.appendChild(mn);}
window.gnavGo=function(e){e.preventDefault();var v=(document.getElementById('gnavq').value||'').trim().toUpperCase();if(v)location.href='/titre/'+encodeURIComponent(v);return false;};
if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();})();
</script></head><body>
<a class="back" href="/">← cockpit</a>
<div class="wrap">
  <div class="htop"><span style="font-size:36px">🏢</span>
    <div><div class="htitle">ANALYSE ENTREPRISE</div><div class="hsub" id="hsub">…</div></div>
    <div class="hmeta"><div>Cours <b>en direct</b> + fondamentaux</div><div id="hsrc">…</div><div style="font-size:10px;color:#5b6678;margin-top:3px">Analyse éducative — pas un conseil · ⛔ aucun ordre</div></div>
  </div>
  <div class="bar" id="bar"></div>
  <div class="panel"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>
  <div class="foot">Trie en cliquant les colonnes · clic ligne → fiche complète sur le cockpit · <b>57 leaders US</b></div>
</div>
<script>
const C={g:'#22C55E',r:'#EF4444',gold:'#F5B45B',blue:'#38BDF8',mut:'#6b7689'};
const COLS=[['symbol','Ticker'],['sector','Secteur'],['price','Prix'],['change','Var %'],['mcap','Cap.'],['pe','P/E'],['valr','vs sect.'],['fwd_pe','P/E fwd'],['margin','Marge'],['growth','Croiss.'],['beta','Beta'],['div','Div'],['score','Score'],['rs','RS'],['rsi','RSI'],['regime','Régime'],['earn','Résultats']];
let DATA=[],SORT='score',DIR=-1;
function cap(n){return n==null?'—':n>=1e12?(n/1e12).toFixed(2)+'T':n>=1e9?(n/1e9).toFixed(1)+'B':n>=1e6?(n/1e6).toFixed(0)+'M':n}
function pct(n,d){return n==null?'—':(n*100).toFixed(d==null?0:d)+'%'}
function scol(s){return s>=72?C.g:s>=55?C.gold:s==null?C.mut:C.r}
function regTxt(r){return r==='TREND'?'Tendance':r==='CHOP'?'Range':'Neutre'}
function cell(c){
  return `<tr onclick="location.href='/titre/${c.symbol}'">
  <td class="sym">${c.symbol}</td>
  <td class="muted">${c.sector||'—'}</td>
  <td>$${c.price!=null?c.price:'—'}</td>
  <td class="${(c.change||0)>=0?'up':'dn'}">${c.change!=null?((c.change>=0?'+':'')+c.change+'%'):'—'}</td>
  <td>${cap(c.mcap)}</td>
  <td>${c.pe?c.pe.toFixed(1):'—'}</td>
  <td style="color:${c.valTone==='good'?C.g:c.valTone==='warn'?C.r:C.mut};font-weight:600">${c.valLabel||'—'}</td>
  <td class="muted">${c.fwd_pe?c.fwd_pe.toFixed(1):'—'}</td>
  <td class="muted">${pct(c.margin)}</td>
  <td class="${(c.growth||0)>=0?'up':'dn'}">${c.growth!=null?pct(c.growth):'—'}</td>
  <td class="muted">${c.beta?c.beta.toFixed(2):'—'}</td>
  <td class="muted">${c.div?(c.div<1?(c.div*100).toFixed(1):c.div.toFixed(1))+'%':'—'}</td>
  <td style="font-weight:800;color:${scol(c.score)}">${c.score!=null?c.score:'—'} <span class="muted" style="font-size:10px">${c.grade||''}</span></td>
  <td class="muted">${c.rs!=null?Math.round(c.rs):'—'}</td>
  <td class="muted">${c.rsi!=null?Math.round(c.rsi):'—'}</td>
  <td style="color:${c.regime==='TREND'?C.g:c.regime==='CHOP'?C.r:C.gold}">${regTxt(c.regime)}</td>
  <td class="${c.earnSoon?'dn':'muted'}" style="${c.earnSoon?'font-weight:800':''}">${c.earn||'—'}</td></tr>`;}
function render(){
  document.getElementById('thead').innerHTML='<tr>'+COLS.map(([k,l])=>`<th onclick="setSort('${k}')">${l}${SORT===k?(DIR<0?' ▾':' ▴'):''}</th>`).join('')+'</tr>';
  const d=[...DATA].sort((a,b)=>{let x=a[SORT],y=b[SORT];if(x==null)return 1;if(y==null)return -1;if(typeof x==='string')return DIR*x.localeCompare(y);return DIR*(x-y);});
  document.getElementById('tbody').innerHTML=d.map(cell).join('');
}
function setSort(k){if(SORT===k)DIR=-DIR;else{SORT=k;DIR=(k==='symbol'||k==='sector')?1:-1;}render();updateBar();}
function updateBar(){document.getElementById('bar').innerHTML='Trier : '+[['score','Score'],['change','Variation'],['mcap','Capitalisation'],['pe','P/E'],['rs','Force relative'],['growth','Croissance'],['margin','Marge']].map(([k,l])=>`<span class="sbtn ${SORT===k?'on':''}" onclick="setSort('${k}')">${l}</span>`).join('');}
async function load(){
  let s={},q={},cal={};
  try{[s,q,cal]=await Promise.all([fetch('/scan').then(r=>r.json()),fetch('/quotes').then(r=>r.json()).catch(()=>({})),fetch('/cal-feed').then(r=>r.json()).catch(()=>({}))]);}catch(e){}
  const ql=(q&&q.quotes)||{},det=s.detail||{},fu=(s.fundamentals||{}),fs=fu.by_sym||{},fsec=fu.by_sector||{};
  const em={};((cal.items)||[]).forEach(x=>{em[x.sym]={d:x.date,dte:x.dte};});
  DATA=(s.rows||[]).map(r=>{const sym=r.symbol,d=det[sym]||{},f=fs[sym]||{},x=ql[sym];
    const sec=f.sector||d.sector,med=(fsec[sec]||{}).median_pe;
    let valTone=null,valLabel=null,valr=null;
    if(f.pe&&med){const ra=f.pe/med;valr=ra;valLabel=ra>=1.3?'cher ×'+ra.toFixed(1):ra<=0.75?'décoté ×'+ra.toFixed(1):'moyenne ×'+ra.toFixed(1);valTone=ra>=1.3?'warn':ra<=0.75?'good':'neutral';}
    const e=em[sym];
    return {symbol:sym,sector:sec||'—',price:x?x.last:r.price,change:(x&&x.change!=null)?x.change:r.change,
      mcap:f.mcap,pe:f.pe,fwd_pe:f.fwd_pe,margin:f.margin,growth:f.growth,beta:f.beta,div:f.div,
      valTone,valLabel,valr,score:d.score,grade:d.grade,rs:d.rs,rsi:d.rsi,regime:d.regime,
      earn:e?(e.dte<=0?'auj.':'J-'+e.dte):null,earnSoon:e&&e.dte!=null&&e.dte<7};});
  const m=s.market||{},SE={pre:'🌅 avant-bourse',open:'🟢 séance',after:'🌙 après-bourse',closed:'🌑 fermé'};
  document.getElementById('hsub').textContent=DATA.length+' sociétés · '+(SE[m.session]||'')+' '+(m.et||'');
  document.getElementById('hsrc').innerHTML=(q&&q.meta&&q.meta.rt)?'Cours <b>TEMPS RÉEL IBKR</b> + fondamentaux yfinance':'Cours yfinance différé + fondamentaux';
  updateBar();render();
}
load();setInterval(load,12000);
</script></body></html>"""


@app.route('/entreprises')
@app.route('/analyse-entreprise')
def entreprises_page():
    return PAGE_ENTREPRISES


# ─── FICHE ENTREPRISE COMPLÈTE (page dédiée par titre) ──────────────────────
PAGE_TITRE = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Trading Desk"><meta name="mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0b0e14"><link rel="apple-touch-icon" href="/static/icon-180.png"><link rel="manifest" href="/manifest.webmanifest">
<title>Fiche · Trading Desk</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(1200px 600px at 50% -10%,#161616,#070707 60%);color:#e8edf5;font-family:'Segoe UI',system-ui,sans-serif;padding:20px;font-variant-numeric:tabular-nums}
.wrap{max-width:1280px;margin:0 auto}
.hdr{display:flex;align-items:flex-end;gap:18px;flex-wrap:wrap;border-bottom:1px solid #1c1c24;padding-bottom:16px;margin-bottom:18px}
.tk{font-size:46px;font-weight:900;letter-spacing:1px}
.nm{font-size:14px;color:#8794ab;margin-bottom:6px}
.px{font-size:30px;font-weight:800}
.tag{font-size:11px;font-weight:800;padding:3px 10px;border-radius:8px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.card{border:1px solid #1c1c24;border-radius:14px;background:linear-gradient(165deg,#131313,#0b0b0b);padding:16px 18px;margin-bottom:16px;animation:fu .35s ease both}
@keyframes fu{from{opacity:0;transform:translateY(8px)}to{opacity:1}}
.ct{font-size:11px;font-weight:800;letter-spacing:1px;color:#8794ab;margin-bottom:11px;text-transform:uppercase}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px}
.kpi{background:#0e0e0e;border:1px solid #1a1a22;border-radius:10px;padding:11px 13px}
.kpi .l{font-size:9px;color:#8794ab;text-transform:uppercase;letter-spacing:.5px}.kpi .v{font-size:17px;font-weight:800;margin-top:3px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:7px 10px;font-size:9px;color:#6b7689;text-transform:uppercase;font-weight:700}
td{padding:8px 10px;border-top:1px solid #141414}
.up{color:#22C55E;font-weight:700}.dn{color:#EF4444;font-weight:700}.muted{color:#6b7689}.sym{font-weight:800;color:#fff}
.back{position:fixed;top:13px;left:13px;background:#111;border:1px solid #FF8C3255;color:#F5B45B;padding:7px 13px;border-radius:9px;text-decoration:none;font-size:12px;font-weight:700;z-index:9}
.fit{font-size:9px;font-weight:800;padding:2px 7px;border-radius:6px}
a.news{color:#9fc1ff;text-decoration:none}a.news:hover{text-decoration:underline}
@media(max-width:820px){.grid2{grid-template-columns:1fr}}
</style><style id="m-fix">
html,body{overflow-x:hidden;max-width:100%}
@media(max-width:640px){
  .grid,.cols,.herorow,.hero,.panorama,.layout,.heat,.secgrid,.poster{grid-template-columns:1fr!important}
  [style*="grid-template-columns"]{grid-template-columns:1fr!important}
  .top3{grid-template-columns:1fr 1fr 1fr!important}
  .plan{grid-template-columns:1fr 1fr!important}
  td,th{padding-left:9px!important;padding-right:9px!important}
  table{font-size:11.5px!important}
  .wrap,.daily{padding-left:12px!important;padding-right:12px!important}
  .phead-top{padding:15px 16px!important}
  .htitle{font-size:25px!important}
  .hmeta{margin-left:0!important;text-align:left!important}
  .back{padding:5px 9px!important;font-size:11px!important}
}
</style>
<style id="nav-css">
#gnav{position:sticky;top:0;z-index:1000;background:rgba(10,10,12,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid #1c1c24}
.gnav-in{max-width:1500px;margin:0 auto;display:flex;align-items:center;gap:12px;padding:8px 16px;flex-wrap:wrap}
.gnav-links{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
#gnav a{color:#aab4c4;text-decoration:none;font-size:13px;font-weight:700;padding:7px 12px;border-radius:9px;white-space:nowrap}
#gnav a:hover{background:#ffffff0d;color:#fff}
#gnav a.act{background:rgba(245,180,91,.14);color:#F5B45B}
.gnav-search{display:flex;gap:6px;align-items:center}
.gnav-search input{background:#0e0e0e;border:1px solid #1c1c24;color:#e8edf5;border-radius:9px;padding:7px 11px;font-size:13px;width:210px;max-width:46vw}
.gnav-search button{background:rgba(245,180,91,.12);border:1px solid #F5B45B55;color:#F5B45B;border-radius:9px;padding:7px 13px;font-weight:800;cursor:pointer}
.back{display:none!important}
@media(max-width:640px){.gnav-in{padding:6px 10px;gap:8px}.gnav-links{order:2;overflow-x:auto;flex-wrap:nowrap}#gnav a{font-size:12px;padding:6px 9px}.gnav-search{order:1;width:100%}.gnav-search input{flex:1;max-width:none;width:auto}}
/* === systeme de design premium (toutes pages) === */
body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;background:radial-gradient(1200px 780px at 50% -8%,#0d0d12,#050506 60%)!important}
.panel,.scard,.card,.pcard,.seccard,.tile,.kpi,.fav,.pchip,.stat{background:linear-gradient(165deg,#16171c,#0d0e12)!important;border:1px solid rgba(255,255,255,.08)!important;border-radius:16px!important}
.panel,.scard,.card,.pcard,.fav,.seccard{transition:border-color .2s ease,box-shadow .2s ease,transform .18s ease!important}
.panel:hover,.scard:hover,.card:hover,.fav:hover,.seccard:hover{border-color:rgba(255,140,50,.32)!important;box-shadow:0 0 26px rgba(255,140,50,.10)!important}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#26262e;border-radius:6px}::-webkit-scrollbar-thumb:hover{background:#3a3a44}
::selection{background:rgba(255,140,50,.28);color:#fff}
/* bandeau KPI (cockpit + autres) */
.kpiband{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin:14px 0}
.kc{background:linear-gradient(165deg,#16171c,#0d0e12);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:11px 12px;min-width:0}
.kc .kt{font-size:8.5px;letter-spacing:.6px;text-transform:uppercase;color:#8794ab;display:flex;align-items:center;gap:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kv{font-size:17px;font-weight:900;margin-top:3px;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .ks{font-size:9px;color:#6b7689;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc .kbar{height:5px;border-radius:4px;background:#0a0a0a;overflow:hidden;margin-top:6px}
.kc .kbar>div{height:100%;border-radius:4px}
@media(max-width:760px){.kpiband{grid-template-columns:none;grid-auto-flow:column;grid-auto-columns:44%;overflow-x:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;padding-bottom:2px}.kc{scroll-snap-align:start}}
/* navigation mobile sticky en bas */
#mnav{display:none}
@media(max-width:760px){
  #mnav{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:1200;background:rgba(8,8,10,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:1px solid rgba(255,255,255,.09);padding:6px 4px calc(6px + env(safe-area-inset-bottom))}
  #mnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;text-decoration:none;color:#8794ab;font-size:9.5px;font-weight:700;padding:5px 2px;border-radius:12px}
  #mnav a .mi{font-size:19px;line-height:1}
  #mnav a.act{color:#FF8C32;background:rgba(255,140,50,.13)}
  body{padding-bottom:68px!important}
  #gnav .gnav-links{display:none}
}
</style>
<script>
(function(){var L=[['/','🔱 Cockpit'],['/ma-page','⭐ Ma Page'],['/watchlist','📋 Watchlist'],['/entreprises','🏢 Entreprises'],['/options','💎 Options']];
function build(){if(document.getElementById('gnav'))return;var p=location.pathname;
var links=L.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'>'+x[1]+'</a>';}).join('');
var nav=document.createElement('nav');nav.id='gnav';
nav.innerHTML='<div class="gnav-in"><div class="gnav-links">'+links+'</div><form class="gnav-search" onsubmit="return gnavGo(event)"><input id="gnavq" placeholder="Rechercher un titre… (ex: NVDA)" autocomplete="off"><button type="submit">→</button></form></div>';
document.body.insertBefore(nav,document.body.firstChild);
var M=[['/','🏠','Cockpit'],['/watchlist','📡','Scan'],['/ma-page','⭐','Ma Page'],['/options','💎','Options'],['/entreprises','🏢','Titres']];
var mn=document.createElement('nav');mn.id='mnav';
mn.innerHTML=M.map(function(x){var a=(x[0]==='/'?(p==='/'||p==='/daily'):p.indexOf(x[0])===0);return '<a href="'+x[0]+'"'+(a?' class="act"':'')+'><span class="mi">'+x[1]+'</span>'+x[2]+'</a>';}).join('');
document.body.appendChild(mn);}
window.gnavGo=function(e){e.preventDefault();var v=(document.getElementById('gnavq').value||'').trim().toUpperCase();if(v)location.href='/titre/'+encodeURIComponent(v);return false;};
if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();})();
</script></head><body>
<a class="back" href="/">← cockpit</a>
<div class="wrap">
  <div class="hdr">
    <div><div class="nm" id="nm">chargement…</div><div class="tk" id="sym">…</div></div>
    <div><div class="px" id="px">—</div><div id="chg" class="muted" style="font-size:13px"></div></div>
    <div style="margin-left:auto;text-align:right" id="tags"></div>
    <button id="favBtn" onclick="toggleFav()" style="margin-left:14px;background:#0e0e0e;border:1px solid #F5B45B55;color:#F5B45B;border-radius:10px;padding:9px 15px;font-weight:800;font-size:13px;cursor:pointer;white-space:nowrap">☆ Suivre</button>
  </div>
  <div id="ibkr"></div>
  <div class="grid2">
    <div id="left"></div>
    <div id="right"></div>
  </div>
  <div style="text-align:center;color:#5b6678;font-size:11px;margin:18px 0">⛔ analyse only · jamais un ordre · cours live IBKR si dispo, sinon yfinance différé</div>
</div>
<script>
const C={g:'#22C55E',r:'#EF4444',gold:'#F5B45B',blue:'#38BDF8',vio:'#A78BFA',cy:'#34D399',yl:'#FFB23F',mut:'#6b7689'};
const SYM=decodeURIComponent((location.pathname.split('/').filter(Boolean).pop()||'')).toUpperCase();
function niv(n){return n==='S+'?C.g:n==='S'?C.cy:n==='A'?C.gold:n==='B'?C.yl:C.r}
function tim(s){return s==='BUY_NOW'?'✅ achat propre':s==='BUY_PULLBACK'?'⏳ sur repli':s==='WATCH_BREAKOUT'?'👀 sur cassure':s==='TOO_LATE'?'🛑 trop étendu':'éviter'}
function eu(s){return s?s.slice(8,10)+'/'+s.slice(5,7)+'/'+s.slice(2,4):''}
function cap(n){return n==null?'—':n>=1e12?(n/1e12).toFixed(2)+'T':n>=1e9?(n/1e9).toFixed(1)+'B':n>=1e6?(n/1e6).toFixed(0)+'M':n}
function fmt(n){return n==null?'—':(Math.abs(n)>=1e6?(n/1e6).toFixed(1)+'M':Math.abs(n)>=1e3?(n/1e3).toFixed(1)+'k':n)}
function pct(n){return n==null?'—':(n*100).toFixed(0)+'%'}
function qc(q){return q>=78?C.g:q>=62?C.gold:q>=50?C.yl:C.r}
const FIT={leaps:['✅ LEAPS',C.g],swing:['⚡ Swing',C.blue],tact:['⚠️ Tactique',C.yl],avoid:['❌ Éviter',C.r]};
function fitOf(c){const d=Math.abs(c.delta||0);if((c.quality||0)<50||c.danger==='Extrême'||(c.spread!=null&&c.spread>10))return'avoid';if(c.bucket==='long'&&d>=0.6&&(c.quality||0)>=66)return'leaps';if(c.bucket==='moyen')return'swing';if(c.bucket==='court')return'tact';return'swing';}
function kpi(l,v){return `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`}
async function load(){
  let o={},s={},q={};
  try{[o,s,q]=await Promise.all([fetch('/options/'+SYM).then(r=>r.json()),fetch('/scan').then(r=>r.json()),fetch('/quotes').then(r=>r.json()).catch(()=>({}))]);}catch(e){}
  const d=(s.detail||{})[SYM]||{};
  const lq=((q&&q.quotes)||{})[SYM];
  const px=lq?lq.last:(o.spot||d.price), chg=(lq&&lq.change!=null)?lq.change:(d.change);
  document.getElementById('sym').textContent=SYM;
  document.getElementById('nm').textContent=(o.name||'')+(o.sector?' · '+o.sector:'');
  document.getElementById('px').textContent='$'+(px!=null?px:'—');
  document.getElementById('chg').innerHTML=chg!=null?`<span class="${chg>=0?'up':'dn'}">${chg>=0?'+':''}${chg}%</span> ${lq?'· live IBKR':'· différé'}`:'';
  // tags
  const ik=o.ibkr;
  document.getElementById('tags').innerHTML=ik?`<span class="tag" style="color:${ik.color};border:1.5px solid ${ik.color};background:${ik.color}18">${ik.niveau} · ${ik.score40}/40</span> <span class="tag" style="color:${ik.color}">${ik.decision}</span>`:(d.grade?`<span class="tag" style="color:${C.gold}">${d.grade} · score ${d.score}</span>`:'');
  // IBKR verdict card
  if(ik){const ic=ik.color,tm=ik.timing||{};const comp=Object.entries(ik.components||{}).map(([k,v])=>`<div style="margin:4px 0"><div style="display:flex;justify-content:space-between;font-size:10.5px"><span class="muted">${k}</span><b>${v[0]}/${v[1]}</b></div><div style="height:5px;background:#1a1a1a;border-radius:3px;overflow:hidden"><div style="height:100%;width:${Math.round(v[0]/v[1]*100)}%;background:${ic}"></div></div></div>`).join('');
    const nc=(ik.no_chase||[]).length?`<div style="margin-top:9px;background:rgba(239,68,68,.08);border:1px solid #EF444444;border-radius:8px;padding:8px 11px"><b style="color:#F87171;font-size:10px">🛑 NO-CHASE</b>${ik.no_chase.map(x=>`<div style="font-size:11px;margin-top:2px">• ${x}</div>`).join('')}</div>`:'';
    document.getElementById('ibkr').innerHTML=`<div class="card" style="border:1.5px solid ${ic}66;background:linear-gradient(135deg,${ic}14,#0b0b0b)">
      <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:13px"><span style="font-size:13px;font-weight:900;color:${ic}">🔱 VERDICT IBKR</span><span style="font-size:19px;font-weight:900;color:${ic};padding:1px 12px;border:1.5px solid ${ic};border-radius:9px">${ik.niveau}</span><span style="font-size:27px;font-weight:900;color:${ic}">${ik.score40}<span style="font-size:13px;color:#888">/40</span></span><span style="margin-left:auto;font-size:18px;font-weight:900;color:${ic}">${ik.decision}</span></div>
      <div class="grid2"><div><div class="ct">Score IBKR /40</div>${comp}</div><div><div class="ct">⏱️ Timing — ${tim(tm.state)}</div><div style="font-size:12.5px;line-height:1.9"><div>Entrée optimale <b class="up">$${tm.optimal}</b></div><div>Entrée agressive <b>$${tm.aggressive}</b></div><div>Invalidation <b class="dn">$${tm.invalidation}</b></div><div>Allocation max <b style="color:${ic}">${ik.alloc}</b></div></div>${nc}</div></div>
      <div style="margin-top:13px;padding-top:12px;border-top:1px solid #ffffff12;font-size:13px"><b style="color:${ic}">Raison :</b> ${ik.raison}<br><b style="color:${ic}">→ Action :</b> ${ik.action}</div></div>`;}
  // LEFT : KPIs + chart + decision
  const dec=o.decision,plan=d.plan||{};
  const kpis=`<div class="kpis">${kpi('SCORE',(d.score!=null?d.score:'—')+' '+(d.grade||''))}${kpi('RÉGIME',d.regime==='TREND'?'Tendance':d.regime==='CHOP'?'Range':'Neutre')}${kpi('RSI',Math.round(d.rsi||0))}${kpi('FORCE REL.',Math.round(d.rs||0))}${kpi('QUALITÉ SETUP',(d.setup_quality!=null?d.setup_quality:'—')+'/100')}${kpi('POS 52S',Math.round(d.pos52||0)+'%')}${kpi('EXT. ATR',(d.ext_atr!=null?d.ext_atr.toFixed(1):'—')+'x')}${kpi('RVOL',(d.volx!=null?d.volx.toFixed(1):'—')+'x')}${kpi('PLAN',`$${plan.entry||'—'} <span class="dn">$${plan.stop||'—'}</span> <span class="up">$${plan.tp2||'—'}</span>`)}</div>`;
  const decCard=dec?`<div class="card"><div class="ct">🧠 Décision · conviction ${dec.conviction}/100</div><div class="grid2"><div><div style="font-size:10px;color:${C.g};font-weight:700;margin-bottom:4px">✓ FORCES</div>${(dec.pros||[]).map(p=>`<div style="font-size:12px;margin:3px 0"><span class="up">✓</span> ${p}</div>`).join('')||'—'}</div><div><div style="font-size:10px;color:${C.r};font-weight:700;margin-bottom:4px">⚠ RISQUES</div>${(dec.cons||[]).map(c=>`<div style="font-size:12px;margin:3px 0"><span class="dn">✗</span> ${c}</div>`).join('')||'aucun majeur'}</div></div></div>`:'';
  const chartCard=(d.series&&d.series.close)?`<div class="card"><div class="ct">📈 Cours · 120 jours <span style="color:${C.gold}">— MM20</span> <span class="muted">— MM50</span></div><div style="height:230px"><canvas id="cv"></canvas></div><div class="muted" style="font-size:11.5px;margin-top:10px;line-height:1.5">🔬 ${o.chart_read||''}</div></div>`:'';
  document.getElementById('left').innerHTML=`<div class="card"><div class="ct">📊 Indicateurs techniques</div>${kpis}</div>${chartCard}${decCard}`;
  // RIGHT : fundamentals + options + news
  const f=o.fund||{},v=o.valuation;
  const fund=`<div class="card"><div class="ct">🏢 Fondamentaux vs secteur</div><table>
    <tr><td class="muted">Capitalisation</td><td style="text-align:right;font-weight:700">${cap(o.mcap||f.mcap)}</td></tr>
    <tr><td class="muted">P/E</td><td style="text-align:right;font-weight:700">${(f.pe||o.pe)?(f.pe||o.pe).toFixed(1):'—'} ${v?`<span style="color:${v.tone==='good'?C.g:v.tone==='warn'?C.r:C.mut}">(${v.label})</span>`:''}</td></tr>
    <tr><td class="muted">P/E forward</td><td style="text-align:right">${f.fwd_pe?f.fwd_pe.toFixed(1):'—'} <span class="muted">· secteur ${o.sector_median_pe||'—'}</span></td></tr>
    <tr><td class="muted">Marge nette</td><td style="text-align:right">${pct(f.margin)} <span class="muted">· secteur ${o.sector_median_margin!=null?o.sector_median_margin+'%':'—'}</span></td></tr>
    <tr><td class="muted">Croissance CA</td><td style="text-align:right" class="${(f.growth||0)>=0?'up':'dn'}">${pct(f.growth)} <span class="muted">· secteur ${o.sector_median_growth!=null?o.sector_median_growth+'%':'—'}</span></td></tr>
    <tr><td class="muted">Beta</td><td style="text-align:right">${f.beta?f.beta.toFixed(2):(o.beta?o.beta.toFixed(2):'—')}</td></tr>
    <tr><td class="muted">Dividende</td><td style="text-align:right">${f.div?(f.div<1?(f.div*100).toFixed(1):f.div.toFixed(1))+'%':'—'}</td></tr>
    <tr><td class="muted">Résultats</td><td style="text-align:right">${o.earnings||'—'}${o.earnings_dte!=null?` <span class="${o.earnings_dte<7?'dn':'muted'}">(J-${o.earnings_dte})</span>`:''}</td></tr></table></div>`;
  const bp=o.best_pick,sc=o.scenarios,be=o.breakeven,em=o.expected_move;
  const contracts=(o.contracts||[]).map(c=>{const f2=FIT[fitOf(c)];return `<tr><td class="sym">${(c.bucket||'').toUpperCase()}</td><td>${eu(c.exp)}</td><td>$${c.strike}</td><td style="font-weight:800;color:${qc(c.quality)}">${c.quality}</td><td>${c.pop}%</td><td>$${fmt(c.cost)}</td><td><span class="fit" style="color:${f2[1]};border:1px solid ${f2[1]}66">${f2[0]}</span></td></tr>`;});
  const opt=bp?`<div class="card"><div class="ct">💎 Options Desk</div>
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px"><b style="font-size:14px">${(bp.bucket||'').toUpperCase()} · ${eu(bp.exp)} · $${bp.strike}</b><span style="margin-left:auto">QUALITÉ <b style="color:${qc(bp.quality)};font-size:16px">${bp.quality}/100</b></span></div>
    <div class="muted" style="font-size:11.5px;margin-bottom:10px">coût $${fmt(bp.cost)} · delta ${bp.delta} · POP ${bp.pop}% · danger ${bp.danger} · OI ${fmt(bp.oi)} · IV ${bp.iv}%</div>
    ${sc?`<div style="display:flex;gap:8px;background:#0c0c0c;border-radius:8px;padding:10px;margin-bottom:8px"><div style="flex:1;text-align:center"><div class="muted" style="font-size:9px">🔴 PESSIMISTE</div><div style="font-size:17px;font-weight:800;color:${C.r}">${(sc.pess&&sc.pess.pnl!=null)?sc.pess.pnl+'%':'—'}</div></div><div style="flex:1;text-align:center"><div class="muted" style="font-size:9px">🟡 PROBABLE</div><div style="font-size:17px;font-weight:800;color:${C.gold}">${(sc.prob&&sc.prob.pnl!=null)?'+'+sc.prob.pnl+'%':'—'}</div></div><div style="flex:1;text-align:center"><div class="muted" style="font-size:9px">🟢 EXCEPTIONNEL</div><div style="font-size:17px;font-weight:800;color:${C.g}">${(sc.exalt&&sc.exalt.pnl!=null)?'+'+sc.exalt.pnl+'%':'—'}</div></div></div>`:''}
    <div class="muted" style="font-size:11.5px;margin-bottom:10px">${be?`🎯 Breakeven $${be.be} (${be.dist>=0?'+':''}${be.dist}%) · ${be.monthly}%/mois → ${be.verdict}`:''} ${em?` · 📐 Expected move ±${em.pct}%`:''}</div>
    <table><thead><tr><th>Bucket</th><th>Éch.</th><th>Strike</th><th>Qual.</th><th>POP</th><th>Coût</th><th>Verdict</th></tr></thead><tbody>${contracts.join('')}</tbody></table></div>`:'<div class="card"><div class="ct">💎 Options</div><div class="muted" style="font-size:12px">Chaîne en calcul ou indisponible hors séance.</div></div>';
  const news=(o.news||[]).slice(0,5).map(n=>`<div style="padding:7px 0;border-top:1px solid #141414;font-size:12px;line-height:1.4">${n.link?`<a class="news" href="${n.link}" target="_blank">${n.fr||n.title}</a>`:(n.fr||n.title)} <span class="muted" style="font-size:10px">${n.time||''}</span></div>`).join('');
  const newsCard=news?`<div class="card"><div class="ct">📰 Actualités</div>${news}</div>`:'';
  document.getElementById('right').innerHTML=fund+opt+newsCard;
  // chart
  if(d.series&&d.series.close&&typeof Chart!=='undefined'){const cv=document.getElementById('cv');if(cv){const g=cv.getContext('2d').createLinearGradient(0,0,0,230);g.addColorStop(0,'rgba(255,140,50,.28)');g.addColorStop(1,'rgba(255,140,50,0)');new Chart(cv,{type:'line',data:{labels:d.series.dates,datasets:[{data:d.series.close,borderColor:'#FF8C32',backgroundColor:g,fill:true,borderWidth:2,pointRadius:0,tension:.18},{data:d.series.ema20,borderColor:'#F5B45B',borderWidth:1.1,pointRadius:0,borderDash:[4,3]},{data:d.series.sma50,borderColor:'#6b7689',borderWidth:1,pointRadius:0}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#6b6b6b',maxTicksLimit:7,font:{size:9}},grid:{color:'#141414'}},y:{ticks:{color:'#6b6b6b',font:{size:9}},grid:{color:'#141414'}}}}});}}
}
function getFavs(){try{return JSON.parse(localStorage.getItem('myFavs')||'[]')}catch(e){return[]}}
function toggleFav(){let a=getFavs();if(a.includes(SYM))a=a.filter(x=>x!==SYM);else a.unshift(SYM);localStorage.setItem('myFavs',JSON.stringify(a));updateFavBtn(true);}
function updateFavBtn(flash){const b=document.getElementById('favBtn');if(!b)return;const on=getFavs().includes(SYM);b.innerHTML=on?'⭐ Dans Ma Page':'☆ Suivre';b.style.background=on?'rgba(245,180,91,.16)':'#0e0e0e';if(flash&&on){b.innerHTML='⭐ Ajouté à Ma Page ✓';setTimeout(()=>updateFavBtn(false),1400);}}
updateFavBtn(false);
load();setInterval(load,15000);
</script></body></html>"""


@app.route('/titre/<sym>')
def titre_page(sym):
    return PAGE_TITRE


def _start_app():
    threading.Thread(target=_loop, daemon=True).start()
    threading.Thread(target=_opt_loop, daemon=True).start()
    threading.Thread(target=_news_loop, daemon=True).start()
    threading.Thread(target=_cal_loop, daemon=True).start()
    threading.Thread(target=_weekly_loop, daemon=True).start()
    threading.Thread(target=_fund_loop, daemon=True).start()
    if IBKR_ENABLED:                                  # pas de TWS sur le cloud → on n'essaie pas
        threading.Thread(target=_quotes_worker, daemon=True).start()
    port = int(os.environ.get('PORT', 5002))          # le cloud (Render…) impose le port via $PORT
    # host 0.0.0.0 = accessible réseau local (iPhone) ET cloud
    print(f'TRACK TERMINAL -> http://localhost:{port}  ·  IBKR live: {IBKR_ENABLED}  (Ctrl+C pour arreter)')
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)


# démarre les threads dès l'import (pour gunicorn/cloud) si demandé, sinon en __main__
if os.environ.get('START_ON_IMPORT') == '1':
    threading.Thread(target=_loop, daemon=True).start()
    threading.Thread(target=_opt_loop, daemon=True).start()
    threading.Thread(target=_news_loop, daemon=True).start()
    threading.Thread(target=_cal_loop, daemon=True).start()
    threading.Thread(target=_weekly_loop, daemon=True).start()
    threading.Thread(target=_fund_loop, daemon=True).start()
    if IBKR_ENABLED:
        threading.Thread(target=_quotes_worker, daemon=True).start()

if __name__ == '__main__':
    _start_app()
