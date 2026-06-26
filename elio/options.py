"""
elio/options.py — Moteur OPTIONS INTELLIGENCE (cahier §6).

Pour les meilleurs setups du scanner, tire la VRAIE chaîne d'options (yfinance),
calcule les greeks (Black-Scholes maison), score la "suitability" (elio.scoring),
filtre les contrats sales (spread large / OI faible / IV extrême) et CLASSE les
meilleures options à trader : CALLS sur les haussiers, PUTS sélectifs sur les
baissiers. Échéances ciblées 6 / 9 / 12 mois (profil utilisateur).

⛔ ANALYSE ONLY — aucune exécution. Recommandation pédagogique, pas un ordre.
"""
import math
from datetime import datetime

import yfinance as yf

from . import scoring, config

R = 0.045


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


def _npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def _greeks(S, K, T, sig, is_call):
    if T <= 0 or sig <= 0 or S <= 0 or K <= 0:
        return 0.0, 0.0, 0.0, 0.0
    srt = sig * math.sqrt(T)
    d1 = (math.log(S / K) + (R + 0.5 * sig * sig) * T) / srt
    d2 = d1 - srt
    pdf = _npdf(d1)
    delta = _ncdf(d1) if is_call else _ncdf(d1) - 1.0
    gamma = pdf / (S * srt)
    vega = S * pdf * math.sqrt(T) / 100.0
    term = -(S * pdf * sig) / (2.0 * math.sqrt(T))
    theta = ((term - R * K * math.exp(-R * T) * _ncdf(d2)) if is_call
             else (term + R * K * math.exp(-R * T) * _ncdf(-d2))) / 365.0
    return delta, gamma, theta, vega


def _bs_price(S, K, T, sig, is_call):
    if T <= 0 or sig <= 0:
        return max((S - K) if is_call else (K - S), 0.0)
    srt = sig * math.sqrt(T)
    d1 = (math.log(S / K) + (R + 0.5 * sig * sig) * T) / srt
    d2 = d1 - srt
    if is_call:
        return S * _ncdf(d1) - K * math.exp(-R * T) * _ncdf(d2)
    return K * math.exp(-R * T) * _ncdf(-d2) - S * _ncdf(-d1)


def _iv_from_price(S, K, T, price, is_call):
    """IV implicite par bissection (fallback quand yfinance ne donne pas d'IV fiable)."""
    if price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    lo, hi = 0.02, 3.0
    if _bs_price(S, K, T, hi, is_call) < price:   # prix hors bornes
        return None
    for _ in range(40):
        m = (lo + hi) / 2.0
        if _bs_price(S, K, T, m, is_call) < price:
            lo = m
        else:
            hi = m
    return round((lo + hi) / 2.0, 4)


