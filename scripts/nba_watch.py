"""Watch today's NBA combos: send log-only RFQs slowly and irregularly, and
print any line worth a look (see rfq_report.alerts). Pacing, so makers see
neither bursts nor a constant stream:
  - one RFQ at a time, random gaps averaging --per-minute (default 1/min,
    gaps roughly 40-80 s apart start to start; hard cap 2/min),
  - work/rest: about --on-minutes of requests, then a --off-minutes pause
    (both randomised +-30%),
  - --night-cap: stop once this many RFQs are logged since --night-start.
Each batch has
  - random combos (2-4 legs, same game and different games, every prop type),
    --auto of each shape in MIX, and
  - "Novig edge" combos: different-game 2-3 leggers built only from player
    props whose Kalshi price is --edge-gap or more away from Novig's fair, on
    the side Kalshi underprices (--edge-auto of each shape in EDGE_MIX).
Player-prop legs are de-vigged on Novig's prices when listed there.

  python scripts/nba_watch.py --until 2026-11-03T03:00Z --max-minutes 60

Exits with status 3 as soon as a "fair" alert appears (best price --fair-pct
or more AND --fair-min-c cents or more better than fair value after fees), otherwise after --max-minutes or
at --until. Alerts are appended to logs/alerts_<date>.jsonl. Quotes are never
accepted.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import rfq_report  # noqa: E402

# Paced on purpose: makers see every RFQ, and a requester who fires many at
# once can get ignored or blocked. RFQs go out one at a time, --per-minute
# apart (start to start), including across batches.
MIX = "2:2,2:1,3:2,4:2"
EDGE_MIX = "2:1,3:2"   # x:1 all different games; x:2 mixes a same-game pair in


def ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--until", required=True, help="UTC time to stop, e.g. 2026-11-03T03:00Z")
    ap.add_argument("--every", type=float, default=0, help="minutes between batch starts (0 = back to back)")
    ap.add_argument("--per-minute", type=float, default=1.0, help="average RFQs per minute while active (max 2)")
    ap.add_argument("--on-minutes", type=float, default=20, help="active stretch before a pause")
    ap.add_argument("--off-minutes", type=float, default=15, help="pause length")
    ap.add_argument("--night-cap", type=int, default=250, help="max RFQs logged since --night-start")
    ap.add_argument("--night-start", default=None, help="UTC start of the night for --night-cap (default: 12 h before --until)")
    ap.add_argument("--wait", type=float, default=10, help="seconds to collect quotes per RFQ")
    ap.add_argument("--max-minutes", type=float, default=60, help="exit after this long (a summary point)")
    ap.add_argument("--mix", default=MIX, help="random combo shapes legs:same_game (x:1 = all different games)")
    ap.add_argument("--edge-mix", default=EDGE_MIX, help="Novig-edge combo shapes")
    ap.add_argument("--auto", type=int, default=1, help="random combos of each MIX shape per batch")
    ap.add_argument("--edge-auto", type=int, default=1, help="Novig-edge combos of each EDGE_MIX shape per batch")
    ap.add_argument("--edge-gap", type=float, default=0.02, help="min |Kalshi - Novig| leg fair gap")
    ap.add_argument("--pct", type=float, default=10.0, help="quoter alert: cheapest this %% under the others")
    ap.add_argument("--fair-pct", type=float, default=5.0, help="fair alert: best price this %% under fair, after fees")
    ap.add_argument("--fair-min-c", type=float, default=2.0, help="fair alert: and at least this many cents under fair")
    ap.add_argument("--min-c", type=float, default=2.0)
    args = ap.parse_args()
    if not 0 < args.per_minute <= 2:
        ap.error("--per-minute must be above 0 and at most 2")
    rng = random.Random()
    spacing = 60.0 / args.per_minute                 # average seconds from one RFQ start to the next
    mean_gap = max(5.0, spacing - args.wait - 2.0)   # ~2 s per RFQ for create/delete calls
    gap_lo, gap_hi = max(20.0, mean_gap * 0.6), mean_gap * 1.4
    gap_lo = min(gap_lo, gap_hi)
    night_start = (args.night_start and ts(args.night_start)) or ts(args.until) - 12 * 3600

    def logged_tonight() -> int:
        since_iso = datetime.fromtimestamp(night_start, timezone.utc).isoformat()
        n = 0
        for f in (ROOT / "logs").glob("combo_rfqs_*.jsonl"):
            n += sum(1 for line in f.open() if json.loads(line).get("ts", "") >= since_iso)
        return n

    def pause_for() -> float:
        return rng.uniform(gap_lo, gap_hi)

    start, until = time.time(), ts(args.until)
    logs = ROOT / "logs"
    found = []
    end = min(until, start + args.max_minutes * 60)
    active_until = time.time() + args.on_minutes * 60 * rng.uniform(0.7, 1.3)
    while time.time() < end:
        done_tonight = logged_tonight()
        if done_tonight >= args.night_cap:
            print(f"night cap reached ({done_tonight} requests); stopping", flush=True)
            break
        if time.time() >= active_until:
            rest = args.off_minutes * 60 * rng.uniform(0.7, 1.3)
            print(f"pausing {rest / 60:.0f} min", flush=True)
            time.sleep(max(0.0, min(rest, end - time.time())))
            active_until = time.time() + args.on_minutes * 60 * rng.uniform(0.7, 1.3)
            continue
        t0 = time.time()
        since = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        base = [sys.executable, str(ROOT / "scripts" / "kalshi_rfq_recorder.py"),
                "--collection", "KXMVESPORTSMULTIGAMEEXTENDED-R", "--series", "KXNBA*",
                "--within-hours", "16", "--pregame-minutes", "10", "--wait", str(args.wait), "--gap", f"{gap_lo:.0f}", "--gap-max", f"{gap_hi:.0f}",
                "--poll", "1"]
        runs = [("random", base + ["--mix", args.mix, "--auto", str(args.auto)])]
        # (a batch can overshoot the cap by its own size, at most ~6)
        if args.edge_auto:
            runs.append(("novig-edge", base + ["--mix", args.edge_mix, "--auto", str(args.edge_auto),
                                               "--novig-edge", str(args.edge_gap)]))
        n, stop = 0, False
        for k, (name, cmd) in enumerate(runs):
            if k:
                time.sleep(pause_for())   # same random spacing between the two runs
            r = subprocess.run(cmd, capture_output=True, text=True)
            n += sum(1 for l in r.stdout.splitlines() if l.startswith("["))
            if r.returncode:
                stamp = datetime.now(timezone.utc).strftime("%H:%M UTC")
                print(f"{stamp} {name} batch failed: {(r.stdout + r.stderr).strip().splitlines()[-1:]}", flush=True)
                if name == "random" and "not enough open games" in r.stdout + r.stderr:
                    stop = True
        stamp = datetime.now(timezone.utc).strftime("%H:%M UTC")
        if stop and not n:
            print("no games left before tip-off; stopping", flush=True)
            break
        new = []
        for f in sorted(logs.glob("combo_rfqs_*.jsonl")):
            for line in f.open():
                rec = json.loads(line)
                if rec.get("ts", "") >= since:
                    for a in rfq_report.alerts(rec, args.pct, args.min_c, fair_pct=args.fair_pct, fair_min_c=args.fair_min_c):
                        new.append({**a, "ts": rec["ts"], "legs": (rec.get("row") or {}).get("legs", ""),
                                    "group": rfq_report.group_label(rec["legs"])})
        with (logs / f"alerts_{datetime.now(timezone.utc):%Y%m%d}.jsonl").open("a") as af:
            for a in new:
                af.write(json.dumps(a) + "\n")
        found += new
        print(f"{stamp} {n} requests, {len(new)} alerts", flush=True)
        for a in new:
            print("  ALERT " + json.dumps(a), flush=True)
        if any(a["kind"] == "fair" for a in new):
            return 3
        pause = max(pause_for(), args.every * 60 - (time.time() - t0))
        time.sleep(max(0.0, min(pause, end - time.time())))
    print(f"done: {len(found)} alerts", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
