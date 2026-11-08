"""Tyre pressure sensor must not disappear when a parked car sends no readings."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "porscheconnect"))

from pyporscheconnectapi.vehicle import PorscheVehicle  # noqa: E402

ENABLED = {"isEnabled": True}


def _vehicle(measurements):
    v = PorscheVehicle(vin="WP0TEST", connection=None, status={"vin": "WP0TEST", "modelName": "Taycan", "measurements": measurements})
    v._update_vehicle_data()
    return v


def test_listed_without_value_still_counts_as_monitoring():
    v = _vehicle([{"key": "TIRE_PRESSURE_FRONT_LEFT", "status": ENABLED}])
    assert v.has_tire_pressure_monitoring is True
    assert v.tire_pressures is None
    assert v.tire_pressure_status is None  # shown as "unknown", not "OK"


def test_disabled_measurement_means_no_monitoring():
    v = _vehicle([{"key": "TIRE_PRESSURE_FRONT_LEFT", "status": {"isEnabled": False}}])
    assert v.has_tire_pressure_monitoring is False


def test_readings_within_and_outside_tolerance():
    def reading(diff):
        return {"key": "TIRE_PRESSURE_FRONT_LEFT", "status": ENABLED,
                "value": {"actualPressureBar": 2.7, "differenceBar": diff, "lastModified": "2026-11-04T08:07:36Z"}}

    assert _vehicle([reading(0.3)]).tire_pressure_status is True
    assert _vehicle([reading(-0.5)]).tire_pressure_status is False
