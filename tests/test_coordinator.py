import ast
import logging
import math
import time
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "custom_components" / "whats_that_plane" / "__init__.py"


class FakeCoordinator:
    def __init__(self, hass, logger, *, name, update_interval):
        self.hass = hass
        self.update_interval = update_interval


class FakeHass:
    async def async_add_executor_job(self, function, *args):
        return function(*args)


class RateLimitError(Exception):
    code = 429


class FakeAPI:
    def __init__(self):
        self.flights = []
        self.error = None

    def get_bounds_by_point(self, *args):
        return args

    def get_flights(self, *args):
        if self.error:
            raise self.error
        return self.flights


def nested_get(data, path, default=None):
    for key in path.split("/"):
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return data


def nested_new(data, path, value):
    keys = path.split("/")
    for key in keys[:-1]:
        data = data.setdefault(key, {})
    data[keys[-1]] = value


def distance_between(origin, destination):
    latitude_delta = math.radians(destination[0] - origin[0])
    longitude_delta = math.radians(destination[1] - origin[1])
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(origin[0]))
        * math.cos(math.radians(destination[0]))
        * math.sin(longitude_delta / 2) ** 2
    )
    return SimpleNamespace(km=6371 * 2 * math.asin(math.sqrt(haversine)))


def load_coordinator():
    namespace = {
        "DataUpdateCoordinator": FakeCoordinator,
        "HomeAssistant": FakeHass,
        "ConfigEntry": SimpleNamespace,
        "FlightRadar24API": FakeAPI,
        "UpdateFailed": RuntimeError,
        "DOMAIN": "whats_that_plane",
        "_LOGGER": logging.getLogger(__name__),
        "timedelta": timedelta,
        "math": math,
        "time": time,
        "geodesic": distance_between,
        "dpath": SimpleNamespace(util=SimpleNamespace(get=nested_get, new=nested_new)),
    }
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id.isupper()
            and not target.id.startswith("_") for target in node.targets
        )
        or isinstance(node, (ast.FunctionDef, ast.ClassDef))
        and node.name in {"_is_rate_limit_error", "WhatsThatPlaneCoordinator"}
    ]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["WhatsThatPlaneCoordinator"]


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = load_coordinator()(FakeHass(), config={
            "latitude": 52.2982,
            "longitude": -1.52662,
            "radius_km": 2,
            "facing_direction": 0,
            "fov_cone": 360,
            "update_interval": 10,
            "hold_flight_data_seconds": 0,
            "historic_flights_max_count": 5,
        })
        self.flight = SimpleNamespace(
            id="test-flight", latitude=52.2982, longitude=-1.52662,
            altitude=30000, ground_speed=400, heading=90, callsign="TEST123",
        )
        self.coordinator.fr_api.flights = [self.flight]
        self.coordinator._get_flight_details_scraper = lambda flight_id: {}

    async def test_outside_radius_moves_to_history(self):
        self.assertEqual(len(await self.coordinator._async_update_data()), 1)
        self.flight.latitude += 0.03
        self.assertEqual(await self.coordinator._async_update_data(), [])
        self.assertEqual(len(self.coordinator.historic_flights), 1)

    async def test_missing_flight_moves_to_history(self):
        await self.coordinator._async_update_data()
        self.coordinator.fr_api.flights = []
        self.assertEqual(await self.coordinator._async_update_data(), [])
        self.assertEqual(len(self.coordinator.historic_flights), 1)

    async def test_feed_rate_limit_does_not_keep_flight_overhead(self):
        await self.coordinator._async_update_data()
        self.coordinator.fr_api.error = RateLimitError()
        self.assertEqual(await self.coordinator._async_update_data(), [])
        self.assertEqual(len(self.coordinator.historic_flights), 1)
        self.assertEqual(self.coordinator.update_interval, timedelta(seconds=20))
        await self.coordinator._async_update_data()
        self.assertEqual(len(self.coordinator.historic_flights), 1)

    async def test_feed_rate_limit_respects_history_limit(self):
        self.coordinator.config["historic_flights_max_count"] = 0
        await self.coordinator._async_update_data()
        self.coordinator.fr_api.error = RateLimitError()
        self.assertEqual(await self.coordinator._async_update_data(), [])
        self.assertEqual(self.coordinator.historic_flights, [])

    async def test_feed_rate_limit_does_not_apply_configured_hold(self):
        self.coordinator.config["hold_flight_data_seconds"] = 300
        await self.coordinator._async_update_data()
        self.coordinator.fr_api.error = RateLimitError()
        self.assertEqual(await self.coordinator._async_update_data(), [])
        self.assertEqual(len(self.coordinator.historic_flights), 1)

    async def test_feed_backoff_is_capped_at_five_minutes(self):
        self.coordinator.fr_api.error = RateLimitError()
        for attempt in range(10):
            self.assertEqual(await self.coordinator._async_update_data(), [])
        self.assertEqual(self.coordinator.update_interval, timedelta(minutes=5))

    async def test_response_status_code_rate_limit_clears_live_flights(self):
        await self.coordinator._async_update_data()
        error = Exception("Too many requests")
        error.response = SimpleNamespace(status_code=429)
        self.coordinator.fr_api.error = error
        self.assertEqual(await self.coordinator._async_update_data(), [])
        self.assertEqual(len(self.coordinator.historic_flights), 1)

    async def test_successful_feed_resets_backoff(self):
        await self.coordinator._async_update_data()
        self.coordinator.fr_api.error = RateLimitError()
        await self.coordinator._async_update_data()
        self.coordinator.fr_api.error = None
        self.assertEqual(len(await self.coordinator._async_update_data()), 1)
        self.assertEqual(self.coordinator.update_interval, timedelta(seconds=10))
        self.assertFalse(self.coordinator._rate_limited)

    async def test_detail_rate_limit_keeps_confirmed_live_flight(self):
        def rate_limited_details(flight_id):
            raise RateLimitError()

        self.coordinator._get_flight_details_scraper = rate_limited_details
        self.assertEqual(len(await self.coordinator._async_update_data()), 1)
        self.assertTrue(self.coordinator.tracked_flights[self.flight.id]["details_pending"])
        self.assertEqual(self.coordinator.update_interval, timedelta(seconds=10))

    async def test_configured_hold_applies_to_successful_feed(self):
        self.coordinator.config["hold_flight_data_seconds"] = 30
        with patch.object(time, "time", return_value=1000):
            await self.coordinator._async_update_data()
        self.coordinator.fr_api.flights = []
        with patch.object(time, "time", return_value=1010):
            self.assertEqual(len(await self.coordinator._async_update_data()), 1)
        with patch.object(time, "time", return_value=1031):
            self.assertEqual(await self.coordinator._async_update_data(), [])


if __name__ == "__main__":
    unittest.main()