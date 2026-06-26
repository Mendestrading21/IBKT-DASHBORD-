# -*- coding: utf-8 -*-
"""
Track — SWING ACTIONS US (Daily) — BACKTEST + BALAYAGE DES SORTIES
==================================================================
Le moteur entre bien (expectancy +) mais SORT TROP TÔT. Ce script teste plusieurs
RÉGIMES DE SORTIE (du plus serré au plus lâche) sur le même panier/période, et
compare rendement vs drawdown vs buy & hold. ANALYSE ONLY — aucun ordre.

Moteur (entrées + scoring) identique au backtest précédent ; SEULES les sorties
changent. Données yfinance Daily 5 ans. Coûts 0,03 %/côté + slippage 0,05 %/côté.
"""
import sys, math
import numpy as np
import pandas as pd
try:
    import yfinance as yf
except Exception as e:
    print("yfinance manquant :", e); sys.exit(1)

BASKET = ["AAPL","MSFT","NVDA","AMD","GOOGL","AMZN","META","LLY","UNH","JPM",
          "V","MA","AVGO","COST","HD","NFLX","CRM","ADBE","PG","XOM"]
PERIOD, INTERVAL = "5y", "1d"
INIT_CAPITAL, RISK_PCT, ATR_MULT = 1000.0, 1.5, 2.0
CHAND_LEN = 22
EMA_FAST, EMA_MID, EMA_SLOW = 20, 50, 200
ATR_LEN, RSI_LEN, DC_LEN = 14, 14, 20
SAT_LEN, REG_LEN = 100, 50
ADAPTIVE_NORM, USE_TSTAT = True, True
ADX_TREND, CONF_TH, PB_TH, BRK_TH, EXIT_TH = 23.0, 50.0, 35.0, 40.0, 18.0
HYST_BARS, VOL_MULT, PB_TOL_ATR = 2, 1.3, 0.5
W_TREND, W_MOM, W_VOL = 40.0, 25.0, 5.0
SAT_STACK, SAT_SLOPE, SAT_MACD, SAT_ROC = 3.0, 2.0, 0.5, 3.0
ROC_LEN = 10
COMMISSION_PCT, SLIPPAGE_PCT = 0.03, 0.05

# ── régimes de sortie testés ──
EXIT_CONFIGS = [
    dict(name="Baseline (signal+chand3)",  on_signal=True,  score_exit=True,  ema_exit=True,  chand=3.0, from_start=False),
    dict(name="Sans score-exit (EMA ride)",on_signal=True,  score_exit=False, ema_exit=True,  chand=3.0, from_start=False),
    dict(name="Chandelier seul 3.0",       on_signal=False, score_exit=False, ema_exit=False, chand=3.0, from_start=False),
    dict(name="Chandelier seul 4.0",       on_signal=False, score_exit=False, ema_exit=False, chand=4.0, from_start=False),
    dict(name="Chandelier dyn 4.0",        on_signal=False, score_exit=False, ema_exit=False, chand=4.0, from_start=True),
    dict(name="Chandelier dyn 6.0",        on_signal=False, score_exit=False, ema_exit=False, chand=6.0, from_start=True),
    dict(name="Agressif ±2% fixe",         on_signal=False, score_exit=False, ema_exit=False, chand=3.0, from_start=False, fixed_pct=2.0),
]

def rma(s,n): return s.ewm(alpha=1.0/n,adjust=False).mean()
def ema(s,n): return s.ewm(span=n,adjust=False).mean()
def true_range(h,l,c):
    pc=c.shift(1); return pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
def atr(h,l,c,n): return rma(true_range(h,l,c),n)
def rsi(c,n):
    d=c.diff(); rs=rma(d.clip(lower=0),n)/rma((-d).clip(lower=0),n).replace(0,np.nan)
    return (100-100/(1+rs)).fillna(50)
def macd_hist(c):
    line=ema(c,12)-ema(c,26); return line-ema(line,9)
