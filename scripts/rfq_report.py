"""Summarize the combo RFQ logs written by kalshi_rfq_recorder.py.

  python scripts/rfq_report.py                 # all logs/combo_rfqs_*.jsonl
  python scripts/rfq_report.py --since 2026-10-25
  python scripts/rfq_report.py --export out/   # rfqs.csv + quoters.csv for the results page

Groups combos by how many legs share one game (1 = all different games),
then reports quoter counts, how far apart quoters are, and per quoter how
their price compares with the other quoters on the same RFQ. A quoter who is
consistently cheaper than the rest on same-game combos is the thing to look
for: that is a quoter pricing correlation differently.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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


def group_label(legs: list[dict]) -> str:
    k = same_game_size(legs)
    if k == 1:
        return f"{len(legs)} legs, different games"
    if k == len(legs):
        return f"{len(legs)} legs, same game"
    return f"{len(legs)} legs, {k} from one game"


def sport(legs: list[dict]) -> str:
    t = legs[0]["event_ticker"]
    for name in ("NBA", "NFL", "NHL", "MLB", "NCAAF", "NCAAB"):
        if t.startswith("KX" + name):
            return name
    return "other"


def num(row: dict, k: str):
    v = row.get(k, "")
    return "" if v in ("", None) else float(v)


def export(recs: list[dict], out: Path) -> None:
    """rfqs.csv (one row per RFQ) and quoters.csv (per group and quoter)."""
    out.mkdir(parents=True, exist_ok=True)
    et = ZoneInfo("America/New_York")
    rfq_rows, per = [], defaultdict(list)
    for r in recs:
        row, group = r.get("row") or {}, group_label(r["legs"])
        cs = costs(r.get("quotes") or [])
        gaps, edges = [], []
        for side in ("yes", "no"):
            vals = cs[side]
            if len(vals) >= 2:
                gaps.append((max(vals.values()) - min(vals.values())) * 100)
            if len(vals) >= 3:
                for c, v in vals.items():
                    per[(group, c)].append((v - st.median([x for o, x in vals.items() if o != c])) * 100)
                best = min(vals, key=vals.get)
                edges.append((st.median([x for o, x in vals.items() if o != best]) - vals[best]) * 100)
        cm, nf = num(row, "consensus_mid"), num(row, "naive_fair")
        rfq_rows.append({
            "ts": r["ts"],
            "night": datetime.fromisoformat(r["ts"]).astimezone(et).strftime("%Y-%m-%d"),
            "sport": sport(r["legs"]), "group": group, "n_legs": len(r["legs"]),
            "legs": row.get("legs", ""), "quoted": int(bool(r.get("quotes"))), "error": int(bool(r.get("error"))),
            "n_quoters": len({q.get("creator_id") for q in r.get("quotes") or []}),
            "price_gap_c": round(max(gaps), 2) if gaps else "",
            "best_vs_others_c": round(max(edges), 2) if edges else "",
            "fair": num(row, "fair"), "naive_fair": nf, "consensus_mid": cm,
            "quoted_vs_naive": round(cm / nf, 4) if cm and nf else "",
            "yes_ev_pct": num(row, "yes_ev_pct"), "no_ev_pct": num(row, "no_ev_pct"),
        })
    with (out / "rfqs.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rfq_rows[0]))
        w.writeheader()
        w.writerows(rfq_rows)
    with (out / "quoters.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["key", "group", "quoter", "sides", "mean_c", "median_c", "cheaper_share"])
        w.writeheader()
        for (group, c), v in sorted(per.items(), key=lambda t: (t[0][0], st.mean(t[1]))):
            w.writerow({"key": f"{group}|{c[:8]}", "group": group, "quoter": c[:8], "sides": len(v),
                        "mean_c": round(st.mean(v), 3), "median_c": round(st.median(v), 3),
                        "cheaper_share": round(sum(x < 0 for x in v) / len(v), 4)})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="", help="only RFQs at or after this ISO time/date (UTC)")
    ap.add_argument("--logs", type=Path, default=ROOT / "logs")
    ap.add_argument("--min-rfqs", type=int, default=10, help="min RFQs for a quoter to be listed")
    ap.add_argument("--export", type=Path, help="also write rfqs.csv and quoters.csv to this folder")
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
    if args.export:
        export(recs, args.export)

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
