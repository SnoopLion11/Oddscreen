"""Settle the "track_sgp" alerts (logs/alerts_*.jsonl) and report how they did.

Tracking only, never a bet: did SGPs that quoter 99516d7e priced within
1.15x the plain leg product hit more often than its price implied?

  python scripts/sgp_track.py

Reads Kalshi market results (GET /markets only); unsettled legs are skipped.
A leg that settled at a price (e.g. player out) counts at that price.
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oddscreen import kalshi  # noqa: E402


def leg_value(result: dict | None, side: str) -> float | None:
    if not result or not result.get("result"):
        return None
    v = {"yes": 1.0, "no": 0.0}.get(result["result"], result.get("value"))
    return None if v is None else (v if side == "yes" else 1.0 - v)


def main() -> int:
    alerts, seen = [], set()
    for f in sorted(glob.glob(str(ROOT / "logs" / "alerts_*.jsonl"))):
        for line in open(f):
            a = json.loads(line)
            if a.get("kind") == "track_sgp" and (a["ts"], a["legs"]) not in seen:
                seen.add((a["ts"], a["legs"]))
                alerts.append(a)
    if not alerts:
        print("no track_sgp alerts yet")
        return 0
    env = kalshi.load_env(ROOT)
    client = kalshi.Client(kalshi.Signer.from_pem(env["KALSHI_API_KEY_ID"], env["KALSHI_PRIVATE_KEY"]),
                           env.get("KALSHI_API_URL", kalshi.DEFAULT_BASE))
    legs = lambda a: [tuple(x.rsplit(":", 1)) for x in a["legs"].split(" & ")]
    tickers = sorted({t for a in alerts for t, _ in legs(a)})
    res = {}
    for i in range(0, len(tickers), 100):
        for m in client.markets(tickers=",".join(tickers[i:i + 100])):
            res[m["ticker"]] = {"result": m.get("result"), "value": kalshi.dollars(m, "settlement_value_dollars")}
    done = []
    for a in alerts:
        vals = [leg_value(res.get(t), side) for t, side in legs(a)]
        if None in vals:
            continue
        pay = 1.0
        for v in vals:
            pay *= v
        done.append((a, pay))
    print(f"{len(alerts)} tracked, {len(done)} settled")
    if not done:
        return 0
    for lo, hi in ((0, 1.0), (1.0, 1.15)):
        g = [(a, p) for a, p in done if lo < a["ratio"] <= hi]
        if g:
            cost = sum(a["cost"] for a, _ in g)
            pay = sum(p for _, p in g)
            print(f"  price {lo:.2f}-{hi:.2f}x leg product: {len(g)} bets, {sum(p >= 0.999 for _, p in g)} won, "
                  f"wins expected {cost:.1f}, actual {pay:.1f}, return {100 * (pay / cost - 1):+.0f}% before fees")
    cost = sum(a["cost"] for a, _ in done)
    pay = sum(p for _, p in done)
    print(f"  all: return {100 * (pay / cost - 1):+.0f}% before fees on {len(done)} bets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
