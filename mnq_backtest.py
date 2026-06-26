# -*- coding: utf-8 -*-
"""
Track — MNQ Intraday — BACKTEST PYTHON (port fidèle de Track_Strategy_MNQ.pine)
================================================================================
But : MESURER l'edge honnêtement sur des mois de données 5 min, ce que TradingView
ne peut pas (feed différé ~300 barres). ANALYSE ONLY — aucun ordre, aucune exécution.

Données : NQ front-month 5 min via yfinance (60 j). Le prix de NQ = celui de MNQ
(même indice Nasdaq-100) ; on applique la valeur du point MNQ (2 $) pour le P&L et
le sizing. Capital 1000 $, risque 1,5 %/trade, contrats entiers, session US RTH,
stop ATR + trailing chandelier, garde-fous journaliers. Long + Short.

Hypothèses de fill (réalistes, énoncées honnêtement) :
  - entrées/sorties-signal : au PROCHAIN open (comme le défaut TradingView)
  - stops : intrabar (au stop, ou à l'open si gap au-delà) + slippage
  - commission 0,52 $/contrat/côté, slippage 1 tick (0,25 pt) par côté
"""
import sys
import math
import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception as e:
    print("yfinance manquant :", e)
    sys.exit(1)

# ───────────────────────── PARAMÈTRES (= inputs de la .pine) ─────────────────
TICKER        = "NQ=F"        # NQ front-month continu (proxy prix MNQ)
PERIOD        = "60d"
INTERVAL      = "5m"

POINT_VALUE   = 2.0           # MNQ : 1 pt = 2 $
MINTICK       = 0.25
INIT_CAPITAL  = 1000.0
RISK_PCT      = 1.5
ATR_MULT      = 2.0
CHAND_MULT    = 2.5
CHAND_LEN     = 22
MIN_STOP_TICKS= 8
FORCE_MIN1    = True
MAX_CONTRACTS = 2
EXIT_ON_SIGNAL= True

EMA_FAST, EMA_MID, EMA_SLOW = 9, 21, 50
ATR_LEN, RSI_LEN = 14, 14
DC_LEN = 20
SAT_LEN = 100
REG_LEN = 50
ADAPTIVE_NORM = True
USE_TSTAT = True

# Mode 'Balanced'
ADX_TREND = 23.0
CONF_TH   = 50.0
PB_TH     = 35.0
BRK_TH    = 40.0
EXIT_TH   = 18.0
HYST_BARS = 2
VOL_MULT  = 1.3
PB_TOL_ATR = 0.5

# Poids / saturations
W_TREND, W_MOM, W_VOL = 40.0, 25.0, 5.0
W_MTF = 0.0                    # MTF désactivé dans ce backtest mono-TF (poids 0 -> wSum 70)
SAT_STACK, SAT_SLOPE, SAT_MACD, SAT_ROC = 3.0, 2.0, 0.5, 3.0
ROC_LEN = 10

import os
TRADE_LONGS  = os.environ.get("LONGS", "1") == "1"
TRADE_SHORTS = os.environ.get("SHORTS", "1") == "1"

# Garde-fous journaliers / session
SESS_START = (9, 30)          # heure NY
SESS_END   = (16, 0)
FLATTEN_EOD   = True
USE_DAILY_STOP= True
DAILY_LOSS_PCT= 3.0
MAX_TRADES    = 3

COMMISSION_PER_SIDE = 0.52    # $/contrat/côté
SLIPPAGE_TICKS = 1

# ───────────────────────── INDICATEURS (formules Pine) ──────────────────────
def rma(s, n):                 # Wilder (ta.rma) = ewm alpha=1/n
    return s.ewm(alpha=1.0/n, adjust=False).mean()

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def true_range(h, l, c):
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)

def atr(h, l, c, n):
    return rma(true_range(h, l, c), n)

def rsi(c, n):
    d = c.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    rs = rma(up, n) / rma(dn, n).replace(0.0, np.nan)
    return (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)

def macd_hist(c):
    line = ema(c, 12) - ema(c, 26)
    sig = ema(line, 9)
    return line - sig

