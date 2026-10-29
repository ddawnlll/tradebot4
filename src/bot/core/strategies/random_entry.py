"""Benchmark: seeded random entries with the same risk envelope.

If a strategy cannot beat random entries with identical stops, take-profits,
costs and sizing, its signal has no economic value. The RNG is seeded per
(symbol, bar) so runs are reproducible and the entry set is independent of
strategy logic.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any

from bot.core.models import RejectReason, Side
from bot.core.strategies.base import (
    EntryContext,
    EntryDecision,
    EntryProposal,
    ExitContext,
    ExitSignal,
    StrategyBase,
)


@dataclass(frozen=True, slots=True)
class RandomEntryConfig:
    entry_probability: float = 0.02
    stop_atr: float = 1.5
    tp_r_multiples: tuple[float, ...] = (2.0, 3.0)
    seed: int = 7


class RandomEntryStrategy(StrategyBase):
    name = "random_entry"
    version = "1.0.0"

    def __init__(self, config: RandomEntryConfig | None = None) -> None:
        self.config = config or RandomEntryConfig()

    def config_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, **self.config.__dict__}

    def evaluate_entry(self, ctx: EntryContext) -> EntryDecision:
        atr = ctx.state.atr
        if atr is None or atr <= 0:
            return EntryDecision(None, RejectReason.MISSING_CONTEXT, "no atr")
        digest = hashlib.sha256(f"{self.config.seed}:{ctx.state.symbol}:{ctx.ts_ms}".encode()).digest()
        rng = random.Random(int.from_bytes(digest[:8], "big"))
        if rng.random() > self.config.entry_probability:
            return EntryDecision(None, RejectReason.NOT_AT_EDGE, "random skip")
        side = Side.LONG if rng.random() < 0.5 else Side.SHORT
        price = ctx.bar.close
        stop = price - self.config.stop_atr * atr if side is Side.LONG else price + self.config.stop_atr * atr
        risk = abs(price - stop)
        tps = tuple(
            price + risk * m if side is Side.LONG else price - risk * m for m in self.config.tp_r_multiples
        )
        return EntryDecision(
            EntryProposal(
                side=side,
                entry_price=price,
                stop_price=stop,
                take_profits=tps,
                reason="random_entry",
            )
        )

    def evaluate_exit(self, ctx: ExitContext) -> ExitSignal | None:
        return None
