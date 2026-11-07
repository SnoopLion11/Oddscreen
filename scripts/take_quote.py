"""Take ONE +EV combo quote that the recorder held open, after the user approves it.

The recorder (nba_watch.py --ask) keeps a +EV RFQ open for a few minutes and
writes logs/pending/<rfq_id>.json. Nothing is bought until someone runs:

  python scripts/take_quote.py list                     # what is waiting
  python scripts/take_quote.py take <rfq_id> --approved # user said "take it"
  python scripts/take_quote.py cancel <rfq_id>          # user said no

`take` re-checks everything before accepting:
  - the approval window has not passed,
  - the quote is still open at the same (or a better) price,
  - the legs still price at least --min-pct %% and --min-c cents under fair
    after fees (Novig/Kalshi re-priced now),
  - the stake fits --max-stake and tonight's total fits --night-max,
  - the balance covers it.
Then it accepts that one quote (the maker still has ~3 s to confirm), waits
for the result, checks the fill's side, logs to logs/takes.jsonl and closes
the RFQ. It cannot confirm quotes or place orders.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from oddscreen import combos, kalshi, novig  # noqa: E402
import kalshi_rfq_recorder as rec  # noqa: E402

PENDING = ROOT / "logs" / "pending"
TAKES = ROOT / "logs" / "takes.jsonl"


def client_from_env() -> kalshi.TradingClient:
    env = kalshi.load_env(ROOT)
    signer = kalshi.Signer.from_pem(env["KALSHI_API_KEY_ID"], env["KALSHI_PRIVATE_KEY"])
    return kalshi.TradingClient(signer, env.get("KALSHI_API_URL", kalshi.DEFAULT_BASE))


def spent_tonight(hours: float = 12) -> float:
    if not TAKES.exists():
        return 0.0
    since = time.time() - hours * 3600
    return sum(t.get("total", 0) for t in map(json.loads, TAKES.open())
               if t.get("at", 0) >= since and t.get("status") in ("executed", "accepted", "confirmed"))


def log(entry: dict) -> None:
    TAKES.parent.mkdir(exist_ok=True)
    with TAKES.open("a") as f:
        f.write(json.dumps({**entry, "at": time.time()}) + "\n")


def close(c, p, path: Path, suffix: str) -> None:
    try:
        c.delete_rfq(p["rfq_id"])
    except kalshi.KalshiError:
        pass
    path.rename(path.with_suffix(suffix))


def reprice(c, p, max_vig: float) -> tuple[float | None, list[str]]:
    """Fair of our side now, re-priced like the recorder does."""
    legs = [rec.Leg(l["market_ticker"], l["event_ticker"], l["side"]) for l in p["legs"]]
    fairs, books = rec.price_legs(c, legs, 0.0, max_vig, novig.PropBook())
    if any(f is None for f in fairs):
        return None, [b.get("fair_src", "?") for b in books]
    fair = combos.combo_fair(fairs)
    return (fair if p["side"] == "yes" else 1 - fair), [b.get("fair_src", "?") for b in books]


def take(c, p, path: Path, a) -> int:
    if time.time() > p["expires"]:
        close(c, p, path, ".expired")
        print("too late: the approval window passed; RFQ closed")
        return 1
    total = p["total"]
    if total > a.max_stake + 1e-9:
        print(f"refused: stake ${total:.2f} is over ${a.max_stake:.2f}")
        return 1
    if spent_tonight() + total > a.night_max + 1e-9:
        print(f"refused: tonight's total would pass ${a.night_max:.2f}")
        return 1
    bal = c.balance()
    avail = kalshi.dollars(bal, "balance") or 0.0
    if avail < total:
        print(f"refused: balance ${avail:.2f} is less than ${total:.2f}")
        return 1
    q = c.rfq_quote(p["rfq_id"], p["quote_id"])
    key = "no_bid" if p["side"] == "yes" else "yes_bid"
    bid = kalshi.dollars(q, key) or 0.0
    if q.get("status") != "open" or not bid or 1 - bid > p["cost"] + 1e-9:
        print(f"refused: quote is {q.get('status')} at {1 - bid if bid else '-'} (was {p['cost']})")
        return 1
    cost = 1 - bid
    fee = combos.taker_fee_per_contract(cost, p["contracts"])
    fair, src = reprice(c, p, a.max_vig)
    if fair is None:
        print(f"refused: can't re-price every leg now ({src})")
        return 1
    ev = (fair / (cost + fee) - 1) * 100
    edge_c = (fair - cost - fee) * 100
    print(f"now: cost {cost:.3f} + fee {fee:.3f}, fair {fair:.3f} ({'/'.join(src)}), EV {ev:+.1f}%, edge {edge_c:+.1f}c")
    need = rec.required_ev_pct(cost, a.min_pct)
    if ev < need or edge_c < a.min_c:
        print(f"refused: no longer +EV enough (needs {need:.0f}% at this price)")
        return 1
    c.accept_quote(p["rfq_id"], p["quote_id"], p["side"])
    print(f"accepted {p['contracts']} x {p['side'].upper()} at {cost:.3f}; waiting for the maker to confirm")
    status, q2 = "accepted", q
    for _ in range(20):
        time.sleep(1)
        q2 = c.rfq_quote(p["rfq_id"], p["quote_id"])
        status = q2.get("status", status)
        if status in ("executed", "cancelled"):
            break
    fills = []
    if status == "executed":
        time.sleep(1)
        fills = [f for f in c.fills(ticker=p["combo_ticker"]) if f.get("ticker") == p["combo_ticker"]]
    sides = sorted({f.get("outcome_side") for f in fills})
    log({**p, "status": status, "cost_now": cost, "fee_now": fee, "fair_now": fair, "ev_now": ev,
         "accepted_side_sent": "no" if p["side"] == "yes" else "yes", "fill_sides": sides,
         "cancellation_reason": q2.get("cancellation_reason")})
    close(c, p, path, f".{status}")
    print(f"result: {status}" + (f" ({q2.get('cancellation_reason')})" if q2.get("cancellation_reason") else ""))
    if fills:
        print(f"fills: {len(fills)}, side {sides}" + ("" if sides == [p["side"]] else "  << SIDE MISMATCH, check!"))
    return 0 if status == "executed" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["list", "take", "cancel"])
    ap.add_argument("rfq_id", nargs="?")
    ap.add_argument("--approved", action="store_true", help="required for take: the user approved this quote")
    ap.add_argument("--max-stake", type=float, default=250.0)
    ap.add_argument("--night-max", type=float, default=500.0)
    ap.add_argument("--min-pct", type=float, default=5.0)
    ap.add_argument("--min-c", type=float, default=2.0)
    ap.add_argument("--max-vig", type=float, default=0.06)
    a = ap.parse_args()
    files = sorted(PENDING.glob("*.json")) if PENDING.exists() else []
    if a.cmd == "list":
        for f in files:
            p = json.loads(f.read_text())
            left = p["expires"] - time.time()
            print(f"{p['rfq_id']}  {p['side'].upper()} {p['contracts']} @ {p['cost']:.3f} (${p['total']:.2f})  "
                  f"EV {p['ev_pct']:+.1f}% {p['edge_c']:.1f}c  {'expired' if left < 0 else f'{left:.0f}s left'}  "
                  + " & ".join(p["titles"]))
        if not files:
            print("nothing pending")
        return 0
    path = PENDING / f"{a.rfq_id}.json"
    if not path.exists():
        print("no such pending quote")
        return 1
    p = json.loads(path.read_text())
    c = client_from_env()
    if a.cmd == "cancel":
        close(c, p, path, ".cancelled")
        print("RFQ closed, nothing taken")
        return 0
    if not a.approved:
        print("refused: pass --approved only after the user approves this quote")
        return 1
    return take(c, p, path, a)


if __name__ == "__main__":
    raise SystemExit(main())
