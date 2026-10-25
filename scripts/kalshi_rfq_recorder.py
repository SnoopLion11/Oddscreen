"""Log-only Kalshi combo RFQ recorder.

For each combo: price every leg from its own Kalshi order book (de-vigged,
worst case), create the combo market, send an RFQ, record every quote that
arrives during a short window, then DELETE the RFQ. It never accepts or
confirms a quote and cannot place orders (oddscreen/kalshi.py refuses those
paths in code).

Run on your own machine:

  # combos you choose
  python scripts/kalshi_rfq_recorder.py --combos combos.json

  # random cross-game combos from one collection
  python scripts/kalshi_rfq_recorder.py --collection KXMVESPORTSMULTIGAMEEXTENDED-R \
      --series KXNFLGAME,KXNFLSPREAD,KXNFLTOTAL --within-hours 48 --auto 20 --legs 2

  # list open combo collections
  python scripts/kalshi_rfq_recorder.py --list-collections

combos.json:
  [{"collection": "<COLLECTION_TICKER>",
    "legs": [{"market_ticker": "...", "event_ticker": "...", "side": "yes"}, ...]}]

Output (git-ignored): logs/combo_rfqs_<date>.csv (one row per RFQ) and
logs/combo_rfqs_<date>.jsonl (raw legs, books and every quote).
Needs KALSHI_API_KEY_ID plus either KALSHI_PRIVATE_KEY_PATH (a PEM file) or
KALSHI_PRIVATE_KEY (the PEM text itself), in .env or the environment.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oddscreen import combos, kalshi  # noqa: E402
from oddscreen.combos import Leg, Quote  # noqa: E402

LOGS = ROOT / "logs"

CSV_FIELDS = [
    "ts", "collection", "combo_ticker", "rfq_id", "n_legs", "legs", "independent",
    "leg_fairs", "fair", "naive_fair", "contracts", "n_quotes", "n_quoters", "consensus_mid", "mid_spread_c",
    "yes_cost", "yes_creator", "yes_fee", "yes_ev_pct", "yes_ev_pct_prefee", "yes_consensus", "yes_edge_vs_consensus_c",
    "no_cost", "no_creator", "no_fee", "no_ev_pct", "no_ev_pct_prefee", "no_consensus", "no_edge_vs_consensus_c",
    "first_quote_s", "error",
]


def _r(x, n=4):
    return "" if x is None else round(x, n)


def price_legs(client: kalshi.Client, legs: list[Leg], min_size: float,
               max_vig: float | None = None) -> tuple[list, list]:
    fairs, books = [], []
    for leg in legs:
        ob = client.orderbook(leg.market_ticker)
        ya, ys, na, ns = kalshi.best_asks(ob)
        books.append({"market_ticker": leg.market_ticker, "yes_ask": ya, "yes_size": ys, "no_ask": na, "no_size": ns})
        fairs.append(combos.leg_fair(leg.side, ya, na, ys, ns, min_size, max_vig))
    return fairs, books


def quote_bid(raw: dict, name: str):
    """A quote's bid in dollars; None when that side is not quoted (Kalshi sends "0.0000")."""
    v = kalshi.dollars(raw, name)
    return v if v else None


def collect_quotes(client: kalshi.Client, rfq_id: str, t0: float, wait_s: float, poll_s: float):
    seen: dict[str, tuple[Quote, dict]] = {}
    end = t0 + wait_s
    while time.monotonic() < end:
        for raw in client.quotes(rfq_id):
            if raw.get("rfq_id", rfq_id) != rfq_id:
                continue
            qid = str(raw.get("id") or raw.get("quote_id"))
            yb, nb = quote_bid(raw, "yes_bid"), quote_bid(raw, "no_bid")
            prev = seen.get(qid)
            if prev and (prev[0].yes_bid, prev[0].no_bid) == (yb, nb):
                continue
            first = prev[0].first_seen_s if prev else time.monotonic() - t0
            seen[qid] = (Quote(qid, str(raw.get("creator_id") or ""), yb, nb, first), raw)
        time.sleep(poll_s)
    return [q for q, _ in seen.values()], [r for _, r in seen.values()]


