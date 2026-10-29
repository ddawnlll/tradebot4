# Range Lab dashboard

Streamlit + Plotly research UI over the run artifacts produced by the backtester.
The dashboard is read-only: it never writes to `runs/`, `data/` or the source tree.

## Run it

```bash
uv run streamlit run dashboard/app.py
```

Streamlit opens `http://localhost:8501`. Select a source, strategy version and run in
the sidebar. All pages use the same selection.

Requirements: `streamlit>=1.40`, `plotly>=5.24`, `polars>=1.20` (already in
`pyproject.toml`).

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `TRADEBOT_HOME` | repository root | Where `data/market/bars_1m/` and `runs/` live |
| `TRADEBOT_RUNS_DIR` | `$TRADEBOT_HOME/runs` | Run directory to discover. Relative paths resolve against `TRADEBOT_HOME` |

```bash
TRADEBOT_HOME=/mnt/research/tradebot \
TRADEBOT_RUNS_DIR=/mnt/research/tradebot/runs \
uv run streamlit run dashboard/app.py
```

Missing run directories, missing parquet files and runs from before the trade
exporter are handled with visible "no data" states. Nothing is fabricated.

## Pages

| Page | What it answers |
| --- | --- |
| Overview | Equity, drawdown, cumulative net R, return distribution, long versus short, run composition |
| Breakdowns | Net R by direction, symbol, regime, entry hour, exit reason and error tag; net R histogram and box |
| Trade Replay | One trade on 1m and 15m candles with entry/stop/TP/exit markers, recorded support/resistance, MAE/MFE, fills and tags |
| Calibration | Score bins versus mean net R, win rate, confidence bands, score-return scatter, stop-buffer sensitivity placeholder |
| Error Pareto | Tag frequency, total and average R loss, estimated improvement potential, Pareto curve, sortable table |
| Execution Quality | Expected versus first ENTRY fill price, slippage, maker/taker mix, fees, order rejections, latency placeholder |
| Missed Opportunities | Rejected signals by reason plus clearly labelled hypothetical +2R/-1R outcomes and price paths |

## Data assumptions

The loader tolerates missing columns and older exports, but these are the expected
schemas.

- `trades.parquet`: `trade_id`, `signal_id`, `symbol`, `side` (LONG/SHORT),
  `entry_price`, `exit_price`, `quantity`, `entry_ts_ms`, `exit_ts_ms`,
  `gross_pnl`, `fees`, `funding`, `slippage`, `net_pnl`, `net_r`, `risk_usdt`,
  `mae_r`, `mfe_r`, `tp1_filled`, `exit_reason`, `strategy_version`,
  `setup_score`, `bars_held`.
- `fills.parquet`: `ts_ms`, `symbol`, `client_order_id`, `kind`
  (ENTRY/STOP/TP1/TP2/EXIT), `side`, `price`, `qty`, `fee`, `liquidity`,
  `order_type`, `signal_id`.
- `orders.parquet`: `ts_ms`, `symbol`, `client_order_id`, `kind`, `order_type`,
  `price`, `qty`. Some exporters swap `kind` and `order_type`; the dashboard
  normalizes a known semantic kind from either column and has no `signal_id`
  here, so STOP/TP legs are matched by symbol plus a tight window around entry.
- `equity.parquet`: `ts_ms`, `equity`, `balance`, `used_margin`, `unrealized_pnl`.
- `signals.parquet`: `ts_ms`, `symbol`, `action` (ENTER/REJECT), `side`, `reason`,
  `reject_reason`, `score`, `regime`, `regime_confidence`, `support`,
  `resistance`, `features` (JSON string). The real exporter also includes
  `signal_id`; it is used for exact setup matching when present.
- Market bars: `data/market/bars_1m/symbol=<SYMBOL>/year=<YYYY>/month=<MM>/data.parquet`
  with `symbol`, `ts_open_ms`, `ts_close_ms`, `open`, `high`, `low`,
  `close`, `volume`, `quote_volume`, `trades`, `taker_buy_volume`,
  `taker_buy_quote_volume`.

## Method notes

- **Look-ahead safety.** Trade Replay calls `setup_for_trade`, which only accepts
  ENTER signals with `ts_ms <= entry_ts_ms`. The exact `signal_id` is preferred;
  the latest prior ENTER for the same symbol is a labelled fallback
  (`nearest_prior_signal`). Later signals are never used for zones.
- **Error tags.** If no `tags.parquet` / `trade_tags.parquet` exists, tags are
  derived from `exit_reason`, `mae_r`, `mfe_r`, negative `funding` and `fees`
  relative to `risk_usdt`, and `net_r`. Improvement potential is the clipped
  negative net R per tag; `gave_back_profit` uses `mfe_r - net_r`. These are
  estimates, not counterfactual backtests.
- **Calibration.** Bins are rank-based and equal count. Confidence bands are
  normal approximations (`mean +/- 1.96 * std / sqrt(n)`), which are indicative
  for small samples. The fitted slope is ordinary least squares.
- **Execution slippage.** Expected price is `trades.entry_price`; actual price is
  the first `ENTRY` fill for the matching `signal_id`. Positive bps means an
  adverse fill. Slippage in USDT is signed by side.
- **Hypothetical outcomes.** Rejected signals generally have no side. The
  sampler prefers high-scoring rejected signals that carry recorded support and
  resistance and sit in the outer 25 percent of that range; the simulator then
  infers LONG near support or SHORT near resistance, enters at the first bar open at or after the
  signal, places the stop beyond the recorded level plus 0.35 ATR (or one ATR
  when no level exists), and checks a fixed +2R target versus -1R stop with the
  stop winning ties inside a bar. Fees, funding, slippage and position limits are
  excluded. Results are explicitly labelled hypothetical.
- **Percent units.** `metrics.json` uses percent points for `return_pct` and
  fractions for `max_drawdown_pct`. The dashboard passes each through the correct
  formatter; equity transforms store fractions.
- **Stop-buffer sensitivity.** No sweep artifact exists in current runs. The
  page shows the configured `stop_buffer_atr` and a placeholder; it charts a
  `*sensitivity*.parquet` artifact or `metrics.json.stop_buffer_sensitivity`
  automatically when one appears.

## Structure

```
dashboard/
  app.py           # st.navigation shell
  data.py          # pure polars loading + transforms (no streamlit import)
  cache.py         # @st.cache_data wrappers
  common.py        # theme, sidebar, formatting, guards
  charts.py        # reusable Plotly builders
  pages/           # thin page scripts
tests/dashboard/
  test_data.py     # data-layer unit tests, no streamlit import
```

Run checks:

```bash
uv run pytest tests/dashboard -q
uv run ruff check dashboard tests/dashboard
```
