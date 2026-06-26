"""
elio/fundamentals.py — Fondamentaux par titre (yfinance tk.info) + médianes par secteur.

Permet de juger la VALORISATION d'un titre vs ses pairs : P/E du titre comparé au
P/E médian de son secteur (cher / dans la moyenne / décoté), marges, croissance, beta.

⚠️ tk.info est LENT et parfois incomplet (champs None) → tourné dans un thread dédié,
rafraîchi toutes les ~6 h. Étiqueter : fondamentaux yfinance, peuvent dater.
"""
import statistics

import yfinance as yf

from . import sectors


def _f(v):
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def build(symbols):
    """tk.info pour chaque titre → {by_sym:{sym:{...}}, by_sector:{sec:{median_pe,...}}}."""
    by_sym = {}
    for s in symbols:
        try:
            info = yf.Ticker(s).info or {}
        except Exception:
            continue
        by_sym[s] = {
            'pe': _f(info.get('trailingPE')),
            'fwd_pe': _f(info.get('forwardPE')),
            'pb': _f(info.get('priceToBook')),
            'peg': _f(info.get('pegRatio')),
            'margin': _f(info.get('profitMargins')),
            'growth': _f(info.get('revenueGrowth')),
            'beta': _f(info.get('beta')),
            'mcap': _f(info.get('marketCap')),
            'div': _f(info.get('dividendYield')),
            'sector': sectors.SECTOR_MAP.get(s),
        }

    by_sector = {}
    for sec in set(sectors.SECTOR_MAP.values()):
        members = [v for k, v in by_sym.items() if v.get('sector') == sec]
        pes = [v['pe'] for v in members if v.get('pe') and 0 < v['pe'] < 250]
        fwd = [v['fwd_pe'] for v in members if v.get('fwd_pe') and 0 < v['fwd_pe'] < 250]
        mg = [v['margin'] for v in members if v.get('margin') is not None]
        gr = [v['growth'] for v in members if v.get('growth') is not None]
        if pes or fwd:
            by_sector[sec] = {
                'median_pe': round(statistics.median(pes), 1) if pes else None,
                'median_fwd_pe': round(statistics.median(fwd), 1) if fwd else None,
                'median_margin': round(statistics.median(mg) * 100, 1) if mg else None,
                'median_growth': round(statistics.median(gr) * 100, 1) if gr else None,
                'n': len(members),
            }
    return {'by_sym': by_sym, 'by_sector': by_sector}


def valuation(pe, sector_median_pe):
    """Étiquette de valorisation d'un P/E vs la médiane de son secteur."""
    if not pe or not sector_median_pe or sector_median_pe <= 0:
        return None
    r = pe / sector_median_pe
    if r >= 1.3:
        return {'label': 'cher (premium)', 'ratio': round(r, 2), 'tone': 'warn'}
    if r <= 0.75:
        return {'label': 'décoté', 'ratio': round(r, 2), 'tone': 'good'}
    return {'label': 'dans la moyenne', 'ratio': round(r, 2), 'tone': 'neutral'}