def record_one(client, collection: str, legs: list[Leg], args) -> tuple[dict, dict]:
    """Returns (csv row, raw record). A Kalshi error mid-way is stored in both,
    keeping whatever was already recorded."""
    row = {k: "" for k in CSV_FIELDS}
    raw: dict = {}
    try:
        _record(client, collection, legs, args, row, raw)
    except kalshi.KalshiError as e:
        row["error"] = raw["error"] = str(e)
    return row, raw


def _record(client, collection: str, legs: list[Leg], args, row: dict, raw: dict) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    row.update(ts=ts, collection=collection, n_legs=len(legs), contracts=args.contracts,
               legs=" & ".join(f"{l.market_ticker}:{l.side}" for l in legs),
               independent=combos.independent(legs))
    raw.update(ts=ts, collection=collection, legs=[l.as_api() for l in legs])

    fairs, books = price_legs(client, legs, args.min_size, args.max_vig)
    raw["books"] = books
    fair = combos.combo_fair(fairs) if row["independent"] else None
    row["leg_fairs"] = " ".join("-" if f is None else f"{f.fair:.4f}" for f in fairs)
    row["fair"] = _r(fair)
    row["naive_fair"] = _r(combos.combo_fair(fairs))   # product even for same-game legs (ignores correlation)

    combo = client.create_combo_market(collection, [l.as_api() for l in legs])
    ticker = combo.get("market_ticker") or (combo.get("market") or {}).get("ticker")
    row["combo_ticker"] = ticker
    raw["combo"] = combo

    rfq = client.create_rfq(ticker, args.contracts)
    raw["rfq_create"] = rfq
    rfq_id = str(rfq.get("id") or rfq.get("rfq_id") or (rfq.get("rfq") or {}).get("id"))
    row["rfq_id"] = rfq_id
    t0 = time.monotonic()
    try:
        quotes, raw_quotes = collect_quotes(client, rfq_id, t0, args.wait, args.poll)
    finally:
        try:
            client.delete_rfq(rfq_id)
        except kalshi.KalshiError as e:
            raw["delete_error"] = str(e)
    raw["quotes"] = raw_quotes

    s = combos.summarize(quotes, fair, args.contracts, args.fee_mult)
    row.update(n_quotes=s.n_quotes, n_quoters=s.n_quoters, consensus_mid=_r(s.consensus_mid),
               mid_spread_c=_r(s.mid_spread_c, 2),
               first_quote_s=_r(min((q.first_seen_s for q in quotes), default=None), 2))
    for name in ("yes", "no"):
        r = getattr(s, name)
        row.update({
            f"{name}_cost": _r(r.cost), f"{name}_creator": r.creator, f"{name}_fee": _r(r.fee),
            f"{name}_ev_pct": _r(r.ev_pct_fair, 2), f"{name}_ev_pct_prefee": _r(r.ev_pct_fair_prefee, 2),
            f"{name}_consensus": _r(r.consensus), f"{name}_edge_vs_consensus_c": _r(r.edge_vs_consensus_c, 2),
        })


# --- auto combo generation ---------------------------------------------------

def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def game_start(m: dict) -> float | None:
    """Scheduled start of a game market: close_time is set 48 h after it
    (checked on NBA and NFL markets; the rules allow a 48 h postponement)."""
    return _ts(m["close_time"]) - 48 * 3600 if m.get("close_time") else None


def group_by_game(events: list[dict], markets: list[dict], within_hours: float | None = None,
                  now: float | None = None, pregame_minutes: float | None = None) -> dict[str, list[tuple[dict, dict]]]:
    """Open markets of the collection's events, grouped by game.

    Markets whose event is not in the collection are dropped. With
    within_hours, only markets expected to settle within that many hours
    (expected_expiration_time; close_time is days after the game) are kept.
    With pregame_minutes, only games starting at least that far ahead.
    """
    by_event = {ev["ticker"]: ev for ev in events}
    now = time.time() if now is None else now
    out: dict[str, list[tuple[dict, dict]]] = {}
    for m in markets:
        ev = by_event.get(m.get("event_ticker"))
        if ev is None:
            continue
        if within_hours is not None:
            when = m.get("expected_expiration_time") or m.get("occurrence_datetime") or m.get("close_time")
            if not when or not now < _ts(when) <= now + within_hours * 3600:
                continue
        if pregame_minutes is not None:
            start = game_start(m)
            if start is None or start < now + pregame_minutes * 60:
                continue
        out.setdefault(combos.game_key(ev["ticker"]), []).append((ev, m))
    return out


