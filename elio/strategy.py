"""elio/strategy.py — STRATÉGIE OPTIONS PERSONNALISÉE (échelle 1/2/3/6/9/12 mois).

Profil utilisateur (encodé d'après ses réponses) :
  • style       : croissance + équilibre, offensif maîtrisé
  • instrument  : ACHAT de Calls/Puts simples (directionnel)
  • delta       : MIX par échéance — agressif/OTM sur le court (pari rapide),
                  conservateur/ITM sur le long (LEAPS solides)
  • tailles     : 5 k (ultra-risque) · 15 k (risque contrôlé) · risque 5-10 %/trade
  • SPÉCULATION : ne garde PAS jusqu'à l'échéance → on valorise l'option EN COURS
                  DE ROUTE (mark-to-market Black-Scholes, temps restant), pas à
                  l'expiration. C'est le cœur de cette stratégie.
  • scénarios   : pessimiste / probable / exceptionnel + cible technique + ±1σ
  • direction   : Call ET Put, pondérés par le RÉGIME de marché
  • sorties     : objectif +50/+100 % · stop −50 % · cours cible · alerte théta

Tout est calculé en Black-Scholes depuis le cours + la volatilité → fonctionne
PARTOUT, même sans chaîne d'options réseau (donc visible en mode démo, iPhone).
⛔ ANALYSE ÉDUCATIVE — jamais un conseil financier, jamais un ordre.
"""
import math

from .options import _bs_price, _greeks

# ── Échelle d'échéances + delta cible MIX (court agressif → long conservateur) ─
HORIZONS = [
    {'key': 'm1',  'label': '1 mois',  'months': 1,  'dte': 30,  'delta': 0.45},
    {'key': 'm2',  'label': '2 mois',  'months': 2,  'dte': 60,  'delta': 0.48},
    {'key': 'm3',  'label': '3 mois',  'months': 3,  'dte': 90,  'delta': 0.52},
    {'key': 'm6',  'label': '6 mois',  'months': 6,  'dte': 180, 'delta': 0.58},
    {'key': 'm9',  'label': '9 mois',  'months': 9,  'dte': 270, 'delta': 0.65},
    {'key': 'm12', 'label': '12 mois', 'months': 12, 'dte': 365, 'delta': 0.70},
]

SIZING = [
    {'key': 'ultra',    'label': '5 k ultra',     'budget': 5000,  'color': '#EF4444'},
    {'key': 'controle', 'label': '15 k contrôlé', 'budget': 15000, 'color': '#FFB23F'},
]
RISK_PCT = (5, 10)          # risque cible par trade (info)
IV_MAX = 0.85              # filtre « IV pas trop chère » (vol annualisée)

_BEAR = {'AVOID', 'ÉVITER', 'EVITER', 'SELL', 'VENDRE', 'REDUCE', 'ALLÉGER', 'ALLEGER'}


def _iv_proxy(atr_pct):
    v = (atr_pct or 2.0) / 100.0 * math.sqrt(252)
    return max(0.22, min(1.10, v))


def _round_strike(S, k):
    step = 1 if S < 50 else 2.5 if S < 100 else 5 if S < 250 else 10
    return round(k / step) * step


def _strike_for_delta(S, T, sig, is_call, target):
    best, bd = S, 9.9
    k = S * 0.55
    while k <= S * 1.45:
        delta, *_ = _greeks(S, k, T, sig, is_call)
        diff = abs(abs(delta) - target)
        if diff < bd:
            bd, best = diff, k
        k += S * 0.01
    return best


def _hold_days(dte):
    """Durée de détention type (spéculation) : ~1/3 de l'échéance, bornée 5-45 j."""
    return int(max(5, min(round(dte * 0.34), 45)))


def _val(S_t, K, dte_rest_days, sig, is_call):
    """Valeur mark-to-market de l'option à un cours S_t, avec le temps RESTANT."""
    T = max(dte_rest_days / 365.0, 1.0 / 365.0)
    return _bs_price(S_t, K, T, sig, is_call)


def _bias(market):
    """Régime de marché → 'favorable' / 'neutral' / 'dangerous' (défensif)."""
    if isinstance(market, dict):
        blob = ' '.join(str(market.get(k, '')) for k in
                        ('regime', 'state', 'label', 'spy_regime', 'verdict', 'tone')).lower()
        if any(w in blob for w in ('favor', 'risk-on', 'risk on', 'haussier', 'bull', 'vert')):
            return 'favorable'
        if any(w in blob for w in ('danger', 'risk-off', 'risk off', 'baissier', 'bear', 'rouge', 'stress')):
            return 'dangerous'
        sc = market.get('score') or market.get('market_score')
        if isinstance(sc, (int, float)):
            return 'favorable' if sc >= 60 else 'dangerous' if sc < 40 else 'neutral'
    return 'neutral'


