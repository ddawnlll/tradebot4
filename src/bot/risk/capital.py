"""Static capital-viability diagnostic for small USDT-M perpetual accounts.

This module ports the v8-core ``usdm_sim::capital_viability`` formula. It answers
one narrow question: *at what account equity does a candidate order envelope
become representable at the exchange?* It does not size positions, does not
place orders, and makes no claim about profitability.

Structural minimum equity (per candidate stop distance)
-------------------------------------------------------
Three independent floors are computed; the largest is ``E_crit`` and its name
is reported as the binding constraint::

    E_step     = qty_step      * stop_distance / risk_fraction
    E_notional = min_notional  * stop_distance / (entry_price * risk_fraction)
    E_margin   = min_notional  / leverage
    E_crit     = max(E_step, E_notional, E_margin)

Fees and slippage are deliberately *not* folded into ``E_crit``. They are
reported as a separate reserve for the evaluated notional::

    fee_reserve = notional * (entry_fee_rate + exit_fee_rate)
                + notional * 2 * slippage_bps_per_side / 10_000
    E_fee_reserve = fee_reserve / risk_fraction
    E_required    = E_crit + E_fee_reserve

Both tiers (20 USDT and 500 USDT) are evaluated as independent scenarios with
their own equity. The 500 USDT tier is *not* a scaled copy of the 20 USDT tier.

Unknown history
---------------
Historical exchange filters may be missing. Every optional filter is ``None``
when unknown; this module never invents a default and never silently substitutes
the current live filter. Missing critical fields produce an ``unknown_history``
rejection and are listed in ``unknown_fields``. ``maintenance_margin_rate`` is
optional by design and is only used when supplied.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Literal

from bot.core.models import InstrumentSpec

UNKNOWN = "UNKNOWN"

#: Fee/slippage reserve is evaluated for one entry and one exit.
DEFAULT_SLIPPAGE_BPS_PER_SIDE = 2.0

BindingConstraint = Literal["step", "notional", "margin"]

#: Rejection reasons emitted by :func:`evaluate_candidate`.
REASON_BELOW_MIN_QTY = "below_min_qty"
REASON_BELOW_MIN_NOTIONAL = "below_min_notional"
REASON_MARGIN = "margin"
REASON_LEVERAGE = "leverage"
REASON_FEE_RESERVE = "fee_reserve"
REASON_STEP_ROUNDING = "step_rounding"
REASON_UNKNOWN_HISTORY = "unknown_history"
REASON_INVALID_INPUT = "invalid_input"
REASON_INVALID_STOP = "invalid_stop"

_BINDING_REASON: dict[BindingConstraint, str] = {
    "step": REASON_STEP_ROUNDING,
    "notional": REASON_BELOW_MIN_NOTIONAL,
    "margin": REASON_MARGIN,
}

#: Exact constraint labels used by the upstream v8-core implementation.
V8_BINDING_NAMES: dict[BindingConstraint, str] = {
    "step": "STEP_SIZE_DISCRETIZATION",
    "notional": "MIN_NOTIONAL_LIMIT",
    "margin": "MARGIN_COLLATERAL_LIMIT",
}


def _as_float(value: object) -> float | None:
    """Return a finite float, or ``None`` for UNKNOWN/missing/non-finite input."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.upper() in {UNKNOWN, "NONE", "NAN", "NA", "NULL"}:
            return None
        try:
            out = float(text)
        except ValueError:
            return None
    elif isinstance(value, int | float):
        out = float(value)
    else:
        return None
    return out if math.isfinite(out) else None


def _floor_to_step(value: float, step: float | None) -> float:
    """Floor ``value`` to a positive ``step`` without binary-float noise."""
    if step is None or step <= 0 or not math.isfinite(step):
        return value
    d_value = Decimal(str(value))
    d_step = Decimal(str(step))
    units = (d_value / d_step).to_integral_value(rounding=ROUND_FLOOR)
    return float(units * d_step)


def _is_aligned(value: float, step: float) -> bool:
    if step <= 0:
        return True
    units = value / step
    return abs(units - round(units)) <= 1e-9 * max(1.0, abs(units))