def _prob_above(S, K, T, sig):
    """P(S_T > K) sous le modèle lognormal (risque-neutre) = N(d2). Sert au calcul des probabilités."""
    if S <= 0 or K <= 0 or T <= 0 or sig <= 0:
        return 0.0
    d2 = (math.log(S / K) + (R - 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    return _ncdf(d2)


def option_quality(c, spot):
    """💎 OPTION QUALITY SCORE /100 (delta /20, échéance /15, spread /15, OI /15,
    volume /10, IV /10, théta /10, breakeven /5). Pour juger la PROPRETÉ du contrat."""
    d = abs(_f(c.get('delta')))
    dte = _i(c.get('dte'))
    sp = c.get('spread')
    oi = _i(c.get('oi'))
    vol = _i(c.get('vol'))
    iv = _f(c.get('iv'))
    tb = _f(c.get('theta_burn'))
    be = _f(c.get('be'))
    bk = c.get('bucket')
    qd = 20 if 0.70 <= d <= 0.88 else 15 if (0.62 <= d < 0.70 or 0.88 < d <= 0.92) else 10 if 0.52 <= d < 0.62 else 5
    qe = {'long': 15, 'moyen': 11, 'court': 6}.get(bk, 8)
    s = sp if sp is not None else 12
    qs = 15 if s < 2 else 12 if s < 4 else 9 if s < 6 else 5 if s < 10 else 2
    qo = 15 if oi > 5000 else 12 if oi > 2000 else 9 if oi > 1000 else 6 if oi > 500 else 3 if oi > 100 else 0
    qv = 10 if vol > 500 else 7 if vol > 100 else 5 if vol > 50 else 3 if vol > 10 else 1
    qi = 10 if iv < 35 else 8 if iv < 50 else 5 if iv < 70 else 3 if iv < 90 else 1
    qt = 10 if tb < 0.3 else 8 if tb < 0.6 else 6 if tb < 1.0 else 4 if tb < 1.5 else 2
    bedist = abs(be - spot) / spot * 100 if spot else 99
    qb = 5 if bedist < 6 else 4 if bedist < 10 else 3 if bedist < 15 else 1
    total = qd + qe + qs + qo + qv + qi + qt + qb
    return {'score': total,
            'parts': {'Delta': [qd, 20], 'Échéance': [qe, 15], 'Spread': [qs, 15], 'OI': [qo, 15],
                      'Volume': [qv, 10], 'IV': [qi, 10], 'Théta': [qt, 10], 'Breakeven': [qb, 5]}}


def breakeven_check(c, spot):
    """Le breakeven est-il atteignable ? distance + hausse mensuelle nécessaire + verdict."""
    be = _f(c.get('be'))
    dte = max(_i(c.get('dte')), 1)
    is_call = c.get('type') == 'CALL'
    dist = (be - spot) / spot * 100 if spot else 0.0
    monthly = abs(dist) / max(dte / 30.0, 0.1)
    verdict = 'réaliste' if monthly < 2.5 else 'agressif' if monthly < 5 else 'danger théta'
    return {'be': round(be, 2), 'dist': round(dist, 1), 'monthly': round(monthly, 2),
            'verdict': verdict, 'dir': 'call' if is_call else 'put'}


def scenarios(c, spot, levels, horizon_days=40):
    """3 scénarios obligatoires : pessimiste (stop) / probable (TP1) / exceptionnel (TP3).
    Reprice l'option (Black-Scholes) au niveau cible, à un horizon de ~40j (théta inclus)."""
    iv = _f(c.get('iv')) / 100.0
    K = _f(c.get('strike'))
    dte = max(_i(c.get('dte')), 1)
    mid = _f(c.get('mid'))
    is_call = c.get('type') == 'CALL'
    T_fut = max((dte - horizon_days) / 365.0, 0.01)

    def pnl(px):
        if not px or mid <= 0:
            return None
        val = _bs_price(px, K, T_fut, iv, is_call)
        return round((val - mid) / mid * 100)
    pr = levels.get('tp1') or levels.get('tp2')
    ex = levels.get('tp3') or levels.get('tp2')
    return {'horizon': horizon_days,
            'pess': {'px': levels.get('stop'), 'pnl': pnl(levels.get('stop'))},
            'prob': {'px': pr, 'pnl': pnl(pr)},
            'exalt': {'px': ex, 'pnl': pnl(ex)}}


def _pick_expiries(exps, now, buckets=None):
    """Échéances ciblées PAR BUCKET (court/moyen/long). buckets=None -> ('long',) rétro-compat.
    Renvoie [(exp_str, dte, bucket_key)]."""
    buckets = buckets or config.DEFAULT_BUCKETS
    picks, seen = [], set()
    for bk in buckets:
        b = config.OPTION_BUCKETS[bk]
        best, bd = None, 1e9
        for e in exps:
            try:
                d = (datetime.strptime(e, '%Y-%m-%d') - now).days
            except Exception:
                continue
            if b['min'] <= d <= b['max'] and abs(d - b['target']) < bd:
                bd, best = abs(d - b['target']), e
        if best and best not in seen:
            seen.add(best)
            picks.append((best, (datetime.strptime(best, '%Y-%m-%d') - now).days, bk))
    return picks


def news_for(tk, n=5):
    """Dernières news d'un titre (yfinance) — gère l'ancien format plat et le nouveau imbriqué."""
    out = []
    try:
        for it in (tk.news or [])[:n]:
            if not isinstance(it, dict):
                continue
            c = it.get('content', it)
            title = c.get('title') or it.get('title')
            prov = c.get('provider')
            pub = prov.get('displayName') if isinstance(prov, dict) else (it.get('publisher') or '')
            t = c.get('pubDate') or it.get('providerPublishTime') or ''
            cu = c.get('canonicalUrl')
            link = cu.get('url') if isinstance(cu, dict) else (it.get('link') or '')
            if title:
                out.append({'title': title, 'pub': pub or '', 'time': str(t)[:16].replace('T', ' '), 'link': link or ''})
    except Exception:
        pass
    return out


def best_for_symbol(sym, spot, target, direction, iv_rank=50, max_n=2, buckets=None, earnings_dte=None):
    """Meilleurs contrats par bucket (court/moyen/long), classés. buckets=None -> long (rétro-compat)."""
    buckets = buckets or config.DEFAULT_BUCKETS
    out = []
    try:
        tk = yf.Ticker(sym)
        now = datetime.now()
        for exp, dte, bk in _pick_expiries(list(tk.options), now, buckets):
            T = max(dte / 365.0, 0.02)
            ch = tk.option_chain(exp)
            df = ch.calls if direction == 'call' else ch.puts
            for _, row in df.iterrows():
                K = _f(row['strike'])
                if direction == 'call':
                    lo, hi = (0.99, 1.10) if bk == 'court' else (0.98, 1.18)
                else:
                    lo, hi = (0.90, 1.01) if bk == 'court' else (0.82, 1.02)
                if not (spot * lo <= K <= spot * hi):
                    continue
                iv = _f(row.get('impliedVolatility'))
                oi = _i(row.get('openInterest'))
                vol = _i(row.get('volume'))
                bid = _f(row.get('bid'))
                ask = _f(row.get('ask'))
                quoted = bid > 0 and ask > 0
                mid = (bid + ask) / 2.0 if quoted else _f(row.get('lastPrice'))
                if mid <= 0:
                    continue
                # IV : si yfinance ne donne rien d'exploitable, on la RECALCULE depuis le prix
                stale = False
                if not (0.03 < iv < 3.0):
                    solved = _iv_from_price(spot, K, T, mid, direction == 'call')
                    if solved is None or solved < 0.03:
                        continue
                    iv, stale = solved, True
                if not quoted or oi <= 0:
                    stale = True               # hors séance / illiquide → indicatif
                spread = (ask - bid) / mid * 100.0 if (quoted and ask > bid) else (None if stale else 99.0)
                sc_spread = spread if spread is not None else 12.0
                delta, gamma, theta, vega = _greeks(spot, K, T, iv, direction == 'call')
                theta_burn = abs(theta) / mid * 100.0 if mid else 0.0   # érosion %/jour
                be = K + mid if direction == 'call' else K - mid
                intr = (max(target - K, 0) if direction == 'call' else max(K - target, 0))
                pot = (intr - mid) / mid * 100.0 if mid else 0.0
                suit = scoring.options_score({'oi': oi, 'spread_pct': sc_spread, 'delta': delta,
                                              'dte': dte, 'iv_rank': iv_rank, 'tech_ok': True,
                                              'bucket': bk, 'theta_burn': theta_burn, 'earnings_dte': earnings_dte})
                flags = []
                if bk == 'court':
                    flags.append('decay rapide')
                if earnings_dte is not None and 0 <= earnings_dte <= dte:
                    flags.append('earnings')
                if iv_rank > 70:
                    flags.append('IV chere')
                if spread is not None and spread > 8:
                    flags.append('spread large')
                if stale:
                    flags.append('hors séance')
                # PROBABILITÉS (lognormal, IV de l'option) + score de DANGER
                if direction == 'call':
                    pop = _prob_above(spot, be, T, iv) * 100        # proba profit à l'échéance
                    p_itm = _prob_above(spot, K, T, iv) * 100        # proba finir dans la monnaie
                    p_tgt = _prob_above(spot, target, T, iv) * 100   # proba d'atteindre la cible
                else:
                    pop = (1 - _prob_above(spot, be, T, iv)) * 100
                    p_itm = (1 - _prob_above(spot, K, T, iv)) * 100
                    p_tgt = (1 - _prob_above(spot, target, T, iv)) * 100
                dg = ((2 if theta_burn > 1.5 else 1 if theta_burn > 0.8 else 0)
                      + (2 if pop < 35 else 1 if pop < 50 else 0)
                      + (1 if 'earnings' in flags else 0)
                      + (1 if iv * 100 > 60 else 0)
                      + (1 if bk == 'court' else 0))
                danger = 'Extrême' if dg >= 6 else 'Élevé' if dg >= 4 else 'Modéré' if dg >= 2 else 'Faible'
                _c = {
                    'sym': sym, 'type': direction.upper(), 'bucket': bk, 'exp': exp, 'dte': dte, 'strike': K,
                    'tgt': round(target, 2),
                    'pop': round(pop), 'p_itm': round(p_itm), 'p_tgt': round(p_tgt),
                    'danger': danger, 'danger_n': dg,
                    'bid': round(bid, 2), 'ask': round(ask, 2),
                    'mid': round(mid, 2), 'cost': round(mid * 100), 'be': round(be, 2),
                    'iv': round(iv * 100, 1), 'delta': round(delta, 2), 'gamma': round(gamma, 4),
                    'theta': round(theta, 3), 'theta_burn': round(theta_burn, 2), 'vega': round(vega, 3),
                    'oi': oi, 'vol': vol, 'spread': round(spread, 1) if spread is not None else None,
                    'pot': round(pot), 'suit': round(suit), 'grade': config.grade(suit),
                    'em_pct': round(iv * math.sqrt(T) * 100, 1),
                    'flags': flags, 'stale': stale,
                }
                _q = option_quality(_c, spot)
                _c['quality'] = _q['score']
                _c['quality_parts'] = _q['parts']
                out.append(_c)
    except Exception:
        return []
    # Niveau 1 (séance) : contrats liquides, données réelles. Préféré.
    live = [c for c in out if not c['stale'] and c['spread'] is not None
            and c['spread'] <= config.PROFILE['max_bidask_spread_pct']
            and c['oi'] >= 100 and c['suit'] >= 45]
    # Niveau 2 (hors séance) : si rien de liquide, on montre l'analyse INDICATIVE (IV recalculée).
    good = live if live else [c for c in out if c['suit'] >= 40]
    order = {'court': 0, 'moyen': 1, 'long': 2}
    good.sort(key=lambda c: (order.get(c['bucket'], 9), -c['suit']))
    # max_n PAR BUCKET (sinon le long écrase le court)
    res, cnt = [], {}
    for c in good:
        if cnt.get(c['bucket'], 0) < max_n:
            res.append(c)
            cnt[c['bucket']] = cnt.get(c['bucket'], 0) + 1
    return res


def build_board(detail, rows, max_calls=6, max_puts=3):
    """Board options du jour : calls (court/moyen/long) sur top haussiers, puts sur baissiers."""
    board = []
    buys = [r for r in rows if r['verdict'] in ('BUY', 'WATCH')][:max_calls]
    sells = [r for r in rows if r['verdict'] == 'AVOID'][-max_puts:]
    for r in buys:
        d = detail.get(r['symbol'])
        if d:
            board += best_for_symbol(r['symbol'], d['price'], d['plan']['tp2'], 'call',
                                     max_n=1, buckets=('court', 'moyen', 'long'))
    for r in sells:
        d = detail.get(r['symbol'])
        if d:
            board += best_for_symbol(r['symbol'], d['price'], d['price'] - 2 * d['plan']['atr'], 'put',
                                     max_n=1, buckets=('moyen', 'long'))
    order = {'court': 0, 'moyen': 1, 'long': 2}
    board.sort(key=lambda c: (order.get(c['bucket'], 9), -c['suit']))
    return board


def recommend(contracts):
    """Choisit LA meilleure option entre court / moyen / long (profil swing : moyen/long
    privilégiés, court seulement si excellent). Renvoie le contrat + une justification FR."""
    if not contracts:
        return None

    def sc(c):
        s = c['suit']
        s += {'moyen': 6, 'long': 4, 'court': -2}.get(c.get('bucket'), 0)   # sweet-spot swing
        s += (c.get('pop', 50) - 50) * 0.4                                  # favorise une bonne proba de profit
        s -= c.get('danger_n', 0) * 4                                       # pénalise fort le danger
        if 'earnings' in (c.get('flags') or []):
            s -= 5
        if c.get('theta_burn', 0) > 1.5:
            s -= 4
        return s

    best = max(contracts, key=sc)
    lab = {'court': 'échéance COURTE (~1-2 mois) — tactique, théta violent',
           'moyen': 'échéance MOYENNE (~3 mois) — le compromis idéal du swing',
           'long': 'échéance LONGUE (6-12M) — robuste, théta lent'}
    why = [lab.get(best.get('bucket'), '')]
    why.append(f"proba de profit {best.get('pop')}% · danger {best.get('danger')}")
    if 'earnings' in (best.get('flags') or []):
        why.append('couvre des résultats (risque IV-crush)')
    why.append(f"delta {best['delta']} · suitability {best['suit']}/100 · coût ${best['cost']:,}".replace(',', ' '))
    return {**best, 'why': ' · '.join(w for w in why if w)}