def dmi(h,l,c,n):
    up=h.diff(); dn=-l.diff()
    pdm=np.where((up>dn)&(up>0),up,0.0); mdm=np.where((dn>up)&(dn>0),dn,0.0)
    atn=rma(true_range(h,l,c),n).replace(0,np.nan)
    pdi=100*rma(pd.Series(pdm,index=h.index),n)/atn; mdi=100*rma(pd.Series(mdm,index=h.index),n)/atn
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return pdi.fillna(0),mdi.fillna(0),rma(dx.fillna(0),n).fillna(0)
def choppiness(h,l,c,n=14):
    s=true_range(h,l,c).rolling(n).sum(); rng=(h.rolling(n).max()-l.rolling(n).min()).replace(0,np.nan)
    return (100*np.log10(s/rng)/np.log10(n)).fillna(50)
def f_up(s): return max(0.0,(s-50.0)/50.0)
def f_dn(s): return max(0.0,(50.0-s)/50.0)
def max_dd(series):
    s=pd.Series(series); peak=s.cummax(); return ((peak-s)/peak*100).max()

def build_engine(df):
    h,l,c,o,v=df.high,df.low,df.close,df.open,df.volume
    e=pd.DataFrame(index=df.index); e["open"],e["high"],e["low"],e["close"]=o,h,l,c
    ma1,ma2,ma3=ema(c,EMA_FAST),ema(c,EMA_MID),ema(c,EMA_SLOW)
    e["ma1"],e["ma2"],e["ma3"]=ma1,ma2,ma3
    a=atr(h,l,c,ATR_LEN); e["atr"]=a; atrSafe=a.where(a>0,0.01)
    r=rsi(c,RSI_LEN); mh=macd_hist(c); pdi,mdi,adx=dmi(h,l,c,14); e["adx"]=adx
    chop=choppiness(h,l,c,14)
    volAvg=v.rolling(20).mean(); volRel=(v/volAvg).where(volAvg>0,1.0).fillna(1.0); e["volRel"]=volRel
    e["donH"]=h.rolling(DC_LEN).max().shift(1)
    e["atrExp"]=atr(h,l,c,5)>atr(h,l,c,20)
    e["chHigh"]=h.rolling(CHAND_LEN).max(); e["chATR"]=atr(h,l,c,CHAND_LEN)
    def fsat(sd,k,fx):
        return np.maximum(fx*0.15,k*sd.fillna(fx)) if ADAPTIVE_NORM else pd.Series(fx,index=df.index)
    slopeRaw=((ma3-ma3.shift(20))/atrSafe).fillna(0)
    dStack=(ma1-ma2)/atrSafe+(ma2-ma3)/atrSafe+(c-ma3)/atrSafe
    stackSub=50+50*(dStack/fsat(dStack.rolling(SAT_LEN).std(ddof=0),2.0,SAT_STACK)).clip(-1,1)
    slopeSub=50+50*(slopeRaw/fsat(slopeRaw.rolling(SAT_LEN).std(ddof=0),2.0,SAT_SLOPE)).clip(-1,1)
    adxSub=50+50*((adx-10)/40).clip(0,1)*np.where(pdi>mdi,1.0,-1.0)
    trendSub=0.45*stackSub+0.30*slopeSub+0.25*adxSub
    if USE_TSTAT:
        bx=pd.Series(np.arange(len(c)),index=c.index); rc=c.rolling(REG_LEN).corr(bx)
        ts=(rc*np.sqrt(max(1.0,REG_LEN-2.0)/(1-rc**2).clip(lower=1e-4))).fillna(0)
        trendSub=50+(trendSub-50)*(ts.abs()/2.0).clip(upper=1.0)
    e["trendSub"]=trendSub
    rsiSub=50+50*((r-50)/25).clip(-1,1)
    macdAccel=((mh-mh.shift(1))/atrSafe).fillna(0)
    macdSub=50+50*(macdAccel/fsat(macdAccel.rolling(SAT_LEN).std(ddof=0),2.0,SAT_MACD)).clip(-1,1)
    roc=(c/c.shift(ROC_LEN)-1).fillna(0); rocRaw=roc/(atrSafe/c)
    rocSub=50+50*(rocRaw/fsat(rocRaw.rolling(SAT_LEN).std(ddof=0),2.0,SAT_ROC)).clip(-1,1)
    momSub=0.50*rsiSub+0.35*macdSub+0.15*rocSub; e["momSub"]=momSub
    volSign=np.where(c>=o,1.0,-1.0); volumeSub=50+50*(((volRel-1)*volSign)/1.5).clip(-1,1)
    wSum=W_TREND+W_MOM+W_VOL
    bull=(W_TREND*trendSub.apply(f_up)+W_MOM*momSub.apply(f_up)+W_VOL*volumeSub.apply(f_up))/wSum*100
    bear=(W_TREND*trendSub.apply(f_dn)+W_MOM*momSub.apply(f_dn)+W_VOL*volumeSub.apply(f_dn))/wSum*100
    e["net"]=bull-bear
    trending=(adx>=ADX_TREND)&(chop<50); net=e["net"]
    e["rawRegime"]=np.select([(net>50)&trending,net>15,(net<-50)&trending,net<-15],[2,1,-2,-1],default=0)
    e["stackUpC"]=(ma1>ma2)&(ma2>ma3)&(ma3>=ma3.shift(3))
    return e.dropna()