def sample_legs(by_game: dict[str, list[tuple[dict, dict]]], n_legs: int, same_game: int,
                rng: random.Random) -> list[Leg]:
    """n_legs legs: `same_game` of them from one game (each from a different
    event, e.g. moneyline + total), the rest from other games, one each."""
    def leg(ev, m):
        return Leg(m["ticker"], ev["ticker"], "yes" if ev.get("is_yes_only") else rng.choice(["yes", "no"]))

    legs, games = [], list(by_game)
    if same_game > 1:
        events = lambda g: {ev["ticker"] for ev, _ in by_game[g]}
        g0 = rng.choice([g for g in games if len(events(g)) >= same_game])
        for et in rng.sample(sorted(events(g0)), same_game):
            legs.append(leg(*rng.choice([(ev, m) for ev, m in by_game[g0] if ev["ticker"] == et])))
        games = [g for g in games if g != g0]
    for g in rng.sample(games, n_legs - len(legs)):
        legs.append(leg(*rng.choice(by_game[g])))
    return legs


def auto_combos(client: kalshi.Client, collection_ticker: str, n: int, n_legs: int, rng: random.Random,
                series: list[str] | None = None, within_hours: float | None = None, same_game: int = 1,
                pregame_minutes: float | None = None):
    col = client.collection(collection_ticker)
    lo, hi = col.get("size_min") or 2, col.get("size_max") or n_legs   # size_max 0 = no limit
    if not lo <= n_legs <= hi:
        raise SystemExit(f"{collection_ticker} takes {lo}-{hi} legs, not {n_legs}")
    events = col.get("associated_events") or [{"ticker": t} for t in col.get("associated_event_tickers", [])]
    if series:
        markets = [m for s in series for m in client.markets(series_ticker=s, status="open")]
    elif len(events) > 200:
        raise SystemExit(f"{collection_ticker} has {len(events)} events; pick some with --series, "
                         "e.g. --series KXNFLGAME,KXNFLSPREAD,KXNFLTOTAL")
    else:
        markets = [m for ev in events for m in client.markets(event_ticker=ev["ticker"], status="open")]
    by_game = group_by_game(events, markets, within_hours, pregame_minutes=pregame_minutes)
    games = list(by_game)
    same_game = max(1, min(same_game, n_legs))
    deep = [g for g in games if len({ev["ticker"] for ev, _ in by_game[g]}) >= same_game]
    if not deep or len(games) < n_legs - same_game + 1:
        raise SystemExit(f"{collection_ticker}: not enough open games for {n_legs} legs with {same_game} from one game")
    print(f"{collection_ticker}: {len(games)} games, {sum(map(len, by_game.values()))} open leg markets")
    return [(collection_ticker, sample_legs(by_game, n_legs, same_game, rng)) for _ in range(n)]


def csv_log_path(logs: Path, day: str) -> Path:
    """Today's CSV, or combo_rfqs_<day>_2.csv etc. if an older one has different columns."""
    header = ",".join(CSV_FIELDS)
    for i in range(1, 100):
        p = logs / (f"combo_rfqs_{day}.csv" if i == 1 else f"combo_rfqs_{day}_{i}.csv")
        if not p.exists() or p.open().readline().strip() == header:
            return p
    raise SystemExit("too many CSV logs for today")


def load_combos(path: Path):
    out = []
    for c in json.loads(path.read_text()):
        out.append((c["collection"], [Leg(l["market_ticker"], l["event_ticker"], l["side"]) for l in c["legs"]]))
    return out