def dmi(h, l, c, n):
    up = h.diff()
    dn = -l.diff()
    plus_dm  = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(h, l, c)
    atr_n = rma(tr, n).replace(0.0, np.nan)
    plus_di  = 100.0 * rma(pd.Series(plus_dm,  index=h.index), n) / atr_n
    minus_di = 100.0 * rma(pd.Series(minus_dm, index=h.index), n) / atr_n
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx = rma(dx.fillna(0.0), n)
    return plus_di.fillna(0.0), minus_di.fillna(0.0), adx.fillna(0.0)

def choppiness(h, l, c, n=14):
    tr = true_range(h, l, c)
    s = tr.rolling(n).sum()
    rng = h.rolling(n).max() - l.rolling(n).min()
    rng = rng.replace(0.0, np.nan)
    return (100.0 * np.log10(s / rng) / np.log10(n)).fillna(50.0)

def percentrank(s, n):
    return s.rolling(n).apply(lambda x: (x[:-1] < x[-1]).mean() * 100.0, raw=True)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def f_norm(x, sat):
    return 50.0 + 50.0 * clamp(x / sat, -1.0, 1.0)

def f_up(s):
    return max(0.0, (s - 50.0) / 50.0)

def f_dn(s):
    return max(0.0, (50.0 - s) / 50.0)

# ───────────────────────── CHARGEMENT DONNÉES ───────────────────────────────
def load_data():
    print(f"[data] yfinance {TICKER} {PERIOD} {INTERVAL} ...")
    df = yf.download(TICKER, period=PERIOD, interval=INTERVAL,
                     auto_adjust=False, progress=False)
    if df is None or len(df) == 0:
        print("[data] ECHEC NQ=F, tentative MNQ=F ...")
        df = yf.download("MNQ=F", period=PERIOD, interval=INTERVAL,
                         auto_adjust=False, progress=False)
    if df is None or len(df) == 0:
        raise SystemExit("[data] aucune donnée intraday récupérée.")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy()
    df = df.dropna()
    # Index -> America/New_York
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df.index = idx.tz_convert("America/New_York")
    return df