def precompute_signals(e):
    rows=e.to_dict("records"); n=len(rows)
    regimeStable=regimePend=regimeCount=0; stCB=stPB=False; sigs=[]
    for i in range(n):
        raw=int(rows[i]["rawRegime"])
        if raw==regimeStable: regimeCount=0
        elif raw==regimePend:
            regimeCount+=1
            if regimeCount>=HYST_BARS: regimeStable=raw; regimeCount=0
        else: regimePend=raw; regimeCount=1
        gs=rows[i]["net"]; adx=rows[i]["adx"]; c=rows[i]["close"]; o=rows[i]["open"]
        l=rows[i]["low"]; ma1=rows[i]["ma1"]; a=rows[i]["atr"]
        tol=PB_TOL_ATR*a/c; donH=rows[i]["donH"]; volRel=rows[i]["volRel"]; atrExp=rows[i]["atrExp"]
        c1=rows[i-1]["close"] if i>0 else c; donH1=rows[i-1]["donH"] if i>0 else donH
        nsCB=(gs>CONF_TH) and rows[i]["stackUpC"] and (adx>ADX_TREND)
        sigCB=nsCB and not stCB; stCB=nsCB
        nsPB=(regimeStable>=1) and (l<=ma1*(1+tol)) and (c>o) and (c>ma1) and (gs>PB_TH)
        sigPB=nsPB and not stPB; stPB=nsPB
        sigBR=(c>donH) and (c1<=donH1) and (gs>BRK_TH) and ((volRel>VOL_MULT) or atrExp) and (regimeStable!=0)
        sigs.append(sigCB or sigPB or sigBR)
    return rows, sigs

def run(rows, sigs, idx, cfg):
    n=len(rows)
    fp=cfg.get("fixed_pct")
    cap=INIT_CAPITAL; qty=0.0; entryPx=None; risk=None; trail=None; tp=None; slv=None; pend=None
    trades=[]; eq=[]; bip=0
    sl=SLIPPAGE_PCT/100.0; cm=COMMISSION_PCT/100.0
    def close_pos(i,price,reason):
        nonlocal cap,qty,entryPx,trail,risk,tp,slv
        gross=(price-entryPx)*qty; comm=(entryPx*qty+price*qty)*cm; pnl=gross-comm; cap+=pnl
        trades.append(dict(pnl=pnl,R=(pnl/(qty*risk) if (qty and risk) else np.nan),reason=reason))
        qty=0.0; entryPx=None; trail=None; risk=None; tp=None; slv=None
    for i in range(n):
        row=rows[i]; o=row["open"]; h=row["high"]; l=row["low"]; c=row["close"]
        c1=rows[i-1]["close"] if i>0 else c; ma2=row["ma2"]; ma2p=rows[i-1]["ma2"] if i>0 else ma2
        net=row["net"]; net1=rows[i-1]["net"] if i>0 else net
        if pend=="exit" and qty>0:
            close_pos(i,o*(1-sl),"signal"); pend=None
        elif pend=="long" and qty==0:
            stopDist=(o*fp/100.0) if fp else ATR_MULT*(row["atr"] if row["atr"]>0 else 0.01)
            q=min((cap*RISK_PCT/100.0)/stopDist, cap/o)
            if q>0:
                qty=q; entryPx=o*(1+sl); risk=stopDist
                if fp:
                    tp=entryPx*(1+fp/100.0); slv=entryPx*(1-fp/100.0)
                else:
                    trail=entryPx-stopDist
            pend=None
        if fp:
            # TP/SL fixes ±X% (la règle "agressif +2/-2")
            if qty>0 and l<=slv:
                close_pos(i,(slv if o>=slv else o)*(1-sl),"SL")
            elif qty>0 and h>=tp:
                close_pos(i,(tp if o<=tp else o)*(1-sl),"TP")
        else:
            # stop intrabar
            if qty>0 and trail is not None and l<=trail:
                close_pos(i,(trail if o>=trail else o)*(1-sl),"stop")
            # trailing chandelier
            if qty>0 and trail is not None:
                ch=row["chHigh"]-cfg["chand"]*row["chATR"]
                if cfg["from_start"] or (c-entryPx)/risk>=1.0:
                    trail=max(trail,min(ch,c*0.999))
        # sortie sur signal (selon cfg, hors mode fixe)
        exitSig=False
        if (not fp) and cfg.get("on_signal") and qty>0:
            if cfg.get("score_exit") and (net<EXIT_TH) and (net1>=EXIT_TH): exitSig=True
            if cfg.get("ema_exit") and (c<ma2) and (c1>=ma2p): exitSig=True
        if qty==0 and pend is None and i<n-1 and sigs[i]: pend="long"
        elif qty>0 and pend is None and i<n-1 and exitSig: pend="exit"
        if qty>0: bip+=1
        eq.append(cap)
    return trades, eq, cap, bip