@dataclass(frozen=True, slots=True)
class CapitalFilters:
    """Exchange filters used by the diagnostic. ``None`` means UNKNOWN.

    A missing value is never replaced by a guessed default. Critical fields
    (quantity step, minimum quantity, minimum notional, fees, maximum leverage)
    must be known before a candidate can be approved.
    """

    qty_step: float | None = None
    min_qty: float | None = None
    min_notional: float | None = None
    maker_fee: float | None = None
    taker_fee: float | None = None
    max_leverage: float | None = None
    maintenance_margin_rate: float | None = None
    price_tick: float | None = None

    @classmethod
    def from_instrument_spec(
        cls,
        spec: InstrumentSpec,
        *,
        maintenance_margin_rate: float | None = None,
    ) -> CapitalFilters:
        return cls(
            qty_step=spec.qty_step,
            min_qty=spec.min_qty,
            min_notional=spec.min_notional,
            maker_fee=spec.maker_fee,
            taker_fee=spec.taker_fee,
            max_leverage=spec.max_leverage,
            maintenance_margin_rate=maintenance_margin_rate,
            price_tick=spec.price_tick,
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> CapitalFilters:
        """Build filters from a JSON/parquet row; UNKNOWN and None stay unknown."""
        data = payload or {}
        return cls(
            qty_step=_as_float(data.get("qty_step", data.get("step_size"))),
            min_qty=_as_float(data.get("min_qty")),
            min_notional=_as_float(data.get("min_notional")),
            maker_fee=_as_float(data.get("maker_fee")),
            taker_fee=_as_float(data.get("taker_fee")),
            max_leverage=_as_float(data.get("max_leverage")),
            maintenance_margin_rate=_as_float(data.get("maintenance_margin_rate")),
            price_tick=_as_float(data.get("price_tick")),
        )

    @property
    def unknown_critical_fields(self) -> tuple[str, ...]:
        names = ("qty_step", "min_qty", "min_notional", "maker_fee", "taker_fee", "max_leverage")
        return tuple(name for name in names if _as_float(getattr(self, name)) is None)


@dataclass(frozen=True, slots=True)
class ScenarioTier:
    """One independent capital scenario. Tiers are never scaled into each other."""

    label: str
    equity: float


#: Required separate tiers. Do not collapse these into one scaled run.
SCENARIO_TIERS: tuple[ScenarioTier, ...] = (
    ScenarioTier(label="equity_20_usdt", equity=20.0),
    ScenarioTier(label="equity_500_usdt", equity=500.0),
)


@dataclass(frozen=True, slots=True)
class Candidate:
    """A single order envelope to test for capital viability."""

    symbol: str
    side: str
    entry_price: float
    stop_price: float
    quantity: float
    equity: float
    leverage: float
    risk_fraction: float
    filters: CapitalFilters
    legs: int = 1
    entry_is_maker: bool = False
    slippage_bps_per_side: float = DEFAULT_SLIPPAGE_BPS_PER_SIDE
    stop_distance_source: str = "stop_price"

    def stop_distance(self) -> float:
        entry = _as_float(self.entry_price)
        stop = _as_float(self.stop_price)
        if entry is None or stop is None:
            return 0.0
        return abs(entry - stop)


@dataclass(frozen=True, slots=True)
class LegResult:
    index: int
    quantity: float
    notional: float
    reasons: tuple[str, ...]

    @property
    def approved(self) -> bool:
        return not self.reasons


@dataclass(frozen=True, slots=True)
class Envelope:
    """Outcome of evaluating one candidate at one tier."""

    symbol: str
    side: str
    tier_label: str
    equity: float
    approved: bool
    reasons: tuple[str, ...]
    binding_constraint: BindingConstraint | None
    binding_constraint_v8: str | None
    stop_distance: float
    requested_quantity: float
    rounded_quantity: float
    rounded_notional: float
    e_step_usdt: float | None
    e_notional_usdt: float | None
    e_margin_usdt: float | None
    e_crit_usdt: float | None
    fee_reserve_usdt: float | None
    e_fee_reserve_usdt: float | None
    e_required_usdt: float | None
    margin_required_usdt: float | None
    effective_leverage: float | None
    unknown_fields: tuple[str, ...]
    fee_reserve_status: str
    legs: tuple[LegResult, ...] = field(default_factory=tuple)
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "tier_label": self.tier_label,
            "equity": self.equity,
            "approved": self.approved,
            "reasons": list(self.reasons),
            "binding_constraint": self.binding_constraint,
            "binding_constraint_v8": self.binding_constraint_v8,
            "stop_distance": self.stop_distance,
            "requested_quantity": self.requested_quantity,
            "rounded_quantity": self.rounded_quantity,
            "rounded_notional": self.rounded_notional,
            "e_step_usdt": self.e_step_usdt,
            "e_notional_usdt": self.e_notional_usdt,
            "e_margin_usdt": self.e_margin_usdt,
            "e_crit_usdt": self.e_crit_usdt,
            "fee_reserve_usdt": self.fee_reserve_usdt,
            "e_fee_reserve_usdt": self.e_fee_reserve_usdt,
            "e_required_usdt": self.e_required_usdt,
            "margin_required_usdt": self.margin_required_usdt,
            "effective_leverage": self.effective_leverage,
            "unknown_fields": list(self.unknown_fields),
            "fee_reserve_status": self.fee_reserve_status,
            "legs": [
                {"index": leg.index, "quantity": leg.quantity, "notional": leg.notional, "reasons": list(leg.reasons)}
                for leg in self.legs
            ],
            "notes": dict(self.notes),
        }


