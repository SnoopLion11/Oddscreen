"""Log-only Kalshi combo RFQ recorder.

For each combo: price every leg from its own Kalshi order book (de-vigged,
worst case; NBA player props from Novig's public two-way prices when the
same player/stat/line is listed there, see oddscreen/novig.py), create the
combo market, send an RFQ, record every quote that
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
import math
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oddscreen import combos, kalshi, novig  # noqa: E402
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
               max_vig: float | None = None, props: novig.PropBook | None = None) -> tuple[list, list]:
    """Leg fairs and the books they came from. With `props`, a player-prop leg
    that matches a Novig market is de-vigged on Novig's two sides instead of
    Kalshi's own book (Kalshi YES = Novig Over); otherwise Kalshi's book."""
    fairs, books = [], []
    for leg in legs:
        ob = client.orderbook(leg.market_ticker)
        ya, ys, na, ns = kalshi.best_asks(ob)
        book = {"market_ticker": leg.market_ticker, "yes_ask": ya, "yes_size": ys, "no_ask": na, "no_size": ns,
                "fair_src": "kalshi"}
        fair = combos.leg_fair(leg.side, ya, na, ys, ns, min_size, max_vig)
        if props is not None and leg.market_ticker.split("-")[0] in novig.KALSHI_STATS:
            m = client.market(leg.market_ticker)
            hit = props.lookup(leg.market_ticker, m.get("title", ""), m.get("floor_strike"), game_start(m))
            if hit:
                pp, nm = hit
                nf = combos.leg_fair(leg.side, pp.over_ask, pp.under_ask, pp.over_qty, pp.under_qty, 0.0, max_vig)
                book["novig"] = {"market_id": pp.market_id, "description": pp.description,
                                 "over_ask": pp.over_ask, "under_ask": pp.under_ask,
                                 "over_qty": pp.over_qty, "under_qty": pp.under_qty,
                                 "fair": None if nf is None else nf.fair}
                if nf is not None:
                    fair, book["fair_src"] = nf, "novig"
        books.append(book)
        fairs.append(fair)
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

    fairs, books = price_legs(client, legs, args.min_size, args.max_vig, getattr(args, "props", None))
    raw["books"] = books
    raw["leg_fair_src"] = [b["fair_src"] for b in books]
    fair = combos.combo_fair(fairs) if row["independent"] else None
    row["leg_fairs"] = " ".join("-" if f is None else f"{f.fair:.4f}" for f in fairs)
    row["fair"] = _r(fair)
    row["naive_fair"] = _r(combos.combo_fair(fairs))   # product even for same-game legs (ignores correlation)

    if getattr(args, "target_stake", 0):
        args = sized_args(args, fair if fair is not None else combos.combo_fair(fairs))
        row["contracts"] = args.contracts

    combo = client.create_combo_market(collection, [l.as_api() for l in legs])
    ticker = combo.get("market_ticker") or (combo.get("market") or {}).get("ticker")
    row["combo_ticker"] = ticker
    raw["combo"] = combo

    rfq = client.create_rfq(ticker, args.contracts)
    raw["rfq_create"] = rfq
    rfq_id = str(rfq.get("id") or rfq.get("rfq_id") or (rfq.get("rfq") or {}).get("id"))
    row["rfq_id"] = rfq_id
    t0 = time.monotonic()
    held = False
    try:
        quotes, raw_quotes = collect_quotes(client, rfq_id, t0, args.wait, args.poll)
        raw["quotes"] = raw_quotes
        s = combos.summarize(quotes, fair, args.contracts, args.fee_mult)
        held = maybe_hold(client, rfq_id, ticker, legs, quotes, s, fair, args, raw)
    finally:
        if not held:
            try:
                client.delete_rfq(rfq_id)
            except kalshi.KalshiError as e:
                raw["delete_error"] = str(e)

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


PENDING = LOGS / "pending"

# The user's rule (Nov 6): longer odds need more edge. (min price, min EV %% after fees)
EV_TIERS = ((0.50, 5.0), (0.25, 15.0), (0.0, 25.0))


def required_ev_pct(cost: float, floor: float = 0.0) -> float:
    """EV %% (after fees) a take at this price must have: 5%% at 50c+, 15%% at
    25-50c, 25%% under 25c; never below `floor`."""
    for min_price, pct in EV_TIERS:
        if cost >= min_price:
            return max(pct, floor)
    return max(EV_TIERS[-1][1], floor)


def sized_args(args, yes_fair):
    """Copy of args with --contracts set so a take costs about --target-stake on
    the cheaper side, but never more than --max-stake on the pricier side."""
    import copy
    a = copy.copy(args)
    if yes_fair is None or not 0 < yes_fair < 1:
        return a
    lo, hi = min(yes_fair, 1 - yes_fair), max(yes_fair, 1 - yes_fair)
    n = min(math.ceil(args.target_stake / lo), math.floor(args.max_stake / (hi * 1.03)))
    a.contracts = max(1, n)
    return a


def maybe_hold(client, rfq_id, ticker, legs, quotes, s, fair, args, raw) -> bool:
    """With --hold-s: if the best price on a side is at least --hold-pct %% AND
    --hold-min-c cents under fair after fees, and the whole stake fits in
    --max-stake, keep the RFQ open and write logs/pending/<rfq_id>.json for the
    user to approve (scripts/take_quote.py). Nothing is accepted here."""
    if not args.hold_s or fair is None:
        return False
    for side in ("yes", "no"):
        r = getattr(s, side)
        if r.cost is None or r.ev_pct_fair is None:
            continue
        edge_c = r.ev_pct_fair / 100 * (r.cost + r.fee) * 100
        total = args.contracts * (r.cost + r.fee)
        if r.ev_pct_fair < required_ev_pct(r.cost, args.hold_pct) or edge_c < args.hold_min_c:
            continue
        if total > args.max_stake + 1e-9:
            raw["hold_skipped"] = f"{side}: stake ${total:.2f} over ${args.max_stake:.2f}"
            continue
        key = "no_bid" if side == "yes" else "yes_bid"     # we buy YES at 1 - no_bid
        best = [q for q in quotes if getattr(q, key) and abs(1 - getattr(q, key) - r.cost) < 1e-9]
        if not best:
            continue
        q = min(best, key=lambda q: q.first_seen_s)
        titles = []
        for l in legs:
            try:
                titles.append(f"{'NO ' if l.side == 'no' else ''}{client.market(l.market_ticker).get('title', l.market_ticker)}")
            except kalshi.KalshiError:
                titles.append(f"{l.market_ticker}:{l.side}")
        side_fair = fair if side == "yes" else 1 - fair
        pend = dict(rfq_id=rfq_id, quote_id=q.quote_id, creator=q.creator, combo_ticker=ticker, side=side,
                    cost=round(r.cost, 4), fee=round(r.fee, 4), contracts=args.contracts, total=round(total, 2),
                    fair=round(side_fair, 4), ev_pct=round(r.ev_pct_fair, 2), edge_c=round(edge_c, 2),
                    legs=[l.as_api() for l in legs], titles=titles,
                    created=time.time(), expires=time.time() + args.hold_s)
        PENDING.mkdir(parents=True, exist_ok=True)
        (PENDING / f"{rfq_id}.json").write_text(json.dumps(pend, indent=1))
        raw["held"] = pend
        return True
    return False


def cleanup_pending(client) -> None:
    """Delete RFQs whose approval window has passed."""
    for f in sorted(PENDING.glob("*.json")) if PENDING.exists() else []:
        p = json.loads(f.read_text())
        if time.time() > p["expires"]:
            try:
                client.delete_rfq(p["rfq_id"])
            except kalshi.KalshiError:
                pass
            f.rename(f.with_suffix(".expired"))


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

    Markets whose event is not in the collection, or whose trading is paused
    (status "inactive"), are dropped. With
    within_hours, only markets expected to settle within that many hours
    (expected_expiration_time; close_time is days after the game) are kept.
    With pregame_minutes, only games starting at least that far ahead.
    """
    by_event = {ev["ticker"]: ev for ev in events}
    now = time.time() if now is None else now
    out: dict[str, list[tuple[dict, dict]]] = {}
    for m in markets:
        ev = by_event.get(m.get("event_ticker"))
        if ev is None or m.get("status", "active") not in ("active", "open"):
            continue                      # paused markets can't go in a combo
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
        side = m.get("_side") or ("yes" if ev.get("is_yes_only") else rng.choice(["yes", "no"]))
        return Leg(m["ticker"], ev["ticker"], side)

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


