"""elio/validator.py — VERTEX : VALIDATEUR DE VÉRITÉ hors échantillon.

Priorité #1 du rapport quant : ne pas confondre un beau backtest avec une
stratégie réplicable. On audite la courbe d'equity avec :
  • walk-forward glissant (Sharpe par fenêtre, consistance)
  • PSR  — Probabilistic Sharpe Ratio (proba que le vrai Sharpe > 0)
  • DSR  — Deflated Sharpe Ratio (Sharpe corrigé du nombre d'essais + skew/kurt)
  • estimation PBO — dégradation in-sample → out-of-sample (risque de sur-optim.)

Réf. : Bailey & López de Prado (PSR/DSR/PBO). Pur math/numpy, déterministe,
aucun appel réseau, aucun ordre. ⛔ ANALYSE ÉDUCATIVE — indicatif.
"""
import math

import numpy as np

_GAMMA = 0.5772156649015329          # constante d'Euler-Mascheroni
_E = math.e


def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _nppf(p):
    """Inverse de la CDF normale (approximation d'Acklam). p dans (0,1)."""
    if p <= 0:
        return -1e9
    if p >= 1:
        return 1e9
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _stats(r):
    """Sharpe par période, skew, kurtosis (normale=3), n."""
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 3 or r.std() == 0:
        return 0.0, 0.0, 3.0, n
    mu, sd = r.mean(), r.std(ddof=1)
    z = (r - mu) / sd
    skew = float(np.mean(z ** 3))
    kurt = float(np.mean(z ** 4))
    return float(mu / sd), skew, kurt, n


def _psr(sr, skew, kurt, n, sr_bench=0.0):
    """Probabilistic Sharpe Ratio : P(SR_vrai > sr_bench)."""
    if n < 3:
        return 0.5
    denom = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4.0 * sr * sr, 1e-9))
    return _ncdf((sr - sr_bench) * math.sqrt(n - 1) / denom)


def _equity_to_returns(equity):
    eq = np.asarray(equity, dtype=float)
    eq = eq[eq > 0]
    if len(eq) < 4:
        return np.array([])
    return eq[1:] / eq[:-1] - 1.0


def build(equity, n_trials=10, folds=6, ann=252):
    """Audit complet d'une courbe d'equity. Renvoie scores + verdict de crédibilité."""
    try:
        r = _equity_to_returns(equity)
        if len(r) < 12:
            return {'ok': False, 'note': 'historique trop court pour valider'}
        sr_p, skew, kurt, n = _stats(r)
        sharpe_ann = round(sr_p * math.sqrt(ann), 2)
        psr0 = round(_psr(sr_p, skew, kurt, n, 0.0), 3)

        # walk-forward : Sharpe annualisé par fenêtre
        chunks = np.array_split(r, folds)
        fold_sr = []
        for ch in chunks:
            s, _, _, nn = _stats(ch)
            if nn >= 3:
                fold_sr.append(round(s * math.sqrt(ann), 2))
        pos = sum(1 for x in fold_sr if x > 0)
        folds_pos_pct = round(pos / len(fold_sr) * 100) if fold_sr else 0

        # Deflated Sharpe : benchmark = Sharpe max attendu sous H0 pour N essais.
        # Var du Sharpe estimé (par période), formule analytique (Lo / López de Prado) :
        se_sr2 = max((1 - skew * sr_p + (kurt - 1) / 4.0 * sr_p * sr_p) / max(n - 1, 1), 1e-9)
        N = max(int(n_trials), 1)
        if N > 1:
            sr0 = math.sqrt(se_sr2) * ((1 - _GAMMA) * _nppf(1 - 1.0 / N)
                                       + _GAMMA * _nppf(1 - 1.0 / (N * _E)))
        else:
            sr0 = 0.0
        dsr = round(_psr(sr_p, skew, kurt, n, sr0), 3)

        # PBO proxy : dégradation in-sample (1re moitié) → out-of-sample (2e moitié)
        half = len(r) // 2
        sr_is = _stats(r[:half])[0] * math.sqrt(ann)
        sr_oos = _stats(r[half:])[0] * math.sqrt(ann)
        degradation = round((sr_is - sr_oos), 2)
        pbo = round(float(np.clip((sr_is - sr_oos) / (abs(sr_is) + 1e-9), 0, 1)), 2) if sr_is > 0 else (1.0 if sr_oos < 0 else 0.0)

        # verdict de crédibilité
        if dsr >= 0.90 and folds_pos_pct >= 67 and pbo <= 0.5:
            verdict, color = 'CRÉDIBLE', '#22C55E'
        elif dsr >= 0.75 and folds_pos_pct >= 50:
            verdict, color = 'PROMETTEUR', '#FFB23F'
        else:
            verdict, color = 'FRAGILE', '#EF4444'
        note = ("DSR élevé + consistance walk-forward → edge crédible hors échantillon."
                if verdict == 'CRÉDIBLE' else
                "Signal présent mais à confirmer (plus d'historique / moins d'essais)."
                if verdict == 'PROMETTEUR' else
                "Risque de sur-optimisation : Sharpe non robuste hors échantillon. Prudence.")
        return {
            'ok': True, 'sharpe_ann': sharpe_ann, 'psr0': psr0, 'dsr': dsr,
            'n_trials': N, 'fold_sharpes': fold_sr, 'folds_positive_pct': folds_pos_pct,
            'sr_in_sample': round(sr_is, 2), 'sr_out_sample': round(sr_oos, 2),
            'degradation': degradation, 'pbo_estimate': pbo,
            'skew': round(skew, 2), 'kurtosis': round(kurt, 2), 'n': n,
            'verdict': verdict, 'color': color, 'note': note,
        }
    except Exception as e:
        return {'ok': False, 'note': f'{type(e).__name__}: {e}'}
