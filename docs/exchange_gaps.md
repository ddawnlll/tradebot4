# Binance USDT-M constraints relevant to a 20 USDT account

Metadata snapshot: 2026-10-29 (`GET /fapi/v1/exchangeInfo`), 659 USDT-M
instruments, 522 perpetuals in `TRADING` state.

## Minimum notional (the binding constraint)

| `MIN_NOTIONAL` | Symbols |
|---|---|
| 5 USDT | 516 perpetuals |
| 20 USDT | ETH, BCH, LTC, ETC, LINK |
| 50 USDT | BTC |

With 20 USDT equity and 10x leverage the maximum order notional is 200 USDT.
A 5% risk budget is 1 USDT. For a 5 USDT minimum notional the stop must be at
most ~20% away before fees; for BTC's 50 USDT minimum the stop must be at most
~2% away. The engine rejects orders below `1.10 x MIN_NOTIONAL` and records
`MIN_NOTIONAL` in the journal. This is why BTC is rarely tradable in this
account size.

## Fees

VIP0 USDT-M futures: maker 0.020%, taker 0.050%. No BNB discount is assumed.
Backtests use these values from the venue configuration, and sizing reserves
the round-trip taker fee plus 2 bps of slippage per side.

## Funding

Funding is charged every 4 or 8 hours depending on the symbol. The
`funding_interval_hours` column from `data.binance.vision` is used, and the
rate is applied at its publication timestamp. Positive funding debits longs and
credits shorts. Backtest funding uses the 1m close at settlement as the price
proxy instead of the mark price.

## Order types and protection

* Supported: `LIMIT`, `MARKET`, `STOP`, `STOP_MARKET`, `TAKE_PROFIT`,
  `TAKE_PROFIT_MARKET`, `TRAILING_STOP_MARKET`; time in force includes `GTX`
  (post-only) and `GTD`.
* There is **no native OCO** for futures. A live adapter must cancel the
  sibling order when one bracket leg fills. The backtest bridge implements the
  same behaviour, so the logic is exercised rather than assumed.
* Native stops should use `MARK_PRICE` triggers in live trading. The backtest
  has no mark-price series in the catalog and therefore triggers on `LAST`.
  This is a real backtest/live divergence and must be tested on testnet before
  GO.
* One-way mode (`NETTING`) is assumed. Two take-profit legs are partial closes
  of one net position, not hedge positions.

## Leverage brackets

`/fapi/v1/leverageBracket` requires a signed request. At 200 USDT maximum
notional every symbol is inside tier 1, so the tier-1 maximum leverage is the
binding limit. This assumption must be re-checked if capital grows.

## API operations (for the future live adapter)

* REST + WebSocket user data stream; listen-key renewal.
* Weight-based rate limits; order rate limits are independent.
* `X-MBX-TIME-IN-EAST` / server time offset handling.
* Client order IDs are limited to 36 characters.
* Withdrawals must be disabled; futures trading only; IP allowlist; secrets in
  the environment, never in git.

## Data availability

`data.binance.vision` publishes monthly and daily ZIPs with `.CHECKSUM` files
for klines, mark-price klines and funding rates. Delisted symbols remain
available, which is required for a survivorship-free universe. Some symbols
have gaps (SOLUSDT is missing 4.18% of minutes in the downloaded window); the
aggregator marks affected 15m bars incomplete and the strategy refuses to trade
on incomplete context.