def kalshi_listed_fair(m: dict, max_vig: float | None) -> combos.LegFair | None:
    """YES fair of a Kalshi market from the asks in its market listing (no orderbook call)."""
    ya, na = kalshi.dollars(m, "yes_ask_dollars"), kalshi.dollars(m, "no_ask_dollars")
    return combos.leg_fair("yes", ya, na, max_vig=max_vig)


def novig_edge_legs(by_game: dict[str, list[tuple[dict, dict]]], props: novig.PropBook, min_gap: float,
                    max_vig: float | None) -> dict[str, list[tuple[dict, dict]]]:
    """Only the player-prop markets whose Kalshi price is at least min_gap away
    from Novig's de-vigged fair, each with its side preset to the one Kalshi
    underprices (Novig fair > Kalshi fair): if quoters price from Kalshi's
    legs, a combo of such legs inherits the gap."""
    out: dict[str, list[tuple[dict, dict]]] = {}
    for g, lst in by_game.items():
        for ev, m in lst:
            if m["ticker"].split("-")[0] not in novig.KALSHI_STATS:
                continue
            kf = kalshi_listed_fair(m, max_vig)
            hit = props.lookup(m["ticker"], m.get("title", ""), m.get("floor_strike"), game_start(m))
            if kf is None or not hit:
                continue
            nf = combos.leg_fair("yes", hit[0].over_ask, hit[0].under_ask, max_vig=max_vig)
            if nf is None or abs(nf.fair - kf.fair) < min_gap:
                continue
            out.setdefault(g, []).append((ev, {**m, "_side": "yes" if nf.fair > kf.fair else "no",
                                               "_gap": nf.fair - kf.fair}))
    return out


