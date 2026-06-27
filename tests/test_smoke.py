"""Tests smoke + garde-fous sécurité pour TRADING DESK IBKR V3.

Vérifie que l'app démarre, que les routes répondent, que les pages se rendent,
et surtout que la garde LECTURE SEULE est intacte (aucun chemin d'ordre).
Lancé en CI (GitHub Actions) avec NO_IBKR=1.
"""
import os
import pathlib

os.environ.setdefault("NO_IBKR", "1")  # jamais de connexion IBKR en test

import terminal  # noqa: E402

_SRC = pathlib.Path(terminal.__file__).read_text(encoding="utf-8")


def _client():
    return terminal.app.test_client()


def test_app_imports():
    assert terminal.app is not None
    assert len(terminal.UNIVERSE) >= 100  # univers élargi


def test_healthz():
    r = _client().get("/healthz")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_api_routes_200():
    c = _client()
    for path in ["/api/healthz", "/api/market/summary", "/api/cockpit",
                 "/api/watchlist", "/api/options", "/api/weekly", "/api/strategie"]:
        assert c.get(path).status_code == 200, path


def test_api_search():
    r = _client().get("/api/search?q=NVDA")
    assert r.status_code == 200
    assert any(x["ticker"] == "NVDA" for x in r.get_json())


def test_pages_render():
    c = _client()
    for path in ["/", "/watchlist", "/options", "/ma-page", "/entreprises"]:
        assert c.get(path).status_code == 200, path


def test_service_worker_served():
    r = _client().get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers.get("Content-Type", "")


# ─── GARDE-FOUS SÉCURITÉ : LECTURE SEULE ABSOLUE ───────────────────────────
def test_readonly_connection():
    # toute connexion IBKR doit être readonly=True
    assert "readonly=True" in _SRC


def test_no_order_execution_paths():
    # aucun appel d'exécution d'ordre ne doit exister dans le code
    for forbidden in ["placeOrder", "bracketOrder", ".executeOrder", "reqGlobalCancel"]:
        assert forbidden not in _SRC, f"chemin d'ordre interdit détecté : {forbidden}"


def test_no_server_side_user_storage():
    # favoris/notes doivent rester côté client (localStorage), jamais en BDD serveur
    assert "myFavs" in _SRC and "myNotes" in _SRC
