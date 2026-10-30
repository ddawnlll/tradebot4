"""Tests for the static capital-viability diagnostic."""

from __future__ import annotations

import math

import pytest

from bot.risk.capital import (
    REASON_BELOW_MIN_NOTIONAL,
    REASON_BELOW_MIN_QTY,
    REASON_FEE_RESERVE,
    REASON_INVALID_INPUT,
    REASON_INVALID_STOP,
    REASON_MARGIN,
    REASON_STEP_ROUNDING,
    REASON_UNKNOWN_HISTORY,
    Candidate,
    CapitalFilters,
    ScenarioTier,
    evaluate_candidate,
    evaluate_tiers,
    summarize,
)

FILTERS = CapitalFilters(
    qty_step=0.1,
    min_qty=0.1,
    min_notional=5.0,
    maker_fee=0.0002,
    taker_fee=0.0005,
    max_leverage=10.0,
)


def candidate(**overrides: object) -> Candidate:
    base: dict[str, object] = {
        "symbol": "TESTUSDT",
        "side": "LONG",
        "entry_price": 100.0,
        "stop_price": 98.0,
        "quantity": 1.0,
        "equity": 500.0,
        "leverage": 10.0,
        "risk_fraction": 0.05,
        "filters": FILTERS,
    }
    base.update(overrides)
    return Candidate(**base)  # type: ignore[arg-type]


def assert_finite_or_none(value: float | None) -> None:
    assert value is None or math.isfinite(value)


def test_formula_matches_hand_computed_example() -> None:
    env = evaluate_candidate(candidate())

    # entry 100, stop 98 -> stop distance 2, risk fraction 0.05, step 0.1,
    # min notional 5, leverage 10.
    assert env.stop_distance == pytest.approx(2.0)
    assert env.e_step_usdt == pytest.approx(0.1 * 2.0 / 0.05)  # 4.0
    assert env.e_notional_usdt == pytest.approx(5.0 * 2.0 / (100.0 * 0.05))  # 2.0
    assert env.e_margin_usdt == pytest.approx(5.0 / 10.0)  # 0.5
    assert env.e_crit_usdt == pytest.approx(4.0)
    assert env.binding_constraint == "step"
    assert env.binding_constraint_v8 == "STEP_SIZE_DISCRETIZATION"
    assert env.rounded_quantity == pytest.approx(1.0)
    assert env.rounded_notional == pytest.approx(100.0)
    assert env.approved


def test_notional_is_binding_at_high_price() -> None:
    filters = CapitalFilters(
        qty_step=0.00001,
        min_qty=0.00001,
        min_notional=5.0,
        maker_fee=0.0002,
        taker_fee=0.0005,
        max_leverage=10.0,
    )
    env = evaluate_candidate(
        candidate(entry_price=50_000.0, stop_price=49_500.0, quantity=0.001, filters=filters)
    )
    assert env.e_step_usdt == pytest.approx(0.00001 * 500.0 / 0.05)
    assert env.e_notional_usdt == pytest.approx(5.0 * 500.0 / (50_000.0 * 0.05))
    assert env.e_margin_usdt == pytest.approx(0.5)
    assert env.binding_constraint == "notional"
    assert env.e_crit_usdt == pytest.approx(1.0)


def test_margin_is_binding_at_high_leverage_and_tight_stop() -> None:
    filters = CapitalFilters(
        qty_step=0.0001,
        min_qty=0.0001,
        min_notional=5.0,
        maker_fee=0.0002,
        taker_fee=0.0005,
        max_leverage=20.0,
    )
    env = evaluate_candidate(
        candidate(entry_price=100.0, stop_price=99.5, quantity=0.1, leverage=5.0, filters=filters)
    )
    assert env.e_step_usdt == pytest.approx(0.0001 * 0.5 / 0.05)
    assert env.e_notional_usdt == pytest.approx(5.0 * 0.5 / (100.0 * 0.05))
    assert env.e_margin_usdt == pytest.approx(5.0 / 5.0)
    assert env.e_crit_usdt == pytest.approx(1.0)
    assert env.binding_constraint == "margin"


def test_lot_rounding_reports_exact_quantity_and_notional() -> None:
    env = evaluate_candidate(candidate(quantity=0.37))
    assert env.rounded_quantity == pytest.approx(0.3)
    assert env.rounded_notional == pytest.approx(30.0)
    assert env.requested_quantity == pytest.approx(0.37)
    assert env.notes["qty_aligned"] is False


def test_quantity_below_one_step_is_step_rounding_and_min_qty() -> None:
    env = evaluate_candidate(candidate(quantity=0.05))
    assert env.rounded_quantity == 0.0
    assert env.rounded_notional == 0.0
    assert REASON_STEP_ROUNDING in env.reasons
    assert REASON_BELOW_MIN_QTY in env.reasons
    assert not env.approved


