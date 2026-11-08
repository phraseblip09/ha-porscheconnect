"""Lease mileage tracking: pure calculations, no Home Assistant imports.

A lease is configured per vehicle (VIN) in the integration options:

    {"start_date": "2026-01-01", "start_km": 10000, "annual_km": 20000,
     "term_months": 36, "excess_price": 0.15}

All figures are derived from the odometer reading and today's date, so nothing has to be
stored between polls and nothing gets lost when Home Assistant restarts.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

CONF_LEASE = "lease"
CONF_START_DATE = "start_date"
CONF_START_KM = "start_km"
CONF_ANNUAL_KM = "annual_km"
CONF_TERM_MONTHS = "term_months"
CONF_EXCESS_PRICE = "excess_price"

STORE_KEY = "porscheconnect_lease"
STORE_VERSION = 1


def add_months(start: date, months: int) -> date:
    """Return the same day ``months`` later (clamped to the end of shorter months)."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@dataclass(frozen=True)
class LeaseFigures:
    """Everything the lease sensors show, in kilometres (cost in the currency of excess_price)."""

    end_date: date
    days_total: int
    days_elapsed: int
    contract_year: int
    driven: int
    allowance_total: int
    allowance_to_date: int
    balance: int  # driven minus pro-rata allowance: > 0 means over the budget
    remaining: int  # total allowance minus driven
    pace_per_year: int | None  # driven km extrapolated to one year
    forecast_excess: int | None  # extrapolated km at the end of the term minus total allowance
    forecast_cost: float | None  # forecast_excess * excess_price (never negative)


def parse_config(raw: dict | None) -> dict | None:
    """Validate a stored lease config; return a clean copy or None if unusable."""
    if not raw:
        return None
    try:
        cfg = {
            CONF_START_DATE: date.fromisoformat(str(raw[CONF_START_DATE])),
            CONF_START_KM: int(float(raw[CONF_START_KM])),
            CONF_ANNUAL_KM: int(float(raw[CONF_ANNUAL_KM])),
            CONF_TERM_MONTHS: int(float(raw[CONF_TERM_MONTHS])),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if cfg[CONF_ANNUAL_KM] <= 0 or cfg[CONF_TERM_MONTHS] <= 0 or cfg[CONF_START_KM] < 0:
        return None
    price = raw.get(CONF_EXCESS_PRICE)
    try:
        cfg[CONF_EXCESS_PRICE] = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        cfg[CONF_EXCESS_PRICE] = None
    return cfg


def calculate(raw_config: dict | None, mileage: int | float | None, today: date) -> LeaseFigures | None:
    """Return the lease figures, or None if the lease or the odometer reading is missing."""
    cfg = parse_config(raw_config)
    if cfg is None or mileage is None:
        return None

    start: date = cfg[CONF_START_DATE]
    end = add_months(start, cfg[CONF_TERM_MONTHS])
    days_total = (end - start).days
    days_elapsed = min(max((today - start).days, 0), days_total)

    annual = cfg[CONF_ANNUAL_KM]
    allowance_total = round(annual * cfg[CONF_TERM_MONTHS] / 12)
    driven = round(mileage) - cfg[CONF_START_KM]
    allowance_to_date = round(allowance_total * days_elapsed / days_total)

    if days_elapsed > 0:
        pace_per_year = round(driven / days_elapsed * 365)
        forecast_excess = round(driven / days_elapsed * days_total) - allowance_total
    else:
        pace_per_year = None
        forecast_excess = None

    price = cfg[CONF_EXCESS_PRICE]
    forecast_cost = round(max(forecast_excess, 0) * price, 2) if price is not None and forecast_excess is not None else None

    return LeaseFigures(
        end_date=end,
        days_total=days_total,
        days_elapsed=days_elapsed,
        contract_year=min(days_elapsed // 365 + 1, max(1, -(-cfg[CONF_TERM_MONTHS] // 12))),
        driven=driven,
        allowance_total=allowance_total,
        allowance_to_date=allowance_to_date,
        balance=driven - allowance_to_date,
        remaining=allowance_total - driven,
        pace_per_year=pace_per_year,
        forecast_excess=forecast_excess,
        forecast_cost=forecast_cost,
    )
