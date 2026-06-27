"""elio/strategy.py — STRATÉGIE OPTIONS PERSONNALISÉE (échelle 1/2/3/6/9/12 mois).

Profil utilisateur :
  • style    : mix croissance + équilibre (offensif mais maîtrisé)
  • instrument: ACHAT de Calls/Puts simples (directionnel)
  • tailles  : 5 k = paris ultra-risque · 10-20 k = risque contrôlé

Synthétise le contrat IDÉAL par échéance en Black-Scholes depuis le cours + la
volatilité (ATR annualisé). → fonctionne PARTOUT, même sans chaîne d'options en
réseau (donc visible en mode démo, sur iPhone).
⛔ ANALYSE ÉDUCATIVE — jamais un conseil financier, jamais un ordre.
"""
import math

from .options import _bs_price, _greeks

# ── Échelle d'échéances personnalisée + delta cible ───────────────────────────
# Court = plus directionnel (delta plus haut, on veut du mouvement vite) ;
# long = on laisse du temps (delta un peu plus bas, moins cher en theta/jour).
HORIZONS = [
    {'key': 'm1',  'label': '1 mois',  'months': 1,  'dte': 30,  'delta': 0.65},
    {'key': 'm2',  'label': '2 mois',  'months': 2,  'dte': 60,  'delta': 0.62},
    {'key': 'm3',  'label': '3 mois',  'months': 3,  'dte': 90,  'delta': 0.60},
    {'key': 'm6',  'label': '6 mois',  'months': 6,  'dte': 180, 'delta': 0.58},
    {'key': 'm9',  'label': '9 mois',  'months': 9,  'dte': 270, 'delta': 0.55},
    {'key': 'm12', 'label': '12 mois', 'months': 12, 'dte': 365, 'delta': 0.52},
]

# ── Dimensionnement (profil utilisateur) ──────────────────────────────────────
SIZING = [
    {'key': 'ultra',    'label': 'Ultra-risque',     'budget': 5000,  'color': '#EF4444'},
    {'key': 'controle', 'label': 'Risque contrôlé',  'budget': 15000, 'color': '#FFB23F'},
]

_BEAR = {'AVOID', 'ÉVITER', 'EVITER', 'SELL', 'VENDRE', 'REDUCE', 'ALLÉGER', 'ALLEGER'}


def _iv_proxy(atr_pct):
    """ATR% quotidien → volatilité annualisée approximative, bornée pour rester réaliste."""
    v = (atr_pct or 2.0) / 100.0 * math.sqrt(252)
    return max(0.22, min(1.10, v))


def _round_strike(S, k):
    step = 1 if S < 50 else 2.5 if S < 100 else 5 if S < 250 else 10
    return round(k / step) * step


def _strike_for_delta(S, T, sig, is_call, target):
    """Cherche le strike dont le |delta| approche la cible (balayage fin autour du cours)."""
    best, bd = S, 9.9
    k = S * 0.55
    while k <= S * 1.45:
        delta, *_ = _greeks(S, k, T, sig, is_call)
        diff = abs(abs(delta) - target)
        if diff < bd:
            bd, best = diff, k
        k += S * 0.01
    return best


def _leg(S, sig, plan, is_call, h):
    T = h['dte'] / 365.0
    k = _round_strike(S, _strike_for_delta(S, T, sig, is_call, h['delta']))
    prem = _bs_price(S, k, T, sig, is_call)
    if prem <= 0:
        return None
    delta, gamma, theta, vega = _greeks(S, k, T, sig, is_call)
    be = (k + prem) if is_call else (k - prem)
    # cible de prix pour le scénario gain = MOUVEMENT ATTENDU ±1σ sur l'horizon
    # (≈ S·IV·√T). Grandit avec le temps → cohérent : plus d'échéance = plus de
    # mouvement potentiel. On ne descend jamais sous le TP2 technique pour les courts.
    em = S * sig * math.sqrt(T)
    tp = (S + em) if is_call else max(S - em, 0.01)
    if is_call and plan.get('tp2'):
        tp = max(tp, plan['tp2'])
    intrinsic = max((tp - k), 0.0) if is_call else max((k - tp), 0.0)
    gain_pct = round((intrinsic - prem) / prem * 100) if prem else 0
    sizes = []
    for s in SIZING:
        contracts = int(s['budget'] // (prem * 100)) if prem > 0 else 0
        cost = round(contracts * prem * 100)
        gain = round(contracts * 100 * (intrinsic - prem))
        sizes.append({'key': s['key'], 'label': s['label'], 'color': s['color'],
                      'budget': s['budget'], 'contracts': contracts,
                      'cost': cost, 'maxloss': cost, 'gain_if_target': gain})
    return {'key': h['key'], 'label': h['label'], 'dte': h['dte'],
            'strike': k, 'premium': round(prem, 2), 'breakeven': round(be, 2),
            'delta': round(abs(delta), 2), 'theta_day': round(theta, 3),
            'target': round(tp, 2), 'gain_if_target': gain_pct, 'sizes': sizes}


def build(rows, detail, top_n=6):
    """Construit la stratégie pour les meilleures convictions du scan."""
    picks = []
    for r in (rows or [])[:top_n]:
        sym = r.get('symbol')
        d = detail.get(sym) if detail else None
        if not d:
            continue
        S = d.get('price')
        if not S or S <= 0:
            continue
        verdict = (d.get('verdict') or '').upper()
        is_call = verdict not in _BEAR                      # haussier par défaut
        sig = _iv_proxy(d.get('atr_pct'))
        plan = d.get('plan') or {}
        legs = [leg for leg in (_leg(S, sig, plan, is_call, h) for h in HORIZONS) if leg]
        if legs:
            picks.append({'symbol': sym, 'price': round(S, 2), 'grade': d.get('grade'),
                          'score': d.get('score'), 'direction': 'CALL' if is_call else 'PUT',
                          'iv': round(sig * 100), 'verdict': verdict, 'legs': legs})
    return {'picks': picks, 'horizons': HORIZONS, 'sizing': SIZING,
            'profile': {'style': 'Croissance + équilibre',
                        'instrument': 'Achat Calls / Puts directionnels',
                        'sizing': '5 k ultra-risque · 10-20 k risque contrôlé'}}
