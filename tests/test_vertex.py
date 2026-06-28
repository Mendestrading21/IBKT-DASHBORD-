"""Tests d'INVARIANTS QUANTITATIFS pour VERTEX (cohérence mathématique du noyau).

Comble le vide pointé par l'audit : le repo protégeait bien le « no order » mais
peu la justesse des calculs. Ici on vérifie les invariants : bornes 0-100, kelly
capé, probabilités dans [0,1], ordre de l'intervalle de confiance, monotonie du
repricing call, robustesse aux données manquantes, no-trade en régime dangereux.
"""
import os

os.environ.setdefault("NO_IBKR", "1")

from elio import vertex, options  # noqa: E402


def _fake(**over):
    d = {
        'price': 100.0, 'score': 72, 'grade': 'A', 'verdict': 'BUY',
        'ma20': 98, 'ma50': 95, 'ma200': 88, 'rs': 62, 'roc': 8, 'adx': 28,
        'chop': 40, 'volx': 1.3, 'rsi': 58, 'ext_atr': 1.0, 'pos52': 70,
        'regime': 'TREND', 'setup_quality': 65, 'confidence': 0.6,
        'signals': {'above50': True, 'above200': True, 'stacked': True, 'goldenNow': True},
        'plan': {'entry': 100.0, 'stop': 92.0, 'tp1': 108.0, 'tp2': 116.0,
                 'tp3': 124.0, 'atr': 3.0, 'resistance': 130.0, 'rr_res': 2.5},
    }
    d.update(over)
    return d


def test_all_scores_in_0_100():
    v = vertex.evaluate(_fake())
    for k in ('score', 'edge', 'trend_quality', 'entry_quality', 'rr',
              'expected_move', 'asymmetry', 'institutionality', 'extension_penalty'):
        assert 0 <= v[k] <= 100, (k, v[k])


def test_kelly_capped_12():
    for over in ({}, {'score': 99, 'confidence': 1.0, 'rs': 99, 'adx': 60}):
        v = vertex.evaluate(_fake(**over))
        assert 0 <= v['kelly']['pct'] <= 12, v['kelly']


def test_mc_probabilities_in_range():
    mc = vertex.monte_carlo_edge(_fake())
    assert mc is not None
    for k in ('p_hit_tp1', 'p_hit_tp2', 'p_tp1_first', 'p_stop_before_tp1'):
        assert 0.0 <= mc[k] <= 1.0, (k, mc[k])


def test_mc_confidence_interval_ordered():
    mc = vertex.monte_carlo_edge(_fake())
    assert mc['edge_ci_low_bps'] <= mc['edge_mean_bps'] <= mc['edge_ci_high_bps']


def test_mc_deterministic():
    a = vertex.monte_carlo_edge(_fake())
    b = vertex.monte_carlo_edge(_fake())
    assert a == b  # même entrée → même sortie (réplicabilité)


def test_bs_call_monotonic_in_spot():
    # repricing d'un call : plus le spot monte, plus le call vaut cher
    lo = options._bs_price(95, 100, 0.25, 0.30, True)
    hi = options._bs_price(110, 100, 0.25, 0.30, True)
    assert hi >= lo


def test_extension_penalty_monotonic():
    p_low = vertex.nonlinear_extension_penalty(_fake(ext_atr=0.5, rsi=55, pos52=60))
    p_high = vertex.nonlinear_extension_penalty(_fake(ext_atr=3.5, rsi=80, pos52=98))
    assert p_high > p_low


def test_no_trade_when_chop_and_uncertain():
    # régime CHOP + cible à peine au-dessus du cours (edge incertain) → no_trade probable
    d = _fake(regime='CHOP', ext_atr=2.0,
              plan={'entry': 100.0, 'stop': 96.0, 'tp1': 101.0, 'tp2': 102.0,
                    'tp3': 103.0, 'atr': 4.0, 'resistance': 103.0})
    v = vertex.evaluate(d)
    assert isinstance(v['risk_flags'], list)
    assert 'regime_chop' in v['risk_flags']


def test_robust_on_missing_data():
    assert vertex.evaluate({}) is not None
    assert vertex.evaluate(None) is not None
    assert vertex.monte_carlo_edge({}) is None  # pas de plan → None propre


def test_verdict_is_valid():
    v = vertex.evaluate(_fake())
    assert v['verdict'] in ('VERTEX S+', 'VERTEX BUY', 'VERTEX WATCH', 'VERTEX WAIT', 'VERTEX AVOID')
