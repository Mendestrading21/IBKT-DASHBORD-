"""
test_connection.py — Lecture seule du compte (REEL en priorite) via ib_async.
Sonde les ports API IBKR, se connecte en readonly, et affiche
le resume de compte, les positions, et 5 bougies AAPL.

PREREQUIS (cote TWS) :
  1. TWS logge sur le compte voulu (ici : REEL).
  2. API activee : Global Configuration > API > Settings
       - cocher "Enable ActiveX and Socket Clients"
       - Socket port = 7496 (TWS reel) [paper = 7497]
       - 127.0.0.1 en "Trusted IPs" (ou "localhost only")
       - "Read-Only API" COCHE  -> verrou anti-ordre (on LIT le reel).
  3. A la 1re connexion, TWS demande "Accept incoming connection" -> Accept.
"""
from ib_reader import IBKRReader, LIVE_PORT


def main() -> None:
    reader = IBKRReader(port=LIVE_PORT)  # 7496 = REEL en 1er, puis fallback auto
    try:
        reader.connect_auto()
    except Exception as e:
        print(f"[ERREUR] {e}")
        print("  -> TWS lance + API activee + 127.0.0.1 en IP de confiance ?")
        return

    print("\n-- Resume compte --")
    summ = reader.account_summary()
    for tag in ("AccountType", "NetLiquidation", "TotalCashValue", "BuyingPower", "AvailableFunds"):
        if tag in summ:
            print(f"  {tag:18} {summ[tag]}")

    print("\n-- Positions --")
    pos = reader.positions()
    print(pos.to_string(index=False) if not pos.empty else "  (aucune position)")

    print("\n-- Bougies AAPL (daily, 5 dernieres) --")
    bars = reader.historical_bars("AAPL", duration="1 M", bar_size="1 day")
    if bars is not None and not bars.empty:
        cols = [c for c in ("date", "open", "high", "low", "close", "volume") if c in bars.columns]
        print(bars.tail()[cols].to_string(index=False))
    else:
        print("  (pas de donnees — verifie les souscriptions market data IBKR)")

    reader.disconnect()


if __name__ == "__main__":
    main()
