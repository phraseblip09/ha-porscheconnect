"""Lease mileage calculations (pure Python, no Home Assistant needed)."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "porscheconnect"))

from lease import add_months, calculate, parse_config  # noqa: E402

LEASE = {"start_date": "2026-01-01", "start_km": 10000, "annual_km": 20000, "term_months": 48}


def test_add_months_clamps_to_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 1, 1), 48) == date(2030, 1, 1)


def test_figures_match_hand_calculation():
    f = calculate(LEASE, 22000, date(2026, 7, 20))
    assert f.end_date == date(2030, 1, 1)
    assert f.days_elapsed == 200
    assert f.driven == 12000
    assert f.allowance_total == 80000
    assert f.remaining == 68000
    assert f.pace_per_year == 21900  # 12000 km in 200 days
    assert f.allowance_to_date == round(80000 * 200 / f.days_total)
    assert f.balance == 12000 - f.allowance_to_date
    assert f.forecast_excess == round(12000 / 200 * f.days_total) - 80000
    assert f.forecast_cost is None
    assert f.contract_year == 1


def test_cost_only_for_excess():
    over = calculate({**LEASE, "excess_price": 0.1}, 22000, date(2026, 7, 20))
    assert over.forecast_cost == round(over.forecast_excess * 0.1, 2)
    under = calculate({**LEASE, "excess_price": 0.1}, 15000, date(2026, 7, 20))
    assert under.forecast_excess < 0
    assert under.forecast_cost == 0


def test_before_start_and_after_end():
    before = calculate(LEASE, 10000, date(2025, 12, 1))
    assert before.days_elapsed == 0
    assert before.pace_per_year is None and before.forecast_excess is None
    after = calculate(LEASE, 100000, date(2031, 1, 1))
    assert after.days_elapsed == after.days_total
    assert after.contract_year == 4
    assert after.forecast_excess == after.driven - after.allowance_total


def test_missing_or_broken_config():
    assert calculate(None, 1000, date(2026, 1, 1)) is None
    assert calculate(LEASE, None, date(2026, 1, 1)) is None
    assert parse_config({"start_date": "kaputt", "start_km": 1, "annual_km": 1, "term_months": 1}) is None
    assert parse_config({**LEASE, "annual_km": 0}) is None
    # values from the HA number selector arrive as floats
    assert parse_config({**LEASE, "start_km": 10000.0, "term_months": 48.0})["term_months"] == 48
