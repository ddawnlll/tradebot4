"""Position sizing for a small linear USDT-M perpetual account.

The textbook ``risk / stop_distance`` formula ignores fees, slippage and
exchange minimums, all of which dominate a 20 USDT account. This module solves
for a quantity whose *total* loss at the stop (price move + taker fees + an
explicit slippage allowance) equals the risk budget, then subjects the result
to margin, leverage, precision and liquidation checks.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.core.models import InstrumentSpec, RejectReason, RiskDecision, Side


@dataclass(frozen=True, slots=True)
class SizingConfig:
    risk_fraction: float = 0.05
    max_leverage: float = 10.0
    slippage_bps_per_side: float = 2.0
    fee_reserve_mult: float = 1.0
    margin_buffer_frac: float = 0.20
    min_notional_buffer: float = 1.10
    maintenance_margin_rate: float = 0.005
    liquidation_buffer_frac: float = 0.005


def size_position(
    *,
    equity: float,
    available_margin: float,
    entry_price: float,
    stop_price: float,
    spec: InstrumentSpec,
    side: Side,
    config: SizingConfig | None = None,
    risk_fraction: float | None = None,
) -> RiskDecision:
    cfg = config or SizingConfig()
    rf = cfg.risk_fraction if risk_fraction is None else risk_fraction
    risk_usdt = max(0.0, equity * rf)

    stop_dist = abs(entry_price - stop_price)
    if stop_dist <= 0 or entry_price <= 0:
        return RiskDecision(False, RejectReason.RISK_POLICY.value, 0.0, 0.0, 0.0, 0.0)

    slip = entry_price * cfg.slippage_bps_per_side / 10_000.0
    fee_rate = spec.taker_fee * cfg.fee_reserve_mult
    # Loss per unit at the stop includes entry+exit fees and adverse slippage.
    loss_per_unit = stop_dist + fee_rate * (entry_price + stop_price) + 2 * slip
    raw_qty = risk_usdt / loss_per_unit
    qty = spec.round_qty(raw_qty, mode="down")

    if qty <= 0:
        return RiskDecision(False, RejectReason.MIN_QUANTITY.value, 0.0, risk_usdt, 0.0, 0.0)

    notional = spec.notional(qty, entry_price)
    min_notional = spec.min_notional * cfg.min_notional_buffer
    if notional < min_notional:
        return RiskDecision(
            False,
            RejectReason.MIN_NOTIONAL.value,
            qty,
            risk_usdt,
            notional / cfg.max_leverage,
            0.0,
            notes={"notional": notional, "required": min_notional},
        )

    leverage = notional / equity if equity > 0 else float("inf")
    if leverage > cfg.max_leverage:
        return RiskDecision(False, RejectReason.LEVERAGE.value, qty, risk_usdt, notional / cfg.max_leverage, leverage)

    margin_required = notional / cfg.max_leverage
    if margin_required > available_margin * (1.0 - cfg.margin_buffer_frac):
        return RiskDecision(
            False,
            RejectReason.MARGIN.value,
            qty,
            risk_usdt,
            margin_required,
            leverage,
            notes={"available": available_margin},
        )

    liq = liquidation_price(entry_price, leverage if leverage > 0 else cfg.max_leverage, side, cfg.maintenance_margin_rate)
    if not stop_before_liquidation(entry_price, stop_price, liq, side, cfg.liquidation_buffer_frac):
        return RiskDecision(
            False,
            RejectReason.LIQUIDATION_BUFFER.value,
            qty,
            risk_usdt,
            margin_required,
            leverage,
            liquidation_price=liq,
        )

    actual_risk = qty * loss_per_unit
    return RiskDecision(
        approved=True,
        reason=None,
        quantity=qty,
        risk_usdt=actual_risk,
        margin_required=margin_required,
        leverage=leverage,
        liquidation_price=liq,
        notes={"notional": notional, "loss_per_unit": loss_per_unit, "raw_qty": raw_qty},
    )


def liquidation_price(entry: float, leverage: float, side: Side, mmr: float) -> float:
    """Approximate isolated-margin liquidation price for a linear contract.

    Binance's exact formula includes the maintenance amount bracket, which is
    negligible at 20 USDT notional. The approximation is conservative because
    it ignores the maintenance amount deduction.
    """
    if leverage <= 0:
        leverage = 1.0
    factor = 1.0 / leverage - mmr
    if side is Side.LONG:
        return max(0.0, entry * (1.0 - factor))
    return entry * (1.0 + factor)


def stop_before_liquidation(entry: float, stop: float, liq: float, side: Side, buffer_frac: float) -> bool:
    if side is Side.LONG:
        # Long: liquidation must be strictly below the stop by the buffer.
        return liq < stop * (1.0 - buffer_frac)
    return liq > stop * (1.0 + buffer_frac)


def split_legs(qty: float, spec: InstrumentSpec, entry_price: float, n_legs: int = 2) -> list[float] | None:
    """Split a position into equal legs that each satisfy exchange minimums.

    Returns ``None`` when splitting is infeasible (the 20 USDT case for cheap
    symbols), in which case the caller must use a single take-profit.
    """
    if n_legs <= 1:
        return [qty]
    per = spec.round_qty(qty / n_legs, mode="down")
    if per <= 0:
        return None
    legs = [per] * (n_legs - 1)
    legs.append(spec.round_qty(qty - per * (n_legs - 1), mode="down"))
    for leg in legs:
        if leg + 1e-12 < spec.min_qty:
            return None
        if spec.notional(leg, entry_price) + 1e-9 < spec.min_notional:
            return None
    return legs
