# Oddscreen

Local real-time odds screen comparing ESX against Kalshi, Polymarket US and
NoVig. Display only. Full spec: [ESX_BRIEF.md](ESX_BRIEF.md).

## Status

| Piece | State |
|---|---|
| De-vig / edge engine (`oddscreen/devig.py`) | Done. Passes every `verify_devig.py` check. |
| ESX connector | Docs read (`docs/esx_api.md`). Waiting on: WebSocket real vs paper, market/outcome fields, fees. `scripts/esx_probe.py` answers all but fees. |
| Kalshi connector | Waiting on docs.kalshi.com being reachable to confirm auth/streams. |
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

## Verifying the math

```
python -m oddscreen.selftest   # runs verify_devig.py's own checks against production code
python -m pytest -q            # self-test + agreement with the reference + speed
```

`oddscreen/selftest.py` loads `verify_devig.py`, strips its reference function
definitions and injects the production ones under the same names, so the
file's checks run unmodified against the code the screen uses. The server will
refuse to show prices unless this returns zero failures.