# ───────────────────────── MOTEUR (sous-scores -> net) ──────────────────────
def build_engine(df):
    h, l, c, o, v = df.high, df.low, df.close, df.open, df.volume
    e = pd.DataFrame(index=df.index)
    e["open"], e["high"], e["low"], e["close"] = o, h, l, c
    ma1, ma2, ma3 = ema(c, EMA_FAST), ema(c, EMA_MID), ema(c, EMA_SLOW)
    e["ma1"], e["ma2"], e["ma3"] = ma1, ma2, ma3
    a = atr(h, l, c, ATR_LEN)
    e["atr"] = a
    atrSafe = a.where(a > 0, MINTICK)
    e["atrSafe"] = atrSafe
    r = rsi(c, RSI_LEN)
    mh = macd_hist(c)
    plusDI, minusDI, adx = dmi(h, l, c, 14)
    e["adx"] = adx
    chop = choppiness(h, l, c, 14)
    e["chop"] = chop
    volAvg = v.rolling(20).mean()
    volRel = (v / volAvg).where(volAvg > 0, 1.0).fillna(1.0)
    e["volRel"] = volRel
    e["donH"] = h.rolling(DC_LEN).max().shift(1)
    e["donL"] = l.rolling(DC_LEN).min().shift(1)
    e["atrExp"] = atr(h, l, c, 5) > atr(h, l, c, 20)
    e["chandRaw"]   = h.rolling(CHAND_LEN).max() - CHAND_MULT * atr(h, l, c, CHAND_LEN)
    e["chandRawSh"] = l.rolling(CHAND_LEN).min() + CHAND_MULT * atr(h, l, c, CHAND_LEN)

    # Trend sub
    slopeRaw = ((ma3 - ma3.shift(20)) / atrSafe).fillna(0.0)
    dStack = (ma1 - ma2) / atrSafe + (ma2 - ma3) / atrSafe + (c - ma3) / atrSafe
    sdStack = dStack.rolling(SAT_LEN).std(ddof=0)
    sdSlope = slopeRaw.rolling(SAT_LEN).std(ddof=0)
    def fsat(sd, k, fixed):
        if not ADAPTIVE_NORM:
            return pd.Series(fixed, index=df.index)
        return np.maximum(fixed * 0.15, k * sd.fillna(fixed))
    stackSub = (50.0 + 50.0 * (dStack / fsat(sdStack, 2.0, SAT_STACK)).clip(-1, 1))
    slopeSub = (50.0 + 50.0 * (slopeRaw / fsat(sdSlope, 2.0, SAT_SLOPE)).clip(-1, 1))
    adxStr = ((adx - 10.0) / 40.0).clip(0, 1)
    adxSub = 50.0 + 50.0 * adxStr * np.where(plusDI > minusDI, 1.0, -1.0)
    trendSub = 0.45 * stackSub + 0.30 * slopeSub + 0.25 * adxSub
    # t-stat
    if USE_TSTAT:
        bar_ix = pd.Series(np.arange(len(c)), index=c.index)
        rCorr = c.rolling(REG_LEN).corr(bar_ix)
        tStat = (rCorr * np.sqrt(max(1.0, REG_LEN - 2.0) / (1.0 - rCorr**2).clip(lower=1e-4))).fillna(0.0)
        sigFac = (tStat.abs() / 2.0).clip(upper=1.0)
        trendSub = 50.0 + (trendSub - 50.0) * sigFac
    e["trendSub"] = trendSub

    # Momentum sub
    rsiSub = (50.0 + 50.0 * ((r - 50.0) / 25.0).clip(-1, 1))
    macdAccel = ((mh - mh.shift(1)) / atrSafe).fillna(0.0)
    sdMacd = macdAccel.rolling(SAT_LEN).std(ddof=0)
    macdSub = (50.0 + 50.0 * (macdAccel / fsat(sdMacd, 2.0, SAT_MACD)).clip(-1, 1))
    roc = (c / c.shift(ROC_LEN) - 1.0).fillna(0.0)
    rocRaw = roc / (atrSafe / c)
    sdRoc = rocRaw.rolling(SAT_LEN).std(ddof=0)
    rocSub = (50.0 + 50.0 * (rocRaw / fsat(sdRoc, 2.0, SAT_ROC)).clip(-1, 1))
    momSub = 0.50 * rsiSub + 0.35 * macdSub + 0.15 * rocSub
    e["momSub"] = momSub

    # Volume sub
    volSign = np.where(c >= o, 1.0, -1.0)
    volumeSub = (50.0 + 50.0 * (((volRel - 1.0) * volSign) / 1.5).clip(-1, 1))
    e["volumeSub"] = volumeSub

    # Scoring
    wSum = W_TREND + W_MOM + W_MTF + W_VOL
    bull = (W_TREND * trendSub.apply(f_up) + W_MOM * momSub.apply(f_up) + W_VOL * volumeSub.apply(f_up)) / wSum * 100.0
    bear = (W_TREND * trendSub.apply(f_dn) + W_MOM * momSub.apply(f_dn) + W_VOL * volumeSub.apply(f_dn)) / wSum * 100.0
    e["net"] = bull - bear

    # Régime brut
    trending = (adx >= ADX_TREND) & (chop < 50)
    net = e["net"]
    raw = np.select(
        [(net > 50) & trending, net > 15, (net < -50) & trending, net < -15],
        [2, 1, -2, -1], default=0)
    e["rawRegime"] = raw
    e["stackUpC"] = (ma1 > ma2) & (ma2 > ma3) & (ma3 >= ma3.shift(3))
    e["stackDnC"] = (ma1 < ma2) & (ma2 < ma3) & (ma3 <= ma3.shift(3))
    e["plusDI_gt"] = plusDI > minusDI
    return e.dropna()

# ───────────────────────── SIMULATION (boucle barre par barre) ──────────────
def in_session(ts):
    mins = ts.hour * 60 + ts.minute
    return (SESS_START[0]*60+SESS_START[1]) <= mins < (SESS_END[0]*60+SESS_END[1])

