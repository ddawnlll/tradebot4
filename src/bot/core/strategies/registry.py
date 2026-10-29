"""Strategy registry."""

from __future__ import annotations

from bot.core.strategies.base import StrategyBase
from bot.core.strategies.random_entry import RandomEntryConfig, RandomEntryStrategy
from bot.core.strategies.range_v1 import RangeV1Config, RangeV1Strategy, RangeV2PassiveStrategy
from bot.core.strategies.trend_pullback import TrendPullbackConfig, TrendPullbackStrategy

REGISTRY: dict[str, type[StrategyBase]] = {
    "range_v1": RangeV1Strategy,
    "range_v2_passive": RangeV2PassiveStrategy,
    "trend_pullback": TrendPullbackStrategy,
    "random_entry": RandomEntryStrategy,
}

CONFIGS = {
    "range_v1": RangeV1Config,
    "range_v2_passive": RangeV1Config,
    "trend_pullback": TrendPullbackConfig,
    "random_entry": RandomEntryConfig,
}


def build_strategy(name: str, params: dict | None = None) -> StrategyBase:
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; known: {sorted(REGISTRY)}")
    cls = REGISTRY[name]
    cfg_cls = CONFIGS[name]
    kwargs = dict(params or {})
    if "tp_r_multiples" in kwargs and isinstance(kwargs["tp_r_multiples"], list):
        kwargs["tp_r_multiples"] = tuple(kwargs["tp_r_multiples"])
    return cls(cfg_cls(**kwargs))