def _leg(S, sig, plan, is_call, h):
    T = h['dte'] / 365.0
    k = _round_strike(S, _strike_for_delta(S, T, sig, is_call, h['delta']))
    prem = _bs_price(S, k, T, sig, is_call)
    if prem <= 0:
        return None
    delta, gamma, theta, vega = _greeks(S, k, T, sig, is_call)
    be = (k + prem) if is_call else (k - prem)
    hold = _hold_days(h['dte'])
    rest = h['dte'] - hold                                 # temps restant à la revente
    em = S * sig * math.sqrt(hold / 365.0)                 # mouvement attendu sur la détention
    sgn = 1.0 if is_call else -1.0

    def scen(mult):
        st = max(S + sgn * mult * em, 0.01)
        val = _val(st, k, rest, sig, is_call)
        pct = round((val - prem) / prem * 100) if prem else 0
        return {'px': round(st, 2), 'val': round(val, 2), 'pct': pct}

    scenarios = {'pess': scen(-0.5), 'prob': scen(1.0), 'except': scen(2.0)}

    # cible TECHNIQUE (plan) valorisée en cours de route
    tp_px = (plan.get('tp2') if is_call else plan.get('stop'))
    tp_tech = None
    if tp_px:
        val = _val(tp_px, k, rest, sig, is_call)
        tp_tech = {'px': round(tp_px, 2), 'pct': round((val - prem) / prem * 100) if prem else 0}

    # gain à l'ÉCHÉANCE (intrinsèque) sur un mouvement +1σ plein horizon (repère)
    emT = S * sig * math.sqrt(T)
    tgtT = max(S + sgn * emT, 0.01)
    intrinsicT = max((tgtT - k) if is_call else (k - tgtT), 0.0)
    gain_exp = round((intrinsicT - prem) / prem * 100) if prem else 0

    sizes = []
    for s in SIZING:
        contracts = int(s['budget'] // (prem * 100)) if prem > 0 else 0
        cost = round(contracts * prem * 100)
        sizes.append({'key': s['key'], 'label': s['label'], 'color': s['color'],
                      'budget': s['budget'], 'contracts': contracts, 'cost': cost,
                      'maxloss': cost,
                      'gain_prob': round(contracts * 100 * (scenarios['prob']['val'] - prem))})

    exit_rules = {
        'tp50': round(prem * 1.5, 2), 'tp100': round(prem * 2.0, 2),
        'stop50': round(prem * 0.5, 2),
        'theta_alert_dte': max(0, h['dte'] - 45),          # zone théta chaude
    }
    return {'key': h['key'], 'label': h['label'], 'dte': h['dte'], 'hold': hold,
            'strike': k, 'premium': round(prem, 2), 'breakeven': round(be, 2),
            'delta': round(abs(delta), 2), 'gamma': round(gamma, 4),
            'theta_day': round(theta, 3), 'vega': round(vega, 3),
            'scenarios': scenarios, 'tp_tech': tp_tech, 'gain_exp': gain_exp,
            'exit': exit_rules, 'sizes': sizes}


def _legs(S, sig, plan, is_call):
    return [leg for leg in (_leg(S, sig, plan, is_call, h) for h in HORIZONS) if leg]


# ── CONSTRUCTEUR DE PORTEFEUILLE (grosses sommes 50k/100k/200k) ───────────────
# Profil utilisateur : 8-12 positions · mix étalé · ~87 % déployé (10-15 % cash)
# CŒUR contrôlé (3-6-9 mois, delta haut, façon « action ») + SATELLITES offensifs
# (1-2 mois OTM, levier) · risque ≤ 10 %/position.
_CORE_HORIZONS = ['m3', 'm6', 'm9']
_SAT_HORIZONS = ['m1', 'm2', 'm3']
_DEPLOY = 0.87
_CORE_SHARE = 0.66
_MAX_POS_RISK = 0.10


def _candidates(rows, detail, bias, limit):
    out = []
    for r in (rows or []):
        sym = r.get('symbol')
        d = detail.get(sym) if detail else None
        if not d:
            continue
        S = d.get('price')
        if not S or S <= 0:
            continue
        sig = _iv_proxy(d.get('atr_pct'))
        bullish = (d.get('verdict') or '').upper() not in _BEAR
        primary = 'PUT' if bias == 'dangerous' else ('CALL' if bullish else 'PUT')
        legs = {l['key']: l for l in _legs(S, sig, (d.get('plan') or {}), primary == 'CALL')}
        if not legs:
            continue
        out.append({'sym': sym, 'S': round(S, 2), 'grade': d.get('grade'),
                    'score': d.get('score'), 'dir': primary, 'legs': legs})
        if len(out) >= limit:
            break
    return out


def build_portfolio(rows, detail, market=None, capital=100000, n_core=6, n_sat=4):
    """Alloue un capital (50k/100k/200k…) en portefeuille d'options selon le profil."""
    bias = _bias(market)
    cand = _candidates(rows, detail, bias, n_core + n_sat)
    deploy = capital * _DEPLOY
    core_budget, sat_budget = deploy * _CORE_SHARE, deploy * (1 - _CORE_SHARE)
    positions = []

    def add(group, budget, horizons, role):
        if not group:
            return
        per = budget / len(group)
        for idx, c in enumerate(group):
            leg = c['legs'].get(horizons[idx % len(horizons)]) or next(iter(c['legs'].values()), None)
            if not leg:
                continue
            unit = leg['premium'] * 100
            if unit <= 0:
                continue
            contracts = int(per // unit)
            if contracts < 1 and unit <= capital * 0.12:
                contracts = 1
            while contracts > 1 and contracts * unit > capital * _MAX_POS_RISK:
                contracts -= 1
            if contracts < 1:
                continue
            cost = round(contracts * unit)
            sc = leg['scenarios']
            positions.append({
                'sym': c['sym'], 'role': role, 'dir': c['dir'], 'grade': c['grade'],
                'horizon': leg['label'], 'dte': leg['dte'], 'strike': leg['strike'],
                'premium': leg['premium'], 'delta': leg['delta'], 'contracts': contracts,
                'cost': cost, 'maxloss': cost,
                'gain_prob': round(contracts * 100 * (sc['prob']['val'] - leg['premium'])),
                'gain_exc': round(contracts * 100 * (sc['except']['val'] - leg['premium'])),
                'prob_pct': sc['prob']['pct'], 'exc_pct': sc['except']['pct']})

    add(cand[:n_core], core_budget, _CORE_HORIZONS, 'CŒUR')
    add(cand[n_core:n_core + n_sat], sat_budget, _SAT_HORIZONS, 'SATELLITE')
    deployed = sum(p['cost'] for p in positions)
    gp = sum(p['gain_prob'] for p in positions)
    ge = sum(p['gain_exc'] for p in positions)
    return {'capital': capital, 'deployed': deployed, 'cash': round(capital - deployed),
            'positions': positions, 'n': len(positions), 'maxloss': deployed,
            'gain_prob': gp, 'gain_exc': ge,
            'gain_prob_pct': round(gp / capital * 100, 1) if capital else 0,
            'gain_exc_pct': round(ge / capital * 100, 1) if capital else 0,
            'regime': bias}


def build(rows, detail, market=None, top_n=6):
    """Stratégie pour les meilleures convictions. Call ET Put, pondérés régime."""
    bias = _bias(market)
    picks = []
    for r in (rows or [])[:top_n]:
        sym = r.get('symbol')
        d = detail.get(sym) if detail else None
        if not d:
            continue
        S = d.get('price')
        if not S or S <= 0:
            continue
        sig = _iv_proxy(d.get('atr_pct'))
        iv_expensive = sig > IV_MAX
        verdict = (d.get('verdict') or '').upper()
        bullish = verdict not in _BEAR
        # direction privilégiée = croisement conviction × régime
        if bias == 'dangerous':
            primary = 'PUT'
        elif bias == 'favorable':
            primary = 'CALL' if bullish else 'PUT'
        else:
            primary = 'CALL' if bullish else 'PUT'
        plan = d.get('plan') or {}
        picks.append({
            'symbol': sym, 'price': round(S, 2), 'grade': d.get('grade'),
            'score': d.get('score'), 'iv': round(sig * 100),
            'iv_expensive': iv_expensive, 'regime': bias, 'primary': primary,
            'call': _legs(S, sig, plan, True),
            'put': _legs(S, sig, plan, False),
        })
    return {'picks': picks, 'horizons': HORIZONS, 'sizing': SIZING,
            'regime': bias, 'risk_pct': list(RISK_PCT),
            'profile': {'style': 'Croissance + équilibre (spéculation, sortie avant échéance)',
                        'instrument': 'Achat Calls / Puts directionnels',
                        'delta': 'Delta MIX : agressif court · conservateur long',
                        'sizing': '5 k ultra-risque · 15 k contrôlé · 5-10 %/trade'}}