def backtest(e):
    rows = e.to_dict("records")
    idx = list(e.index)
    n = len(rows)

    # régime + hystérésis + signal state machines (séquentiel)
    regimeStable = 0; regimePend = 0; regimeCount = 0
    stCB = stPB = stCBs = stPBs = False
    sig = []  # par barre : dict des signaux/regime gelés à la clôture
    for i in range(n):
        raw = int(rows[i]["rawRegime"])
        if raw == regimeStable:
            regimeCount = 0
        elif raw == regimePend:
            regimeCount += 1
            if regimeCount >= HYST_BARS:
                regimeStable = raw; regimeCount = 0
        else:
            regimePend = raw; regimeCount = 1
        gs = rows[i]["net"]; adx = rows[i]["adx"]
        c = rows[i]["close"]; o = rows[i]["open"]; h = rows[i]["high"]; l = rows[i]["low"]
        ma1 = rows[i]["ma1"]; ma2 = rows[i]["ma2"]
        a = rows[i]["atr"]
        tol = PB_TOL_ATR * a / c
        donH = rows[i]["donH"]; donL = rows[i]["donL"]
        volRel = rows[i]["volRel"]; atrExp = rows[i]["atrExp"]
        c1 = rows[i-1]["close"] if i > 0 else c
        donH1 = rows[i-1]["donH"] if i > 0 else donH
        donL1 = rows[i-1]["donL"] if i > 0 else donL
        net1 = rows[i-1]["net"] if i > 0 else gs

        # LONG
        nsCB = (gs > CONF_TH) and rows[i]["stackUpC"] and (adx > ADX_TREND)
        sigConfBull = nsCB and not stCB; stCB = nsCB
        touchedUp = l <= ma1 * (1.0 + tol)
        reboundUp = (c > o) and (c > ma1)
        nsPB = (regimeStable >= 1) and touchedUp and reboundUp and (gs > PB_TH)
        sigPullback = nsPB and not stPB; stPB = nsPB
        brkUp = (c > donH) and (c1 <= donH1)
        sigBreakout = brkUp and (gs > BRK_TH) and ((volRel > VOL_MULT) or atrExp) and (regimeStable != 0)
        # SHORT
        nsCBs = (gs < -CONF_TH) and rows[i]["stackDnC"] and (adx > ADX_TREND)
        sigConfBear = nsCBs and not stCBs; stCBs = nsCBs
        touchedDn = h >= ma1 * (1.0 - tol)
        reboundDn = (c < o) and (c < ma1)
        nsPBs = (regimeStable <= -1) and touchedDn and reboundDn and (gs < -PB_TH)
        sigPullbackS = nsPBs and not stPBs; stPBs = nsPBs
        brkDn = (c < donL) and (c1 >= donL1)
        sigBreakdown = brkDn and (gs < -BRK_TH) and ((volRel > VOL_MULT) or atrExp) and (regimeStable != 0)

        longEntry  = TRADE_LONGS  and (sigConfBull or sigPullback or sigBreakout)
        shortEntry = TRADE_SHORTS and (sigConfBear or sigPullbackS or sigBreakdown)
        gsCrossDn = (gs < EXIT_TH) and (net1 >= EXIT_TH)
        emaLost   = (c < ma2) and ((rows[i-1]["close"] if i > 0 else c) >= (rows[i-1]["ma2"] if i > 0 else ma2))
        exitLong  = EXIT_ON_SIGNAL and (gsCrossDn or emaLost)
        gsCrossUp = (gs > -EXIT_TH) and (net1 <= -EXIT_TH)
        emaReclaim= (c > ma2) and ((rows[i-1]["close"] if i > 0 else c) <= (rows[i-1]["ma2"] if i > 0 else ma2))
        exitShort = EXIT_ON_SIGNAL and (gsCrossUp or emaReclaim)
        sig.append(dict(longEntry=longEntry, shortEntry=shortEntry,
                        exitLong=exitLong, exitShort=exitShort))

    # ── exécution ──
    cap = INIT_CAPITAL
    pos = 0            # +contrats / -contrats
    entryPx = None; riskAtEntry = None; trailStop = None; pendingRisk = None
    pend = None        # ('long'|'short'|'exit', ) à filler au prochain open
    trades = []
    equity_curve = []
    dayStartEq = None; tradesToday = 0; prevInSess = False
    sumRiskPct = 0.0; nEntries = 0
    slip = SLIPPAGE_TICKS * MINTICK

    def close_pos(i, price, reason):
        nonlocal cap, pos, entryPx, trailStop, riskAtEntry, pendingRisk
        gross = (price - entryPx) * pos * POINT_VALUE
        comm = COMMISSION_PER_SIDE * abs(pos) * 2  # entrée + sortie
        pnl = gross - comm
        cap += pnl
        trades.append(dict(time=idx[i], side="L" if pos > 0 else "S",
                           qty=abs(pos), entry=entryPx, exit=price,
                           pnl=pnl, R=(pnl / pendingRisk if pendingRisk else np.nan),
                           reason=reason))
        pos = 0; entryPx = None; trailStop = None; riskAtEntry = None; pendingRisk = None

    for i in range(n):
        ts = idx[i]; row = rows[i]
        o = row["open"]; h = row["high"]; l = row["low"]; c = row["close"]
        nowSess = in_session(ts)
        if nowSess and not prevInSess:
            dayStartEq = cap; tradesToday = 0
        # a) fill pending au open
        if pend is not None:
            if pend == "exit" and pos != 0:
                px = o - slip if pos > 0 else o + slip
                close_pos(i, px, "signal")
            elif pend == "long" and pos == 0:
                stopDist = max(ATR_MULT * (row["atr"] if row["atr"] > 0 else MINTICK), MIN_STOP_TICKS * MINTICK)
                dollarRisk = cap * RISK_PCT / 100.0
                raw = dollarRisk / (stopDist * POINT_VALUE)
                q = int(math.floor(raw))
                if q < 1 and FORCE_MIN1:
                    q = 1
                q = min(q, MAX_CONTRACTS)
                if q >= 1:
                    pos = q; entryPx = o + slip; riskAtEntry = stopDist
                    trailStop = entryPx - stopDist
                    pendingRisk = q * stopDist * POINT_VALUE
                    sumRiskPct += pendingRisk / cap * 100.0; nEntries += 1
                    tradesToday += 1
            elif pend == "short" and pos == 0:
                stopDist = max(ATR_MULT * (row["atr"] if row["atr"] > 0 else MINTICK), MIN_STOP_TICKS * MINTICK)
                dollarRisk = cap * RISK_PCT / 100.0
                raw = dollarRisk / (stopDist * POINT_VALUE)
                q = int(math.floor(raw))
                if q < 1 and FORCE_MIN1:
                    q = 1
                q = min(q, MAX_CONTRACTS)
                if q >= 1:
                    pos = -q; entryPx = o - slip; riskAtEntry = stopDist
                    trailStop = entryPx + stopDist
                    pendingRisk = q * stopDist * POINT_VALUE
                    sumRiskPct += pendingRisk / cap * 100.0; nEntries += 1
                    tradesToday += 1
            pend = None

        # b) stop intrabar
        if pos > 0 and trailStop is not None and l <= trailStop:
            px = trailStop if o >= trailStop else o
            close_pos(i, px - slip, "stop")
        elif pos < 0 and trailStop is not None and h >= trailStop:
            px = trailStop if o <= trailStop else o
            close_pos(i, px + slip, "stop")

        # c) flatten EOD / stop journalier (au close de la barre concernée)
        dayLossHit = USE_DAILY_STOP and dayStartEq is not None and cap <= dayStartEq * (1 - DAILY_LOSS_PCT/100.0)
        if pos != 0 and ((FLATTEN_EOD and not nowSess) or dayLossHit):
            close_pos(i, (c - slip) if pos > 0 else (c + slip), "EOD/jour")

        # d) trailing update (après stop, sur close)
        if pos > 0 and trailStop is not None:
            rNow = (c - entryPx) / riskAtEntry if riskAtEntry else 0
            if rNow >= 1.0:
                trailStop = max(trailStop, min(row["chandRaw"], c - MINTICK))
        elif pos < 0 and trailStop is not None:
            rNow = (entryPx - c) / riskAtEntry if riskAtEntry else 0
            if rNow >= 1.0:
                trailStop = min(trailStop, max(row["chandRawSh"], c + MINTICK))

        # e) décisions à la clôture -> ordre au prochain open
        canTrade = nowSess and (tradesToday < MAX_TRADES) and not dayLossHit
        s = sig[i]
        if pos == 0 and pend is None and canTrade and i < n - 1:
            if s["longEntry"]:
                pend = "long"
            elif s["shortEntry"]:
                pend = "short"
        elif pos != 0 and pend is None and i < n - 1:
            if (pos > 0 and s["exitLong"]) or (pos < 0 and s["exitShort"]):
                pend = "exit"

        equity_curve.append((ts, cap))
        prevInSess = nowSess

    return trades, equity_curve

