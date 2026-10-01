# ESX Live Odds Screen — Build Brief

## Goal
Build a local, real-time odds screen that compares ESX prices against Kalshi, Polymarket US, and NoVig so I can see instantly when an ESX line is off fair value. Display only: no alerts, no notifications, no automated trading.

The screen must update continuously and in real time, like a live odds screen: every price change on any platform appears on screen the moment it arrives, with no refresh button, no page reloads, and no update interval. It stays live for as long as it is open.

Ask me questions before starting if anything about the APIs, fees, or requirements is unclear.

## Platforms and data
- ESX is the target. Kalshi, Polymarket US, and NoVig are the references.
- API docs:
  - ESX: API Trading page https://www.esxtrade.com/api-trading (FAQ: https://www.esxtrade.com/helps/faqs). The site is JavaScript-rendered, so the full API docs are pasted below / in the project folder: [paste ESX API docs here]. IMPORTANT: ESX has two modes, paper trading and real-money trading, and its FAQ labels the API "API Trading (Paper)". The screen must use REAL-MONEY market prices. Before building anything else, confirm from the docs whether the API exposes real-money market data (e.g. a separate live environment or base URL vs a paper one). Paper mode is fine for testing the connector, but if the API is paper-only for market data, stop and tell me before continuing.
  - Kalshi: https://docs.kalshi.com/welcome (websockets: https://docs.kalshi.com/websockets)
  - Polymarket US: https://docs.polymarket.us/api-reference/introduction (this is the US, CFTC-regulated platform; do NOT use the international Polymarket API docs)
  - NoVig: https://docs.novig.com/ (websockets: https://docs.novig.com/api-reference/WSS/overview, fees: https://docs.novig.com/fees)
- Read each platform's docs first and confirm its streaming support, authentication, rate limits, and fees before writing connectors.
- Market data only. Read-only API keys where keys are required.
- Focus on live, in-game sports markets: moneylines, spreads (including run lines and puck lines), and totals.
- Prices must be live executable prices from the order book: the ask (price to buy) for EACH side of a market, on every platform. Never use last-traded prices or midpoints.
- Before building on ESX, confirm from its docs and terms that my account and API access are permitted, and confirm ESX's fee structure (flat per contract vs % of winnings).

## Speed requirements (top priority)
- Updates must be as close to instantaneous as possible. Target: under 100 ms from a platform message to the change showing on screen.
- Use websocket streams from every platform that offers them. For any platform that only has a REST API, poll at the fastest rate its rate limits allow, and tell me what that rate is.
- Process each incoming message immediately and recalculate only the affected market. No batching or timed refresh loops beyond syncing to the browser's frame rate.
- Push updates from the local engine to the browser over a websocket. No page refreshes or polling.
- Market matching runs once when a market first appears and is cached. Never re-match on a price update.
- Keep the latest prices for every market in memory.
- All logging (including the edge CSV) runs asynchronously, off the path from feed message to screen.
- Send only changed rows to the browser, and redraw only the changed cells.
- Auto-reconnect any dropped feed immediately.
- Stale detection is based on connection health (heartbeats/pings), not on whether a price changed. A quiet market on a healthy connection is not stale. If a feed's connection goes silent past a configurable number of seconds, mark it stale: gray it out and exclude it from fair value.
- Show a latency readout per feed measuring the full path (message received to cell updated).

## Market matching
- Map the same game, market type, side, and line across all four platforms.
- Never compare markets with different lines (e.g. -2.5 vs -3, Over 44.5 vs Over 45).
- Run lines and puck lines are classified as spreads.
- Include a manual override file for pairs the matcher gets wrong, and a way to see unmatched markets.

## Fair value and edge math
Use the attached verify_devig.py as the reference implementation and test suite (it needs `pip install shin`). All of its tests must pass against the production devig code before anything is shown on screen.