def test_step_rounding_can_breach_minimum() -> None:
    tight = CapitalFilters(
        qty_step=0.1,
        min_qty=0.15,
        min_notional=5.0,
        maker_fee=0.0002,
        taker_fee=0.0005,
        max_leverage=10.0,
    )
    env = evaluate_candidate(candidate(quantity=0.15, filters=tight))
    assert env.rounded_quantity == pytest.approx(0.1)
    assert REASON_STEP_ROUNDING in env.reasons
    assert REASON_BELOW_MIN_QTY in env.reasons
    assert not env.approved


def test_two_legs_check_each_leg_not_only_total() -> None:
    filters = CapitalFilters(
        qty_step=0.01,
        min_qty=0.01,
        min_notional=5.0,
        maker_fee=0.0002,
        taker_fee=0.0005,
        max_leverage=10.0,
    )
    one_leg = evaluate_candidate(candidate(quantity=0.08, filters=filters))
    assert one_leg.approved, one_leg.reasons

    two_legs = evaluate_candidate(candidate(quantity=0.08, filters=filters, legs=2))
    assert not two_legs.approved
    assert REASON_BELOW_MIN_NOTIONAL in two_legs.reasons
    assert [leg.quantity for leg in two_legs.legs] == pytest.approx([0.04, 0.04])
    assert all(leg.notional == pytest.approx(4.0) for leg in two_legs.legs)

    feasible = evaluate_candidate(candidate(quantity=0.2, filters=filters, legs=2))
    assert feasible.approved, feasible.reasons
    assert all(leg.notional == pytest.approx(10.0) for leg in feasible.legs)


def test_twenty_and_five_hundred_are_separate_scenarios() -> None:
    # 300 USDT notional at 10x needs 30 USDT margin: impossible at 20, fine at 500.
    envs = evaluate_tiers(candidate(entry_price=100.0, stop_price=99.0, quantity=3.0))
    by_label = {env.tier_label: env for env in envs}

    assert tuple(by_label) == ("equity_20_usdt", "equity_500_usdt")
    assert by_label["equity_20_usdt"].equity == 20.0
    assert by_label["equity_500_usdt"].equity == 500.0
    assert not by_label["equity_20_usdt"].approved
    assert REASON_MARGIN in by_label["equity_20_usdt"].reasons
    assert by_label["equity_500_usdt"].approved, by_label["equity_500_usdt"].reasons

    # Independent evaluation, not a scaled copy of the first result.
    assert by_label["equity_20_usdt"].margin_required_usdt == pytest.approx(30.0)
    assert by_label["equity_500_usdt"].margin_required_usdt == pytest.approx(30.0)
    assert by_label["equity_20_usdt"].effective_leverage == pytest.approx(15.0)


def test_custom_tiers_are_evaluated_independently() -> None:
    envs = evaluate_tiers(
        candidate(),
        tiers=(ScenarioTier("small", 10.0), ScenarioTier("large", 1000.0)),
    )
    assert [env.tier_label for env in envs] == ["small", "large"]
    assert [env.equity for env in envs] == [10.0, 1000.0]


def test_unknown_history_is_marked_never_invented() -> None:
    unknown = CapitalFilters()
    env = evaluate_candidate(candidate(filters=unknown))

    assert not env.approved
    assert REASON_UNKNOWN_HISTORY in env.reasons
    assert "qty_step" in env.unknown_fields
    assert "min_notional" in env.unknown_fields
    assert env.e_crit_usdt is None
    assert env.fee_reserve_usdt is None
    assert env.fee_reserve_status == "UNKNOWN"


def test_unknown_strings_in_mapping_stay_unknown() -> None:
    filters = CapitalFilters.from_mapping(
        {
            "qty_step": "UNKNOWN",
            "min_qty": "UNKNOWN",
            "min_notional": "UNKNOWN",
            "maker_fee": None,
            "taker_fee": "NA",
            "max_leverage": "UNKNOWN",
        }
    )
    assert filters.unknown_critical_fields == (
        "qty_step",
        "min_qty",
        "min_notional",
        "maker_fee",
        "taker_fee",
        "max_leverage",
    )
    env = evaluate_candidate(candidate(filters=filters))
    assert REASON_UNKNOWN_HISTORY in env.reasons
    assert not env.approved


def test_partially_unknown_filters_keep_known_formula_but_reject() -> None:
    partial = CapitalFilters(
        qty_step=0.1,
        min_qty=0.1,
        min_notional=5.0,
        maker_fee=None,
        taker_fee=None,
        max_leverage=10.0,
    )
    env = evaluate_candidate(candidate(filters=partial))
    assert env.e_crit_usdt == pytest.approx(4.0)
    assert env.binding_constraint == "step"
    assert REASON_UNKNOWN_HISTORY in env.reasons
    assert env.fee_reserve_status == "UNKNOWN"
    assert not env.approved


