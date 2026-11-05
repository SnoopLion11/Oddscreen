"""Watch today's NBA combos: every 15 minutes, send a batch of log-only RFQs
and print any line worth a look (see rfq_report.alerts). Each batch has
  - random combos (2-4 legs, same game and different games, every prop type),
    --auto of each shape in MIX, and
  - "Novig edge" combos: different-game 2-3 leggers built only from player
    props whose Kalshi price is --edge-gap or more away from Novig's fair, on
    the side Kalshi underprices (--edge-auto of each shape in EDGE_MIX).
Player-prop legs are de-vigged on Novig's prices when listed there.

  python scripts/nba_watch.py --until 2026-11-03T03:00Z --max-minutes 60

Exits with status 3 as soon as a "fair" alert appears (best price --fair-pct
or more better than fair value after fees), otherwise after --max-minutes or
at --until. Alerts are appended to logs/alerts_<date>.jsonl. Quotes are never
accepted.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import rfq_report  # noqa: E402

MIX = "2:2,2:1,3:3,3:1,4:4,4:1"
EDGE_MIX = "2:1,3:1"


def ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--until", required=True, help="UTC time to stop, e.g. 2026-11-03T03:00Z")
    ap.add_argument("--every", type=float, default=15, help="minutes between batches")
    ap.add_argument("--max-minutes", type=float, default=60, help="exit after this long (a summary point)")
    ap.add_argument("--auto", type=int, default=2, help="random combos of each MIX shape per batch")
    ap.add_argument("--edge-auto", type=int, default=6, help="Novig-edge combos of each EDGE_MIX shape per batch")
    ap.add_argument("--edge-gap", type=float, default=0.02, help="min |Kalshi - Novig| leg fair gap")
    ap.add_argument("--pct", type=float, default=10.0, help="quoter alert: cheapest this %% under the others")
    ap.add_argument("--fair-pct", type=float, default=5.0, help="fair alert: best price this %% under fair, after fees")
    ap.add_argument("--min-c", type=float, default=2.0)
    args = ap.parse_args()

    start, until = time.time(), ts(args.until)
    logs = ROOT / "logs"
    found = []
    end = min(until, start + args.max_minutes * 60)
    while time.time() < end:
        t0 = time.time()
        since = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        base = [sys.executable, str(ROOT / "scripts" / "kalshi_rfq_recorder.py"),
                "--collection", "KXMVESPORTSMULTIGAMEEXTENDED-R", "--series", "KXNBA*",
                "--within-hours", "16", "--pregame-minutes", "10", "--wait", "10", "--gap", "3"]
        runs = [("random", base + ["--mix", MIX, "--auto", str(args.auto)])]
        if args.edge_auto:
            runs.append(("novig-edge", base + ["--mix", EDGE_MIX, "--auto", str(args.edge_auto),
                                               "--novig-edge", str(args.edge_gap)]))
        n, stop = 0, False
        for name, cmd in runs:
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
                    for a in rfq_report.alerts(rec, args.pct, args.min_c, fair_pct=args.fair_pct):
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
        time.sleep(max(0.0, min(args.every * 60 - (time.time() - t0), end - time.time())))
    print(f"done: {len(found)} alerts", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
