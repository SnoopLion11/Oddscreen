#!/usr/bin/env bash
# One night of log-only NBA combo RFQs (about 60 RFQs, ~25 minutes).
# Start ~35 minutes before the first tip-off; games starting within 5 minutes are skipped.
set -u
cd "$(dirname "$0")/.."
COMMON=(--collection KXMVESPORTSMULTIGAMEEXTENDED-R
        --series KXNBAGAME,KXNBASPREAD,KXNBATOTAL,KXNBAPTS
        --within-hours 8 --pregame-minutes 5 --legs 3 --wait 10 --gap 8)
python scripts/kalshi_rfq_recorder.py "${COMMON[@]}" --same-game 3 --auto 30
python scripts/kalshi_rfq_recorder.py "${COMMON[@]}" --same-game 2 --auto 15
python scripts/kalshi_rfq_recorder.py "${COMMON[@]}" --same-game 1 --auto 15