def main():
    print(f"[data] yfinance {len(BASKET)} titres {PERIOD} {INTERVAL} ...")
    raw=yf.download(BASKET,period=PERIOD,interval=INTERVAL,auto_adjust=True,progress=False,group_by="ticker")
    engines={}; bh={}
    for tk in BASKET:
        try:
            df=raw[tk].rename(columns=str.lower)[["open","high","low","close","volume"]].dropna()
        except Exception: continue
        if len(df)<300: continue
        e=build_engine(df)
        if len(e)<50: continue
        rows,sigs=precompute_signals(e)
        engines[tk]=(rows,sigs,list(e.index))
        ec=e["close"]; bh[tk]=((ec.iloc[-1]/ec.iloc[0]-1)*100, max_dd((ec/ec.iloc[0]).tolist()))
    bh_ret=np.mean([v[0] for v in bh.values()]); bh_dd=np.mean([v[1] for v in bh.values()])

    print("\n"+"="*78)
    print(" TRACK — SWING ACTIONS — BALAYAGE DES SORTIES  ({} titres, {} Daily)".format(len(engines),PERIOD))
    print("="*78)
    print(f" BUY & HOLD (référence) : rendement moyen {bh_ret:+.1f} %   |   maxDD moyen {bh_dd:.1f} %")
    print("-"*78)
    print(f" {'RÉGIME DE SORTIE':28s}{'trad':>5s}{'expo':>6s}{'win%':>6s}{'PF':>6s}{'expR':>6s}{'STRAT%':>8s}{'maxDD':>7s}{'>B&H':>5s}")
    for cfg in EXIT_CONFIGS:
        allpnl=[]; allR=[]; rets=[]; dds=[]; expos=[]; beat=0; ntr=0
        for tk,(rows,sigs,idx) in engines.items():
            tr,eq,cap,bip=run(rows,sigs,idx,cfg)
            ntr+=len(tr)
            for t in tr: allpnl.append(t["pnl"]); allR.append(t["R"])
            r=(cap/INIT_CAPITAL-1)*100; rets.append(r); dds.append(max_dd(eq)); expos.append(bip/len(rows)*100)
            if r>bh[tk][0]: beat+=1
        gp=sum(p for p in allpnl if p>0); gl=-sum(p for p in allpnl if p<=0)
        pf=gp/gl if gl>0 else float("inf")
        wr=sum(1 for p in allpnl if p>0)/len(allpnl)*100 if allpnl else 0
        expR=np.nanmean(allR) if allR else 0
        print(f" {cfg['name']:28s}{ntr:5d}{np.mean(expos):5.0f}%{wr:6.1f}{pf:6.2f}{expR:+6.2f}{np.mean(rets):+8.1f}{np.mean(dds):6.1f}%{beat:4d}")
    print("="*78)
    print(" Lecture : on cherche STRAT% qui se rapproche du B&H tout en gardant maxDD bas.")
    print(" ANALYSE ONLY — mesure d'edge honnête, pas un conseil.")
    print("="*78)

if __name__=="__main__":
    main()