# ───────────────────────── RAPPORT ──────────────────────────────────────────
def report(df, e, trades, eq):
    print("\n" + "=" * 64)
    print(" TRACK — MNQ INTRADAY — BACKTEST (port Python de la .pine)")
    print("=" * 64)
    print(f" Donnees   : {TICKER} {INTERVAL}  |  {df.index[0]}  ->  {df.index[-1]}")
    print(f" Barres    : {len(df)}  (apres warmup moteur : {len(e)})")
    print(f" Capital   : {INIT_CAPITAL:.0f} $  |  risque cible {RISK_PCT}%/trade  |  pt MNQ {POINT_VALUE} $")
    print("-" * 64)
    if not trades:
        print(" Aucun trade genere sur la periode.")
        return
    t = pd.DataFrame(trades)
    wins = t[t.pnl > 0]; losses = t[t.pnl <= 0]
    net = t.pnl.sum()
    gp = wins.pnl.sum(); gl = -losses.pnl.sum()
    pf = gp / gl if gl > 0 else float("inf")
    wr = len(wins) / len(t) * 100.0
    exp_r = t.R.mean()
    # max drawdown sur la courbe d'equity
    eqs = pd.Series([x[1] for x in eq])
    peak = eqs.cummax(); dd = (peak - eqs) / peak * 100.0
    maxdd = dd.max()
    avg_risk = sumRiskPct_holder["v"] / nEntries_holder["v"] if nEntries_holder["v"] else float("nan")
    longs = t[t.side == "L"]; shorts = t[t.side == "S"]
    print(f" Trades        : {len(t)}   (L {len(longs)} / S {len(shorts)})")
    print(f" P&L net       : {net:+.2f} $   ({net/INIT_CAPITAL*100:+.1f} %)")
    print(f" Capital final : {INIT_CAPITAL + net:.2f} $")
    print(f" Profit factor : {pf:.2f}")
    print(f" Win rate      : {wr:.1f} %")
    print(f" Expectancy    : {exp_r:+.2f} R")
    print(f" Max drawdown  : {maxdd:.1f} %")
    print(f" Risque REEL/tr: {avg_risk:.1f} %   (cible {RISK_PCT}%)")
    if len(longs):
        print(f"   Longs  : {len(longs)} trades, {longs.pnl.sum():+.2f} $, win {len(longs[longs.pnl>0])/len(longs)*100:.0f}%")
    if len(shorts):
        print(f"   Shorts : {len(shorts)} trades, {shorts.pnl.sum():+.2f} $, win {len(shorts[shorts.pnl>0])/len(shorts)*100:.0f}%")
    print("-" * 64)
    print(" Sorties par motif :", dict(t.reason.value_counts()))
    print(" 5 derniers trades :")
    for _, r in t.tail(5).iterrows():
        print(f"   {r['time']:%Y-%m-%d %H:%M}  {r['side']} x{r['qty']}  "
              f"{r['entry']:.1f}->{r['exit']:.1f}  {r['pnl']:+.2f}$  ({r['reason']})")
    print("=" * 64)
    print(" ANALYSE ONLY — aucune execution. Mesure d'edge, pas un conseil.")
    print("=" * 64)

# holders pour passer les compteurs au report sans variable globale lourde
sumRiskPct_holder = {"v": 0.0}
nEntries_holder = {"v": 0}

if __name__ == "__main__":
    df = load_data()
    e = build_engine(df)
    # rejoue la sim et capture les compteurs de risque
    import types
    global_ns = globals()
    trades, eq = backtest(e)
    # récup compteurs via re-calcul rapide (sumRiskPct/nEntries internes) :
    # on relit depuis l'objet trades (pendingRisk déjà appliqué) -> approx risque réel
    if trades:
        # risque réel moyen = moyenne du risque $ initial / capital au moment de l'entrée,
        # approx via |pnl/R| (= pendingRisk) / capital initial
        tdf = pd.DataFrame(trades)
        risk_dollars = (tdf.pnl / tdf.R).abs()
        sumRiskPct_holder["v"] = float((risk_dollars / INIT_CAPITAL * 100.0).sum())
        nEntries_holder["v"] = len(tdf)
    report(df, e, trades, eq)
