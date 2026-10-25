"""Summarize the combo RFQ logs written by kalshi_rfq_recorder.py.

  python scripts/rfq_report.py                 # all logs/combo_rfqs_*.jsonl
  python scripts/rfq_report.py --since 2026-10-25

Groups combos by how many legs share one game (1 = all different games),
then reports quoter counts, how far apart quoters are, and per quoter how
their price compares with the other quoters on the same RFQ. A quoter who is
consistently cheaper than the rest on same-game combos is the thing to look
for: that is a quoter pricing correlation differently.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oddscreen import combos  # noqa: E402


def bid(q: dict, name: str):
    v = q.get(f"{name}_dollars")
    v = float(v) if v not in (None, "") else 0.0
    return v or None              # "0.0000" = side not quoted


def costs(quotes: list[dict]) -> dict[str, dict[str, float]]:
    """{side: {creator: cost to buy that side}} from each quoter's latest quote."""
    latest: dict[str, dict] = {}
    for q in sorted(quotes, key=lambda q: q.get("updated_ts") or q.get("created_ts") or ""):
        latest[q.get("creator_id") or q.get("id")] = q
    out: dict[str, dict[str, float]] = {"yes": {}, "no": {}}
    for c, q in latest.items():
        nb, yb = bid(q, "no_bid"), bid(q, "yes_bid")
        if nb is not None:
            out["yes"][c] = 1.0 - nb
        if yb is not None:
            out["no"][c] = 1.0 - yb
    return out


def same_game_size(legs: list[dict]) -> int:
    keys = [combos.game_key(l["event_ticker"]) for l in legs]
    return max(keys.count(k) for k in keys)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="", help="only RFQs at or after this ISO time/date (UTC)")
    ap.add_argument("--logs", type=Path, default=ROOT / "logs")
    ap.add_argument("--min-rfqs", type=int, default=10, help="min RFQs for a quoter to be listed")
    args = ap.parse_args()

    recs = []
    for f in sorted(args.logs.glob("combo_rfqs_*.jsonl")):
        for line in f.open():
            r = json.loads(line)
            if r.get("ts", "") >= args.since:
                recs.append(r)
    if not recs:
        print("no RFQs logged")
        return 0

    groups: dict[int, list[dict]] = defaultdict(list)
    for r in recs:
        groups[same_game_size(r["legs"])].append(r)

    print(f"{len(recs)} RFQs, {recs[0]['ts'][:16]} to {recs[-1]['ts'][:16]} UTC")
    print(f"errors: {sum(1 for r in recs if r.get('error'))}, delete errors: {sum(1 for r in recs if r.get('delete_error'))}")
    for k in sorted(groups, reverse=True):
        rs = [r for r in groups[k] if r.get("quotes")]
        label = "all legs different games" if k == 1 else f"{k} legs from one game"
        print(f"\n== {label}: {len(groups[k])} RFQs, {len(rs)} with quotes")
        if not rs:
            continue
        nq, gap, edge = [], [], []
        per: dict[str, list[float]] = defaultdict(list)    # creator -> cents vs others' median
        for r in rs:
            cs = costs(r["quotes"])
            nq.append(len({q.get("creator_id") for q in r["quotes"]}))
            for side in ("yes", "no"):
                vals = cs[side]
                if len(vals) >= 2:
                    gap.append((max(vals.values()) - min(vals.values())) * 100)
                if len(vals) >= 3:
                    for c, v in vals.items():
                        others = [x for o, x in vals.items() if o != c]
                        per[c].append((v - st.median(others)) * 100)
                    best = min(vals, key=vals.get)
                    edge.append((st.median([x for o, x in vals.items() if o != best]) - vals[best]) * 100)
        print(f"  quoters per RFQ: median {st.median(nq)}, range {min(nq)}-{max(nq)}")
        if gap:
            print(f"  cheapest vs priciest quote on a side: median {st.median(gap):.1f}c, max {max(gap):.1f}c")
        if edge:
            print(f"  best quote is cheaper than the other quoters' median by: median {st.median(edge):.1f}c, max {max(edge):.1f}c")
        regulars = [(c, v) for c, v in per.items() if len(v) >= args.min_rfqs]
        if regulars:
            print(f"  quoters on >= {args.min_rfqs} sides (cents vs others' median; negative = cheaper):")
            for c, v in sorted(regulars, key=lambda t: st.mean(t[1]))[:12]:
                cheaper = sum(x < 0 for x in v)
                print(f"    {c[:8]}  sides {len(v):4d}  mean {st.mean(v):+6.2f}c  median {st.median(v):+6.2f}c  cheaper {cheaper}/{len(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