def _invalid_envelope(candidate: Candidate, tier_label: str, reason: str, detail: str) -> Envelope:
    equity = _as_float(candidate.equity)
    quantity = _as_float(candidate.quantity)
    return Envelope(
        symbol=candidate.symbol,
        side=str(candidate.side),
        tier_label=tier_label,
        equity=0.0 if equity is None else equity,
        approved=False,
        reasons=(reason,),
        binding_constraint=None,
        binding_constraint_v8=None,
        stop_distance=candidate.stop_distance(),
        requested_quantity=0.0 if quantity is None else quantity,
        rounded_quantity=0.0,
        rounded_notional=0.0,
        e_step_usdt=None,
        e_notional_usdt=None,
        e_margin_usdt=None,
        e_crit_usdt=None,
        fee_reserve_usdt=None,
        e_fee_reserve_usdt=None,
        e_required_usdt=None,
        margin_required_usdt=None,
        effective_leverage=None,
        unknown_fields=(),
        fee_reserve_status=UNKNOWN,
        notes={"invalid": detail},
    )


def _split_legs(quantity: float, n_legs: int, qty_step: float | None) -> list[float]:
    if n_legs <= 1:
        return [quantity]
    per = _floor_to_step(quantity / n_legs, qty_step)
    if per <= 0:
        return [0.0] * n_legs
    legs = [per] * (n_legs - 1)
    remainder = _floor_to_step(quantity - per * (n_legs - 1), qty_step)
    legs.append(remainder)
    return legs


