"""
elio/config.py — Profil stratégique + barème de scoring (cahier §18/§19).

Source unique de vérité pour les seuils, poids et règles. AUCUNE promesse de
performance. Tout reste ANALYSE / PAPER ; un ordre réel exigera toujours une
confirmation manuelle.
"""

# ── Profil utilisateur (cahier §19) ──────────────────────────────────────
PROFILE = {
    'style': 'aggressive_but_disciplined',
    'horizon': '1_to_3_month_trade_on_6_to_12_month_options',
    'preferred_assets': ['US stocks', 'ETFs', 'Calls', 'Selective puts'],
    'max_position_normal': 0.08,      # 8 %
    'max_position_strong': 0.12,      # 12 %
    'max_options_exposure': 0.20,     # 20 % du portefeuille en options
    'min_option_open_interest': 500,
    'min_option_volume': 50,
    'max_bidask_spread_pct': 12,
    'min_score_buy': 75,
    'min_score_watch': 60,
}

# ── Poids du score global (cahier §18 globalScore) ───────────────────────
#   Quand les données options manquent (niveau scanner), on renormalise sans options.
WEIGHTS = {'technical': 30, 'momentum': 20, 'fundamental': 20, 'options': 15, 'risk': 15}

# ── Buckets d'échéance d'options (court / moyen / long) ──────────────────
#   Le profil = options 6-12M (long, défaut). L'utilisateur veut AUSSI explorer
#   le court (1-3 mois, tactique, théta violent). delta cible glissant par bucket.
OPTION_BUCKETS = {
    'court': {'label': 'Court', 'target': 45, 'min': 25, 'max': 75, 'delta_lo': 0.45, 'delta_hi': 0.70},
    'moyen': {'label': 'Moyen', 'target': 90, 'min': 75, 'max': 135, 'delta_lo': 0.40, 'delta_hi': 0.65},
    'long': {'label': 'Long', 'target': 210, 'min': 150, 'max': 400, 'delta_lo': 0.35, 'delta_hi': 0.65},
}
DEFAULT_BUCKETS = ('long',)   # défaut rétro-compatible (comportement historique)

# ── Couleurs (dark terminal — neo rouge/noir/vert/jaune, cahier §14) ─────
COLORS = {
    'bg': '#080A0E', 'card': '#111827', 'pos': '#34d399', 'neg': '#f87171',
    'warn': '#fbbf24', 'ai': '#a78bfa', 'accent': '#5BE3A8',
}


def grade(score):
    """Note S+ / S / A / B / C / D (cahier §4)."""
    return ('S+' if score >= 90 else 'S' if score >= 80 else 'A' if score >= 72
            else 'B' if score >= 60 else 'C' if score >= 45 else 'D')


def verdict(score, trend, regime=None):
    """BUY / WATCH / WAIT / AVOID selon le profil (cahier §3/§8).
    En régime CHOP (range agité), un BUY est rétrogradé en WATCH : les cassures échouent dans le bruit."""
    if score >= PROFILE['min_score_buy'] and trend >= 66:
        return 'WATCH' if regime == 'CHOP' else 'BUY'
    if score >= PROFILE['min_score_watch']:
        return 'WATCH'
    if trend >= 50:
        return 'WAIT'
    return 'AVOID'