def expand_series(series: list[str], events: list[dict]) -> list[str]:
    """"KXNBA*" becomes every series in the collection starting with KXNBA."""
    have = sorted({ev["ticker"].split("-")[0] for ev in events})
    out: list[str] = []
    for s in series:
        for x in ([h for h in have if h.startswith(s[:-1])] if s.endswith("*") else [s]):
            if x not in out:
                out.append(x)
    return out


def auto_combos(client: kalshi.Client, collection_ticker: str, n: int, n_legs: int, rng: random.Random,
                series: list[str] | None = None, within_hours: float | None = None, same_game: int = 1,
                pregame_minutes: float | None = None, mix: list[tuple[int, int]] | None = None,
                props: novig.PropBook | None = None, novig_edge: float | None = None,
                max_vig: float | None = None):
    """n combos for each (legs, same_game) in mix (default: just n_legs, same_game).
    With novig_edge (and props), legs come only from novig_edge_legs()."""
    mix = mix or [(n_legs, same_game)]
    col = client.collection(collection_ticker)
    lo = col.get("size_min") or 2
    hi = col.get("size_max") or max(l for l, _ in mix)   # size_max 0 = no limit
    for legs, _ in mix:
        if not lo <= legs <= hi:
            raise SystemExit(f"{collection_ticker} takes {lo}-{hi} legs, not {legs}")
    events = col.get("associated_events") or [{"ticker": t} for t in col.get("associated_event_tickers", [])]
    if series:
        series = expand_series(series, events)
        markets = [m for s in series for m in client.markets(series_ticker=s, status="open")]
    elif len(events) > 200:
        raise SystemExit(f"{collection_ticker} has {len(events)} events; pick some with --series, "
                         "e.g. --series KXNFLGAME,KXNFLSPREAD,KXNFLTOTAL or --series 'KXNBA*'")
    else:
        markets = [m for ev in events for m in client.markets(event_ticker=ev["ticker"], status="open")]
    by_game = group_by_game(events, markets, within_hours, pregame_minutes=pregame_minutes)
    if novig_edge is not None:
        if props is None or props.error:
            raise SystemExit("--novig-edge needs Novig prices: " + (props.error if props else "disabled"))
        by_game = novig_edge_legs(by_game, props, novig_edge, max_vig)
        print(f"Novig edge >= {novig_edge:.2f}: " + ", ".join(
            f"{m['ticker']}:{m['_side']} {m['_gap']:+.3f}" for lst in by_game.values() for _, m in lst))
    games = list(by_game)
    print(f"{collection_ticker}: {len(games)} games, {sum(map(len, by_game.values()))} open leg markets")
    out = []
    for legs, same in mix:
        same = max(1, min(same, legs))
        deep = [g for g in games if len({ev["ticker"] for ev, _ in by_game[g]}) >= same]
        if not deep or len(games) < legs - same + 1:
            print(f"  skipped {legs} legs with {same} from one game: not enough open games")
            continue
        out += [(collection_ticker, sample_legs(by_game, legs, same, rng)) for _ in range(n)]
    if not out:
        raise SystemExit(f"{collection_ticker}: not enough open games")
    return out


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
    ap.add_argument("--mix", help="with --collection: several combo shapes as legs:same_game pairs, "
                    "e.g. 2:2,2:1,3:3,3:1 (--auto combos of each)")
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
    ap.add_argument("--gap-max", type=float, default=None,
                    help="if set, each gap is random between --gap and this (no fixed rhythm)")
    ap.add_argument("--min-size", type=float, default=0.0, help="min contracts at a leg's best ask to price it")
    ap.add_argument("--max-vig", type=float, default=0.06,
                    help="don't price a leg whose YES ask + NO ask exceeds 1 by more than this")
    ap.add_argument("--fee-mult", type=float, default=1.0, help="taker fee multiplier; 0 = ignore fees")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--demo", action="store_true", help="use Kalshi's demo environment")
    ap.add_argument("--novig-edge", type=float, default=None,
                    help="with --collection: only player-prop legs whose Kalshi price is at least this far "
                    "(e.g. 0.02) from Novig's fair, on the side Kalshi underprices")
    ap.add_argument("--hold-s", type=float, default=0,
                    help="keep a +EV RFQ open this many seconds for the user's approval (0 = never)")
    ap.add_argument("--hold-pct", type=float, default=5.0)
    ap.add_argument("--hold-min-c", type=float, default=2.0)
    ap.add_argument("--max-stake", type=float, default=5.0, help="max $ for one approved take, fees included")
    ap.add_argument("--target-stake", type=float, default=0,
                    help="size each RFQ so a take costs about this much (0 = use --contracts)")
    ap.add_argument("--no-novig", action="store_true",
                    help="price player props from Kalshi's own book instead of Novig's public prices")
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
    args.props = None if args.no_novig else novig.PropBook(league=env.get("NOVIG_LEAGUE", "NBA"))
    if args.props is not None:
        n = len(args.props.markets())
        print(f"Novig public prices: {n} NBA markets" if not args.props.error
              else f"Novig unreachable ({args.props.error}); props use Kalshi's book")

    cleanup_pending(client)
    if args.list_collections:
        for c in client.collections(status="open"):
            print(f"{c.get('collection_ticker', ''):40} legs {c.get('size_min')}-{c.get('size_max')}  {c.get('title', '')}")
        return 0

    rng = random.Random(args.seed)
    todo = load_combos(args.combos) if args.combos else auto_combos(
        client, args.collection, args.auto, args.legs, rng,
        [x.strip() for x in args.series.split(",") if x.strip()] if args.series else None, args.within_hours,
        args.same_game, args.pregame_minutes,
        [tuple(int(x) for x in p.split(":")) for p in args.mix.split(",")] if args.mix else None,
        args.props, args.novig_edge, args.max_vig)

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
                time.sleep(rng.uniform(args.gap, max(args.gap, args.gap_max)) if args.gap_max else args.gap)
            row, raw = record_one(client, collection, legs, args)
            raw["row"] = row          # the CSV row too, so the .jsonl alone is a complete log
            w.writerow(row)
            jf.write(json.dumps(raw, default=str) + "\n")
            cf.flush()
            jf.flush()
            if raw.get("held"):
                h = raw["held"]
                print(f"PENDING {json.dumps(h)}", flush=True)
                print(f"\nwrote {csv_path}\n      {jsonl_path}")
                return 4
            print(f"[{i}/{len(todo)}] {row['legs']}\n    fair {row['fair'] or '-'}  quoters {row['n_quoters'] or 0}"
                  f"  YES {row['yes_cost'] or '-'} (EV {row['yes_ev_pct'] or '-'}%, vs others {row['yes_edge_vs_consensus_c'] or '-'}c)"
                  f"  NO {row['no_cost'] or '-'} (EV {row['no_ev_pct'] or '-'}%, vs others {row['no_edge_vs_consensus_c'] or '-'}c)"
                  + (f"\n    error: {row['error']}" if row["error"] else ""))
    print(f"\nwrote {csv_path}\n      {jsonl_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
