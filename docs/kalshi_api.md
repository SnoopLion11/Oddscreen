# Kalshi API notes (combo RFQ recorder)

docs.kalshi.com could not be fetched from the build environment, so these
were confirmed from docs.kalshi.com pages via search excerpts (October 2026).
Items under "Open" are assumptions in `oddscreen/kalshi.py` to check on the
first real run (the raw responses land in `logs/combo_rfqs_<date>.jsonl`).

## Confirmed

- **Hosts.** Production `https://external-api.kalshi.com/trade-api/v2`
  (`api.elections.kalshi.com` still supported). Demo
  `https://external-api.demo.kalshi.co/trade-api/v2`. Demo keys only work on
  demo, production keys only on production.
- **Signing.** Headers `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP` (ms),
  `KALSHI-ACCESS-SIGNATURE` (base64). Signed string = timestamp + METHOD +
  full path from root (`/trade-api/v2/...`), query string removed. RSA keys:
  RSA-PSS, SHA-256, MGF1-SHA-256, salt = digest length. Ed25519 (Kalshi's
  default key type) signs the same string directly.
- **Rate limits.** Token buckets per second; most requests cost 10 tokens.
  Basic tier: 200 read / 100 write tokens per second, i.e. about 20 reads and
  10 writes per second. 429s carry no Retry-After; back off exponentially.
  `GET /account/limits` shows your own.
- **Order book.** `GET /markets/{ticker}/orderbook` returns
  `orderbook_fp.yes_dollars` / `no_dollars`: BIDS only, ascending, each
  `[price_dollars, count_fp]` strings. YES ask = 1 - best NO bid.
- **Markets.** Prices are `yes_bid_dollars`, `yes_ask_dollars`,
  `no_bid_dollars`, `no_ask_dollars` (cent fields being removed). Combo
  markets carry `mve_selected_legs` (event_ticker, market_ticker, side).
- **Combo collections.** `GET /multivariate_event_collections` (filters
  `status`, `associated_event_ticker`, `series_ticker`) and
  `GET /multivariate_event_collections/{collection_ticker}`. A collection has
  `associated_events` (per event: `is_yes_only`, max markets), `size_min`,
  `size_max`, `is_ordered`.
- **Creating a combo market.** `POST /multivariate_event_collections/{collection_ticker}`
  with `{"selected_markets": [{"market_ticker", "event_ticker", "side"}]}`
  returns `market_ticker`, `event_ticker`. Must be called before trading or
  quoting a combo. **Limit: 5000 creations per week.** `write::trade` scope.
  The old `PUT .../lookup` is deprecated/removed.
- **RFQs.** `POST /communications/rfqs`, required `market_ticker` and
  `rest_remainder`; size via `contracts` (or `contracts_fp`, or
  `target_cost_dollars`). Max 100 open RFQs. `GET /communications/rfqs`,
  `GET /communications/rfqs/{rfq_id}`, `DELETE /communications/rfqs/{rfq_id}`.
  RFQ `status` is `open` or `closed`.
- **Quotes.** `GET /communications/quotes` (filters `rfq_id`, `min_ts`,
  `max_ts`, `limit`, `cursor`). Quote fields include `rfq_id`, `creator_id`,
  `rfq_creator_id`, `yes_bid_dollars`, `no_bid_dollars`. Quotes are for the
  full RFQ size and private between requester and each quoter.
- **Execution (not used here).** Requester accepts
  (`PUT /communications/rfqs/{rfq_id}/quotes/{quote_id}/accept`, `accepted_side`
  yes/no), then the **quoter must confirm**: effectively a last look. Combo
  markets are high-volatility markets with about 1 s confirm and 1 s execution
  windows.
- **Side mapping.** Kalshi's FIX docs: BUY accepts the maker's NO quote. So the
  requester buys YES at 1 - `no_bid` and NO at 1 - `yes_bid`.
- **General taker fee.** roundup(0.07 × M × C × P × (1 − P)) per trade, M a
  per-series multiplier (default 1).

## Checked on live runs (24 Oct 2026)

- **Collections list key** is `multivariate_contracts`. Three open
  collections; the sports one is `KXMVESPORTSMULTIGAMEEXTENDED-R` with about
  3,100 `associated_events`. Collection `size_max` 0 means no limit,
  `size_min` 2. Per event `size_max` is 1 (one market per event in a combo).
  `active_quoters` was an empty list on every event, so it is not used.
- **Fetching leg markets.** Use `GET /markets?series_ticker=...&status=open`
  (`--series`), not one request per event. A game market's `close_time` is
  days after the game; `expected_expiration_time` is near the game, so
  `--within-hours` filters on that.
- **Combo market tickers** come back under a different series than the
  collection, e.g. `KXMVECROSSCATEGORY0-S2026...`.
- **Create RFQ response** is just `{"id": "<uuid>"}`.
- **Quotes need a user filter.** `GET /communications/quotes?rfq_id=...` alone
  returns 403 ("Either creator_user_id or rfq_creator_user_id must be
  filled"). Passing our RFQ's `creator_id` as `rfq_creator_user_id` also
  403s ("cannot request the quotes of another user"): that field wants an
  internal user UUID. The working filter is `rfq_user_filter=self` (from
  Kalshi's generated Python SDK, `kalshi_python_sync` 3.34).
- **Quote fields:** `id`, `rfq_id`, `creator_id`, `rfq_creator_id`,
  `rfq_creator_user_id`, `market_ticker`, `contracts_fp`, `yes_bid_dollars`,
  `no_bid_dollars`, `yes_contracts_fp`, `no_contracts_fp`, `status`,
  `created_ts`, `updated_ts`.
- **One-sided quotes are common** (98 of 144): the unquoted side is
  `"0.0000"` and has no `*_contracts_fp`. The recorder treats a 0 bid as "not
  quoted"; otherwise it would read as a $1.00 price.
- **`creator_id` is stable per quoter**: across 6 RFQs, 7 quoters appeared in
  all 6 with the same id. Quoters occasionally replace a quote (new `id`, same
  `creator_id`); the recorder keeps the latest.
- **Response speed:** first quotes arrive about 0.13 s after the RFQ; 18-30
  quoters per NFL 2-leg combo within 10 s.
- **Game key.** Event tickers are `SERIES-GAMEID`, but some props add a team
  segment (`KXNFLTEAMFIRSTTD-26OCT25PITNO-PIT`), so the game is the second
  segment only.
- **Delete** worked every time; RFQs show `status: closed` afterwards.

## Still open

1. **Combo/RFQ fees.** No source says how RFQ fills on combos are charged.
   The recorder applies the general taker fee (`--fee-mult 1`) and also logs
   pre-fee EV. Check Kalshi's fee schedule PDF and the series' fee fields.
2. **RFQ etiquette.** No documented rule on RFQs that are never accepted.
   Defaults are one RFQ every 15 s, deleted after the wait (8 s default).
