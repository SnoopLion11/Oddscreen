# Oddscreen

Local real-time odds screen comparing ESX against Kalshi, Polymarket US and
NoVig. Display only. Full spec: [ESX_BRIEF.md](ESX_BRIEF.md).

## Status

| Piece | State |
|---|---|
| De-vig / edge engine (`oddscreen/devig.py`) | Done. Passes every `verify_devig.py` check. |
| ESX connector | Docs read (`docs/esx_api.md`). Waiting on: WebSocket real vs paper, market/outcome fields, fees. `scripts/esx_probe.py` answers all but fees. |
| Kalshi connector | REST client and signing done (`oddscreen/kalshi.py`, notes in `docs/kalshi_api.md`). WebSocket streams not started. |
| Combo RFQ recorder (`scripts/kalshi_rfq_recorder.py`) | Done, log-only. Not yet run against the live API. |
| Matcher, screen, Poly US, NoVig, guards, CSV log | Not started. |

## Setup

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in keys; .env is git-ignored
```

## ESX probe (read-only)

```
python scripts/esx_probe.py                         # first open market found
python scripts/esx_probe.py --sport football --league NFL --seconds 60
python scripts/esx_probe.py --market <marketIdStr>
```

Uses only `ESX_API_KEY`, only the market-data GETs and the market-data
WebSocket (anything else is refused in code). Prints the market/outcome fields,
which outcome ids the WebSocket uses, and whether the WebSocket book matches the
real-money or the paper order book. Raw responses go to `probe_output/`
(git-ignored, market data only).

## Kalshi combo RFQ recorder (log-only)

Sends RFQs on Kalshi combos, records every quote, deletes the RFQ. Never
accepts a quote or places an order (refused in code, see `oddscreen/kalshi.py`).

```
python scripts/kalshi_rfq_recorder.py --list-collections
python scripts/kalshi_rfq_recorder.py --collection <TICKER> --auto 20 --legs 2
python scripts/kalshi_rfq_recorder.py --combos combos.json      # your own combos
python scripts/kalshi_rfq_recorder.py --demo ...                # demo environment
```

Each row in `logs/combo_rfqs_<date>.csv`:

- **fair**: product of the legs' de-vigged Kalshi fairs. Cross-game combos
  only; blank for same-game combos, whose legs are correlated.
- **yes/no_cost**: the best quote to buy each side (1 − the quoter's opposite bid).
- **yes/no_ev_pct**: EV % against fair after the assumed taker fee
  (`_prefee` without it).
- **yes/no_edge_vs_consensus_c**: how many cents the best quote beats the
  median of the other quoters.
- **mid_spread_c**: how far apart the quoters are.

Raw books and quotes go to the matching `.jsonl`. Every combo market the recorder creates counts
toward Kalshi's 5000/week limit. Open questions (combo fees, a few response
field names) are listed in `docs/kalshi_api.md`.

## Verifying the math

```
python -m oddscreen.selftest   # runs verify_devig.py's own checks against production code
python -m pytest -q            # self-test + agreement with the reference + speed
```

`oddscreen/selftest.py` loads `verify_devig.py`, strips its reference function
definitions and injects the production ones under the same names, so the
file's checks run unmodified against the code the screen uses. The server will
refuse to show prices unless this returns zero failures.
