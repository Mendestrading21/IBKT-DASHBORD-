"""
ib_reader.py — Couche LECTURE SEULE vers Interactive Brokers (ib_async).

Cockpit Track — la lecture des donnees est ANALYSE ONLY.
- Connexion forcee en readonly=True (garde-fou structurel cote IBKR).
- AUCUNE methode d'ordre dans ce module, par design.
- Port PAPER (7497) par defaut. Le port REEL (7496) ne doit JAMAIS etre
  utilise sans une decision explicite et deliberee de l'utilisateur.

Le placement d'ordre (mode semi-auto, validation manuelle de CHAQUE ordre)
vivra dans un module SEPARE (order_panel.py), jamais ici.
"""
from __future__ import annotations

import pandas as pd

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

from ib_async import IB, Stock, util

PAPER_PORT = 7497  # TWS — compte PAPER (simule)
LIVE_PORT = 7496   # TWS — compte REEL : ne JAMAIS cibler sans decision explicite

# Ports API IBKR usuels, REEL d'abord (live-tws, paper-tws, gateway-live, gateway-paper)
COMMON_PORTS = [7496, 7497, 4001, 4002]


def _mode_label(port: int) -> str:
    return {
        7496: "REEL (TWS)",
        7497: "PAPER (TWS)",
        4001: "REEL (Gateway)",
        4002: "PAPER (Gateway)",
    }.get(port, f"port {port}")


class IBKRReader:
    """Lecture seule des donnees IBKR (compte, positions, bougies, options)."""

    def __init__(self, host: str = "127.0.0.1", port: int = PAPER_PORT, client_id: int = 11):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()

    # ---- connexion -------------------------------------------------------
    def connect(self) -> "IBKRReader":
        # readonly=True : cette session NE PEUT PAS placer d'ordre (verrou IBKR).
        self.ib.connect(self.host, self.port, clientId=self.client_id, readonly=True)
        print(f"[OK] Connecte a IBKR — {_mode_label(self.port)} — readonly")
        return self

    def connect_auto(self) -> "IBKRReader":
        """Sonde les ports API usuels (reel d'abord) et se connecte au 1er ouvert, en readonly."""
        order = [self.port] + [p for p in COMMON_PORTS if p != self.port]
        last_err = None
        for port in order:
            try:
                self.ib.connect(self.host, port, clientId=self.client_id, readonly=True)
                self.port = port
                print(f"[OK] Connecte a IBKR — {_mode_label(port)} — readonly")
                return self
            except Exception as e:  # port ferme / pas d'API sur ce port
                last_err = e
        raise ConnectionError(
            f"Aucun port API IBKR ouvert parmi {COMMON_PORTS}. "
            f"Active l'API dans TWS (Global Configuration > API > Settings). Detail: {last_err}"
        )

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()
            print("[OK] Deconnecte")

    # ---- lecture ---------------------------------------------------------
    def account_summary(self) -> dict:
        return {row.tag: row.value for row in self.ib.accountSummary()}

    def positions(self) -> pd.DataFrame:
        data = [
            {
                "symbol": p.contract.symbol,
                "secType": p.contract.secType,
                "currency": p.contract.currency,
                "position": p.position,
                "avgCost": p.avgCost,
            }
            for p in self.ib.positions()
        ]
        return pd.DataFrame(data)

    def historical_bars(
        self,
        symbol: str,
        duration: str = "1 Y",
        bar_size: str = "1 day",
        what: str = "TRADES",
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> pd.DataFrame:
        contract = Stock(symbol, exchange, currency)
        self.ib.qualifyContracts(contract)
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what,
            useRTH=True,
            formatDate=1,
        )
        return util.df(bars)

    def option_params(self, symbol: str):
        """Strikes & expirations REELS de la chaine d'options (lecture seule)."""
        stock = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(stock)
        return self.ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)


if __name__ == "__main__":
    print("Module LECTURE SEULE. Lance test_connection.py pour tester la connexion.")