def test_fee_reserve_is_separate_from_e_crit() -> None:
    cheap = CapitalFilters(
        qty_step=0.1,
        min_qty=0.1,
        min_notional=5.0,
        maker_fee=0.0001,
        taker_fee=0.0002,
        max_leverage=10.0,
    )
    expensive = CapitalFilters(
        qty_step=0.1,
        min_qty=0.1,
        min_notional=5.0,
        maker_fee=0.0010,
        taker_fee=0.0020,
        max_leverage=10.0,
    )
    a = evaluate_candidate(candidate(filters=cheap, quantity=0.5))
    b = evaluate_candidate(candidate(filters=expensive, quantity=0.5))

    assert a.e_crit_usdt == pytest.approx(b.e_crit_usdt)
    assert b.fee_reserve_usdt > a.fee_reserve_usdt
    assert a.e_required_usdt == pytest.approx(a.e_crit_usdt + a.e_fee_reserve_usdt)
    assert a.fee_reserve_usdt == pytest.approx(50.0 * (0.0002 + 0.0002 + 0.0004))


def test_fee_reserve_rejection_is_explicit() -> None:
    env = evaluate_candidate(
        candidate(quantity=10.0, equity=100.0, risk_fraction=0.01, stop_price=99.0)
    )
    assert not env.approved
    assert REASON_FEE_RESERVE in env.reasons
    assert env.margin_required_usdt == pytest.approx(100.0)
    assert env.fee_reserve_usdt == pytest.approx(1000.0 * (0.0005 + 0.0005 + 0.0004))


def test_long_and_short_stop_distance_is_symmetric() -> None:
    long = evaluate_candidate(candidate(side="LONG", entry_price=100.0, stop_price=98.0, quantity=1.0))
    short = evaluate_candidate(candidate(side="SHORT", entry_price=100.0, stop_price=102.0, quantity=1.0))

    assert long.stop_distance == pytest.approx(short.stop_distance)
    assert long.e_step_usdt == pytest.approx(short.e_step_usdt)
    assert long.e_notional_usdt == pytest.approx(short.e_notional_usdt)
    assert long.e_crit_usdt == pytest.approx(short.e_crit_usdt)
    assert long.approved == short.approved


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"entry_price": 0.0}, REASON_INVALID_INPUT),
        ({"quantity": 0.0}, REASON_INVALID_INPUT),
        ({"equity": 0.0}, REASON_INVALID_INPUT),
        ({"leverage": 0.0}, REASON_INVALID_INPUT),
        ({"risk_fraction": 0.0}, REASON_INVALID_INPUT),
        ({"risk_fraction": float("nan")}, REASON_INVALID_INPUT),
        ({"entry_price": float("inf")}, REASON_INVALID_INPUT),
        ({"stop_price": None}, REASON_INVALID_INPUT),
        ({"stop_price": 100.0}, REASON_INVALID_STOP),
        ({"side": "LONG", "stop_price": 101.0}, REASON_INVALID_STOP),
        ({"side": "SHORT", "stop_price": 99.0}, REASON_INVALID_STOP),
    ],
)
def test_invalid_inputs_never_produce_nan_or_zero_division(
    overrides: dict[str, object], reason: str
) -> None:
    env = evaluate_candidate(candidate(**overrides))
    assert not env.approved
    assert reason in env.reasons
    for value in (
        env.e_step_usdt,
        env.e_notional_usdt,
        env.e_margin_usdt,
        env.e_crit_usdt,
        env.fee_reserve_usdt,
        env.e_fee_reserve_usdt,
        env.e_required_usdt,
        env.margin_required_usdt,
        env.effective_leverage,
        env.stop_distance,
        env.rounded_quantity,
        env.rounded_notional,
    ):
        assert_finite_or_none(value)


def test_summary_counts_reasons_bindings_and_unknowns() -> None:
    envs = evaluate_tiers(candidate(entry_price=100.0, stop_price=99.0, quantity=3.0))
    summary = summarize(envs)
    assert summary["candidates"] == 2
    assert summary["approved"] == 1
    assert summary["rejected"] == 1
    assert summary["binding_constraint_counts"] == {"step": 2}
    assert summary["reason_counts"][REASON_MARGIN] == 1
    assert summary["max_e_crit_usdt"] == pytest.approx(2.0)


def test_maintenance_margin_adds_to_required_margin() -> None:
    filters = CapitalFilters.from_mapping(
        {
            "qty_step": 0.1,
            "min_qty": 0.1,
            "min_notional": 5.0,
            "maker_fee": 0.0002,
            "taker_fee": 0.0005,
            "max_leverage": 10.0,
            "maintenance_margin_rate": 0.01,
        }
    )
    env = evaluate_candidate(candidate(quantity=1.0, filters=filters))
    assert env.margin_required_usdt == pytest.approx(100.0 / 10.0 + 100.0 * 0.01)
