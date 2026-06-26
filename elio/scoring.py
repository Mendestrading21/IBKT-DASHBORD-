"""
elio/scoring.py — Fonctions de scoring PURES (cahier §18).

Chaque fonction prend un dict d'indicateurs déjà calculés et renvoie un score
0-100. `compose()` combine selon les poids du profil et renvoie le score global
+ la note (grade) + le détail par module. Pures = testables, zéro effet de bord.

Clés attendues dans `ind` :
  above20/above50/above200/stacked/golden (bool), rsi, roc, rs, pos52, volx,
  atr_pct, ext_atr (extension en ATR au-dessus de l'EMA20).
"""
import numpy as np

from . import config


def _clip(x, a=0.0, b=100.0):
    return float(max(a, min(b, x)))


def technical_score(ind):
    s = 0.0
    s += 22 if ind.get('above200') else 0
    s += 16 if ind.get('above50') else 0
    s += 10 if ind.get('above20') else 0
    s += 18 if ind.get('stacked') else 0
    r = ind.get('rsi', 50)
    s += 12 if 45 <= r <= 70 else (4 if r > 70 else 0)
    vx = ind.get('volx', 1.0)
    s += 12 if vx >= 1.2 else (6 if vx >= 0.9 else 0)
    s += 10 if ind.get('golden') else 0
    return _clip(s)


def momentum_score(ind):
    s = 50 + (ind.get('rsi', 50) - 50) * 0.5 + np.clip(ind.get('roc', 0), -25, 25) + (ind.get('rs', 50) - 50) * 0.4
    return _clip(s)


def fundamental_score(ind):
    # FALLBACK ASSUMÉ : aucune source de fondamentaux branchée (cahier §21 : noter ce qui manque).
    # Proxy « qualité » = force relative durable + structure de tendance propre.
    s = 45 + (ind.get('rs', 50) - 50) * 0.6 + (15 if ind.get('stacked') else 0)
    return _clip(s)


def risk_score(ind):
    # Plus c'est SÛR, plus le score est haut. Pénalise vol haute, sur-extension, sommet de range.
    s = 72.0
    s -= min(30, ind.get('atr_pct', 2.0) * 4.0)
    if ind.get('pos52', 50) >= 95:
        s -= 14
    if ind.get('rsi', 50) >= 78:
        s -= 10
    if ind.get('ext_atr', 0) >= 4:
        s -= 12
    return _clip(s)


def options_score(opt):
    """Option Suitability /100 (cahier §6), adapté au bucket d'échéance. `opt` dict ou None."""
    if not opt:
        return None
    p = config.PROFILE
    bucket = opt.get('bucket', 'long')
    b = config.OPTION_BUCKETS.get(bucket, config.OPTION_BUCKETS['long'])
    s = 0.0
    oi = opt.get('oi', 0)
    s += 25 if oi >= p['min_option_open_interest'] else (10 if oi >= 100 else 0)
    sp = opt.get('spread_pct', 99)
    s += 15 if sp <= 8 else (7 if sp <= p['max_bidask_spread_pct'] else 0)
    d = abs(opt.get('delta', 0))
    # delta cible GLISSANT par bucket (le court doit être plus directionnel)
    s += 15 if b['delta_lo'] <= d <= b['delta_hi'] else (8 if (b['delta_lo'] - 0.10) <= d <= (b['delta_hi'] + 0.10) else 2)
    dte = opt.get('dte', 0)
    # barème DTE PAR BUCKET : le court n'est plus puni d'être court
    if bucket == 'long':
        s += 15 if dte >= 120 else (8 if dte >= 60 else 0)
        s += 5 if dte >= 90 else 0
    elif bucket == 'moyen':
        s += 15 if 75 <= dte <= 135 else (8 if 60 <= dte <= 150 else 3)
    else:  # court
        s += 15 if 30 <= dte <= 60 else (8 if 25 <= dte <= 75 else 2)
    ivr = opt.get('iv_rank', 50)
    s += 10 if ivr <= 60 else (4 if ivr <= 80 else 0)
    if bucket == 'court' and ivr > 70:
        s -= 6                                   # court + IV chère = double peine au crush
    s += 15 if opt.get('tech_ok') else 5
    tb = opt.get('theta_burn', 0.0)              # |theta|/mid en %/jour
    if tb > 1.5:
        s -= 8
    elif tb > 1.0:
        s -= 4
    ed = opt.get('earnings_dte')                 # jours avant résultats, ou None
    if ed is not None and 0 <= ed <= dte:
        s -= 10                                  # l'échéance couvre un earnings (IV-crush)
    return _clip(s)


def compose(ind, opt=None):
    """Combine les sous-scores → {global, grade, technical, momentum, fundamental, risk, options?}."""
    parts = {
        'technical': technical_score(ind),
        'momentum': momentum_score(ind),
        'fundamental': fundamental_score(ind),
        'risk': risk_score(ind),
    }
    osc = options_score(opt)
    if osc is not None:
        parts['options'] = osc
    w = {k: config.WEIGHTS[k] for k in parts}
    g = sum(w[k] * parts[k] for k in parts) / sum(w.values())
    out = {'global': round(g), 'grade': config.grade(g)}
    out.update({k: round(v) for k, v in parts.items()})
    # CONFIANCE : sous-scores alignés = haute confiance ; contradictoires = basse.
    core = [parts['technical'], parts['momentum'], parts['fundamental'], parts['risk']]
    out['confidence'] = round(_clip(100 - min(float(np.std(core)) * 2.5, 60)))
    return out