1. Convert every price to implied probability for that side.
   - Cents: price / 100
   - American negative (-A): A / (A + 100)
   - American positive (+B): 100 / (B + 100)
2. De-vig each reference platform SEPARATELY, using only that platform's own prices for both sides. Never combine prices from different platforms before de-vigging.
3. For each platform, run four de-vig methods (p = side's implied probability, S = sum of all sides, n = number of sides):
   - Multiplicative: p / S
   - Additive: p - (S - 1) / n
   - Power: solve k so the sum of p^k over all sides = 1; fair = p^k
   - Shin: solve z so fair probabilities sum to 1; fair = (sqrt(z^2 + 4(1-z) p^2 / S) - z) / (2(1-z))
   - Solve power and Shin with bisection or Newton's method with a fixed iteration cap high enough to converge (per-update cost must stay in microseconds).
   - Discard any method result <= 0.
4. Worst case per platform: the platform's fair probability for a side is the LOWEST remaining result of the four methods for that side.
5. Fair value across platforms:
   - Default (Average): average of each live platform's worst-case fair.
   - Strict toggle: the lowest platform's worst-case fair.
   - Weights configurable later.
6. Cost at ESX = ESX ask + ESX fee (adjust if ESX charges a % of winnings instead of a flat fee).
7. Edge:
   - Cents edge = (fair - ESX ask) x 100 - fee in cents
   - EV % = (fair / cost - 1) x 100

## Safety guards
- Disagreement guard: if live reference platforms' fair values differ by more than X cents (configurable, default 3), flag the row "platforms disagree" and do not highlight it.
- Minimum platforms: configurable minimum number of live reference platforms required for a row to highlight (default 2). Rows below the minimum are shown dimmed.
- Liquidity guard: ignore a platform's price for a market if the size available at its best price is below a configurable dollar amount.

## Screen (browser page served locally, updates continuously)
Dark, dense, terminal-style layout.

Header:
- One pill per feed (ESX, Kalshi, Poly US, NoVig) showing live latency in ms, or "no heartbeat Xs" when stale.

Control bar 1 (filters):
- Sport filter: All, NFL, MLB, NHL (and others as available).
- Markets filter: Moneyline, Spread, Total. Each toggles on/off independently.
- Odds format: American / Cents. Display only; it never changes any calculation.
- Sort: Biggest edge / By game.

Control bar 2 (edge):
- Edge unit: Cents / EV %.
- Fair value mode: Average / Strict.
- Separate edge threshold per market type (Moneyline, Spread, Total), stored separately for each edge unit. Defaults: Cents ML 3 / Spread 2 / Total 2; EV % ML 5 / Spread 4 / Total 4.
- Count of rows currently over threshold.

Table:
- One row per side of a market.
- Columns: Game (with live game clock), Market/Side, ESX, Kalshi, Poly US, NoVig, Fair, Edge.
- Cells flash briefly when a price changes.
- ESX cell and row highlight when edge >= that row's market-type threshold.
- Edge shows in orange when ESX is overpriced by more than the threshold.
- Stale cells gray and italic.
- "Platforms disagree" flag shown on the row when the disagreement guard trips.
- Hover on a row shows each platform's de-vigged worst-case fair and which method set it, plus the combined fair value.

## Logging
Log every row that crosses its threshold to a CSV (asynchronously): timestamp, game, market, side, line, ESX ask, each platform's prices for both sides, each platform's worst-case fair and method, combined fair, cents edge, EV %, and which guards were active. Later I want to compare these against closing prices to check whether the edges were real.

## Build order
- Phase 1: ESX + Kalshi connectors and the matcher. Prove it correctly matches one live game, and report the actual latency and update method (streaming vs polling) of each feed.
- Phase 2: The de-vig/edge engine, passing all verify_devig.py tests, plus the screen using those two feeds.
- Phase 3: Add Polymarket US and NoVig, plus the safety guards.
- Phase 4: CSV logging, run in log-only mode before any real-money use.