def evaluate_candidate(candidate: Candidate, *, tier_label: str = "custom") -> Envelope:
    """Evaluate one candidate envelope against exchange filters and capital.

    ``tier_label`` is descriptive only. The account size comes from
    ``candidate.equity``; two tiers are evaluated by calling this function twice
    with independent equity values (see :func:`evaluate_tiers`).
    """
    f = candidate.filters

    entry = _as_float(candidate.entry_price)
    stop = _as_float(candidate.stop_price)
    qty = _as_float(candidate.quantity)
    equity = _as_float(candidate.equity)
    leverage = _as_float(candidate.leverage)
    risk_fraction = _as_float(candidate.risk_fraction)

    if (
        entry is None
        or stop is None
        or qty is None
        or equity is None
        or leverage is None
        or risk_fraction is None
        or entry <= 0
        or qty <= 0
        or equity <= 0
        or leverage <= 0
        or not (0 < risk_fraction <= 1)
    ):
        return _invalid_envelope(candidate, tier_label, REASON_INVALID_INPUT, "non_finite_or_non_positive")

    side = str(candidate.side).upper()
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return _invalid_envelope(candidate, tier_label, REASON_INVALID_STOP, "stop_distance_zero")
    if not math.isfinite(stop_distance):
        return _invalid_envelope(candidate, tier_label, REASON_INVALID_INPUT, "non_finite_stop_distance")
    if side == "LONG" and stop > entry:
        return _invalid_envelope(candidate, tier_label, REASON_INVALID_STOP, "long_stop_above_entry")
    if side == "SHORT" and stop < entry:
        return _invalid_envelope(candidate, tier_label, REASON_INVALID_STOP, "short_stop_below_entry")

    qty_step = _as_float(f.qty_step)
    min_qty = _as_float(f.min_qty)
    min_notional = _as_float(f.min_notional)
    maker_fee = _as_float(f.maker_fee)
    taker_fee = _as_float(f.taker_fee)
    max_leverage = _as_float(f.max_leverage)
    mmr = _as_float(f.maintenance_margin_rate)

    unknown_fields = f.unknown_critical_fields
    reasons: list[str] = []
    if unknown_fields:
        reasons.append(REASON_UNKNOWN_HISTORY)

    # --- structural minimum equity (v8-core formula, reserve excluded) -------
    # v8-core clamps these before dividing (risk_fraction.max(1e-6), lev.max(1.0)).
    f_risk = max(risk_fraction, 1e-6)
    lev = max(leverage, 1.0)
    e_step = qty_step * stop_distance / f_risk if qty_step is not None else None
    e_notional = min_notional * stop_distance / (entry * f_risk) if min_notional is not None else None
    e_margin = min_notional / lev if min_notional is not None else None

    binding: BindingConstraint | None = None
    e_crit: float | None = None
    for name, value in (("step", e_step), ("notional", e_notional), ("margin", e_margin)):
        if value is None:
            continue
        if e_crit is None or value > e_crit:
            e_crit = value
            binding = name  # type: ignore[assignment]

    # --- quantity rounding and per-leg minimums -----------------------------
    rounded_qty = _floor_to_step(qty, qty_step)
    rounded_notional = rounded_qty * entry
    qty_aligned = qty_step is None or _is_aligned(qty, qty_step)

    if rounded_qty <= 0:
        # A positive candidate smaller than one step cannot be represented.
        reasons.append(REASON_STEP_ROUNDING)
        reasons.append(REASON_BELOW_MIN_QTY)
    elif min_qty is not None and rounded_qty < min_qty:
        if qty >= min_qty and not qty_aligned:
            reasons.append(REASON_STEP_ROUNDING)
        reasons.append(REASON_BELOW_MIN_QTY)

    if min_notional is not None and rounded_notional < min_notional:
        reasons.append(REASON_BELOW_MIN_NOTIONAL)

    n_legs = max(1, int(candidate.legs)) if isinstance(candidate.legs, int) else 1
    leg_qtys = _split_legs(rounded_qty, n_legs, qty_step)
    leg_results: list[LegResult] = []
    for index, leg_qty in enumerate(leg_qtys):
        leg_notional = leg_qty * entry
        leg_reasons: list[str] = []
        if qty_step is not None and leg_qty <= 0:
            leg_reasons.append(REASON_STEP_ROUNDING)
        if min_qty is not None and leg_qty < min_qty:
            leg_reasons.append(REASON_BELOW_MIN_QTY)
        if min_notional is not None and leg_notional < min_notional:
            leg_reasons.append(REASON_BELOW_MIN_NOTIONAL)
        leg_results.append(LegResult(index, leg_qty, leg_notional, tuple(leg_reasons)))
        for reason in leg_reasons:
            if reason not in reasons:
                reasons.append(reason)

    # --- leverage and margin ------------------------------------------------
    effective_leverage = rounded_notional / equity
    if max_leverage is not None and leverage > max_leverage:
        reasons.append(REASON_LEVERAGE)
    if max_leverage is not None and effective_leverage > max_leverage:
        reasons.append(REASON_LEVERAGE)

    margin_required = rounded_notional / leverage
    if mmr is not None:
        # Maintenance margin is a cash requirement in addition to initial margin.
        margin_required += rounded_notional * mmr
    if margin_required > equity:
        reasons.append(REASON_MARGIN)

    # --- explicit fee/slippage reserve (never folded into E_crit) -----------
    fee_reserve_status = "KNOWN"
    fee_reserve: float | None
    if maker_fee is None or taker_fee is None:
        fee_reserve = None
        fee_reserve_status = UNKNOWN
    else:
        entry_fee_rate = maker_fee if candidate.entry_is_maker else taker_fee
        slippage_bps = _as_float(candidate.slippage_bps_per_side)
        slippage_rate = max(0.0, DEFAULT_SLIPPAGE_BPS_PER_SIDE if slippage_bps is None else slippage_bps) / 10_000.0
        fee_reserve = max(0.0, rounded_notional * (entry_fee_rate + taker_fee + 2.0 * slippage_rate))
        if fee_reserve > equity * risk_fraction:
            reasons.append(REASON_FEE_RESERVE)

    e_fee_reserve = fee_reserve / risk_fraction if fee_reserve is not None else None
    e_required = e_crit + e_fee_reserve if (e_crit is not None and e_fee_reserve is not None) else None

    # --- equity below the structural floor ----------------------------------
    if e_crit is not None and equity < e_crit and binding is not None:
        mapped = _BINDING_REASON[binding]
        if mapped not in reasons:
            reasons.append(mapped)

    # Preserve deterministic order while dropping duplicates.
    deduped = tuple(dict.fromkeys(reasons))
    return Envelope(
        symbol=candidate.symbol,
        side=side,
        tier_label=tier_label,
        equity=equity,
        approved=not deduped,
        reasons=deduped,
        binding_constraint=binding,
        binding_constraint_v8=V8_BINDING_NAMES[binding] if binding is not None else None,
        stop_distance=stop_distance,
        requested_quantity=qty,
        rounded_quantity=rounded_qty,
        rounded_notional=rounded_notional,
        e_step_usdt=e_step,
        e_notional_usdt=e_notional,
        e_margin_usdt=e_margin,
        e_crit_usdt=e_crit,
        fee_reserve_usdt=fee_reserve,
        e_fee_reserve_usdt=e_fee_reserve,
        e_required_usdt=e_required,
        margin_required_usdt=margin_required,
        effective_leverage=effective_leverage,
        unknown_fields=unknown_fields,
        fee_reserve_status=fee_reserve_status,
        legs=tuple(leg_results),
        notes={
            "qty_aligned": qty_aligned,
            "legs": n_legs,
            "entry_is_maker": candidate.entry_is_maker,
            "stop_distance_source": candidate.stop_distance_source,
        },
    )


