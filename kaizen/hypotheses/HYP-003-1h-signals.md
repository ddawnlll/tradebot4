# HYP-003: 1h entry signals with 1m execution

Status: proposed (round 1)

## Evidence

The baseline evaluates 15m bars inside a 1h context and a 4h higher-timeframe
range. Many 15m signals are noise: the range score, ADX and choppiness filters
fire on intrabar wiggles, and the time stop (32 x 15m = 8h) forces exits before
funding events can matter. A 1h entry cadence halves the number of decisions,
turns the 1h context into the entry timeframe and leaves the 4h range intact.

## Change (one change)

Set the entry timeframe to 1h. The context timeframe becomes 4h and the higher
timeframe stays 4h (the range is the 4h range). Execution remains 1m, stops
and targets unchanged in price terms.

## Preregistered criteria

Same as HYP-001. Holding time and funding paid are reported per trade; a rise
in PF that comes only from paying less funding is not a signal edge and is
reported separately.

## Configurations (6 max)

| id | entry tf | time stop bars |
|----|----------|----------------|
| h3a | 1h | 32 (32h) |
| h3b | 1h | 8 (8h) |

## Risks

1h bars inside a 4h range give few setups; the 100-trade floor may fail. The
time stop in bars changes wall-clock holding time, so h3a and h3b separate
"fewer, larger decisions" from "same holding time".