# --- main --------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--combos", type=Path, help="JSON file of combos to quote")
    src.add_argument("--collection", help="collection ticker to draw random cross-game combos from")
    src.add_argument("--list-collections", action="store_true", help="print open combo collections and exit")
    ap.add_argument("--auto", type=int, default=10, help="with --collection: number of combos")
    ap.add_argument("--legs", type=int, default=2, help="with --collection: legs per combo")
    ap.add_argument("--series", help="with --collection: comma-separated series to draw legs from, "
                    "e.g. KXNFLGAME,KXNFLSPREAD,KXNFLTOTAL")
    ap.add_argument("--same-game", type=int, default=1,
                    help="with --collection: how many legs come from one game (correlated); 1 = all different games")
    ap.add_argument("--pregame-minutes", type=float, default=None,
                    help="with --collection: only games starting at least this many minutes from now")
    ap.add_argument("--within-hours", type=float, default=None,
                    help="with --collection: only legs expected to settle within this many hours")
    ap.add_argument("--contracts", type=int, default=10, help="RFQ size in contracts")
    ap.add_argument("--wait", type=float, default=8.0, help="seconds to collect quotes per RFQ")
    ap.add_argument("--poll", type=float, default=0.5, help="seconds between quote polls")
    ap.add_argument("--gap", type=float, default=15.0, help="seconds between RFQs")
    ap.add_argument("--min-size", type=float, default=0.0, help="min contracts at a leg's best ask to price it")
    ap.add_argument("--max-vig", type=float, default=0.06,
                    help="don't price a leg whose YES ask + NO ask exceeds 1 by more than this")
    ap.add_argument("--fee-mult", type=float, default=1.0, help="taker fee multiplier; 0 = ignore fees")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--demo", action="store_true", help="use Kalshi's demo environment")
    args = ap.parse_args()

    env = kalshi.load_env(ROOT)
    key_id = env.get("KALSHI_API_KEY_ID", "")
    key_pem, key_path = env.get("KALSHI_PRIVATE_KEY", ""), env.get("KALSHI_PRIVATE_KEY_PATH", "")
    if not key_id or not (key_pem or key_path):
        print("Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY (PEM text) or KALSHI_PRIVATE_KEY_PATH")
        return 2
    signer = (kalshi.Signer.from_pem(key_id, key_pem) if key_pem
              else kalshi.Signer.from_pem_file(key_id, ROOT / key_path))
    base = kalshi.DEMO_BASE if args.demo else env.get("KALSHI_API_URL", kalshi.DEFAULT_BASE)
    client = kalshi.Client(signer, base)
    print(f"Kalshi {base}  (log-only: quotes are never accepted)")

    if args.list_collections:
        for c in client.collections(status="open"):
            print(f"{c.get('collection_ticker', ''):40} legs {c.get('size_min')}-{c.get('size_max')}  {c.get('title', '')}")
        return 0

    rng = random.Random(args.seed)
    todo = load_combos(args.combos) if args.combos else auto_combos(
        client, args.collection, args.auto, args.legs, rng,
        [x.strip() for x in args.series.split(",") if x.strip()] if args.series else None, args.within_hours,
        args.same_game, args.pregame_minutes)

    LOGS.mkdir(exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    csv_path, jsonl_path = csv_log_path(LOGS, day), LOGS / f"combo_rfqs_{day}.jsonl"
    new_csv = not csv_path.exists()
    with csv_path.open("a", newline="") as cf, jsonl_path.open("a") as jf:
        w = csv.DictWriter(cf, fieldnames=CSV_FIELDS)
        if new_csv:
            w.writeheader()
        for i, (collection, legs) in enumerate(todo, 1):
            if i > 1:
                time.sleep(args.gap)
            row, raw = record_one(client, collection, legs, args)
            raw["row"] = row          # the CSV row too, so the .jsonl alone is a complete log
            w.writerow(row)
            jf.write(json.dumps(raw, default=str) + "\n")
            cf.flush()
            jf.flush()
            print(f"[{i}/{len(todo)}] {row['legs']}\n    fair {row['fair'] or '-'}  quoters {row['n_quoters'] or 0}"
                  f"  YES {row['yes_cost'] or '-'} (EV {row['yes_ev_pct'] or '-'}%, vs others {row['yes_edge_vs_consensus_c'] or '-'}c)"
                  f"  NO {row['no_cost'] or '-'} (EV {row['no_ev_pct'] or '-'}%, vs others {row['no_edge_vs_consensus_c'] or '-'}c)"
                  + (f"\n    error: {row['error']}" if row["error"] else ""))
    print(f"\nwrote {csv_path}\n      {jsonl_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