def evaluate_tiers(
    candidate: Candidate,
    tiers: tuple[ScenarioTier, ...] = SCENARIO_TIERS,
) -> tuple[Envelope, ...]:
    """Evaluate the same envelope independently at each capital tier.

    Each tier gets its own equity and its own result. No tier is derived by
    scaling another tier's outcome.
    """
    return tuple(
        evaluate_candidate(replace(candidate, equity=tier.equity), tier_label=tier.label) for tier in tiers
    )


def summarize(envelopes: list[Envelope] | tuple[Envelope, ...]) -> dict[str, Any]:
    """Aggregate accept/reject counts, reasons and binding constraints."""
    totals = {
        "candidates": 0,
        "approved": 0,
        "rejected": 0,
        "reason_counts": {},
        "binding_constraint_counts": {},
        "binding_constraint_v8_counts": {},
        "unknown_field_counts": {},
        "max_e_crit_usdt": None,
        "max_e_required_usdt": None,
        "fee_reserve_unknown": 0,
    }
    for env in envelopes:
        totals["candidates"] += 1
        if env.approved:
            totals["approved"] += 1
        else:
            totals["rejected"] += 1
        for reason in env.reasons:
            totals["reason_counts"][reason] = totals["reason_counts"].get(reason, 0) + 1
        if env.binding_constraint is not None:
            counts = totals["binding_constraint_counts"]
            counts[env.binding_constraint] = counts.get(env.binding_constraint, 0) + 1
        if env.binding_constraint_v8 is not None:
            counts = totals["binding_constraint_v8_counts"]
            counts[env.binding_constraint_v8] = counts.get(env.binding_constraint_v8, 0) + 1
        for name in env.unknown_fields:
            counts = totals["unknown_field_counts"]
            counts[name] = counts.get(name, 0) + 1
        if env.e_crit_usdt is not None:
            current = totals["max_e_crit_usdt"]
            totals["max_e_crit_usdt"] = env.e_crit_usdt if current is None else max(current, env.e_crit_usdt)
        if env.e_required_usdt is not None:
            current = totals["max_e_required_usdt"]
            totals["max_e_required_usdt"] = (
                env.e_required_usdt if current is None else max(current, env.e_required_usdt)
            )
        if env.fee_reserve_status == UNKNOWN:
            totals["fee_reserve_unknown"] += 1

    for key in (
        "reason_counts",
        "binding_constraint_counts",
        "binding_constraint_v8_counts",
        "unknown_field_counts",
    ):
        totals[key] = dict(sorted(totals[key].items()))
    return totals
