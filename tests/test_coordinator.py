import ast
import asyncio
import json
import logging
import math
import time
import unittest
from datetime import timedelta
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "custom_components" / "whats_that_plane" / "__init__.py"


class FakeCoordinator:
    def __init__(self, hass, logger, *, name, update_interval):
        self.hass = hass
        self.update_interval = update_interval
        self.listener_updates = 0

    def async_update_listeners(self):
        self.listener_updates += 1


class FakeHass:
    async def async_add_executor_job(self, function, *args):
        return function(*args)

    def async_create_background_task(self, coroutine, name):
        return asyncio.create_task(coroutine, name=name)


class FakeImageContent:
    def __init__(self, body):
        self.body = body

    async def iter_chunked(self, size):
        for offset in range(0, len(self.body), size):
            yield self.body[offset:offset + size]


def logo_png(size=(16, 16), color=(0, 100, 150, 255)):
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakePhotoResponse:
    def __init__(self, payload=None, status=200, headers=None, gate=None, body=b""):
        self.payload = payload if payload is not None else {"photos": []}
        self.status = status
        self.headers = headers or {}
        self.gate = gate
        self.content = FakeImageContent(body)

    async def __aenter__(self):
        if self.gate is not None:
            await self.gate.wait()
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def json(self):
        return self.payload


class FakePhotoSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


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
        "asyncio": asyncio,
        "parsedate_to_datetime": parsedate_to_datetime,
        "async_get_clientsession": lambda hass: hass.photo_session,
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
        and node.name in {"_is_rate_limit_error", "_retry_after_seconds", "_is_valid_airline_logo", "WhatsThatPlaneCoordinator"}
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
        self.flight.icao_24bit = "ABC123"
        self.coordinator.hass.photo_session = FakePhotoSession(FakePhotoResponse({"photos": [{
            "thumbnail_large": {"src": "https://example.com/aircraft.jpg"},
            "photographer": "Test Photographer",
            "link": "https://www.planespotters.net/photo/123",
        }]}))

    async def asyncTearDown(self):
        await self.coordinator.async_cancel_photo_tasks()

    def enable_logo_response(self, *, status=200, headers=None, body=None, gate=None):
        self.flight.airline_icao = "tst"
        self.coordinator.hass.photo_session.response = FakePhotoResponse(
            status=status, headers=headers or {"Content-Type": "image/png"},
            body=logo_png() if body is None else body, gate=gate,
        )

    async def test_logo_validation_does_not_block_live_poll(self):
        gate = asyncio.Event()
        self.enable_logo_response(gate=gate)
        data = await asyncio.wait_for(self.coordinator._async_update_data(), timeout=1)
        self.assertEqual(len(data), 1)
        self.assertIsNone(data[0]["data"]["airline_logo_link"])
        task = self.coordinator._airline_logo_tasks["TST"]
        self.assertFalse(task.done())
        gate.set()
        await task
        self.assertEqual(
            data[0]["data"]["airline_logo_link"],
            "https://www.flightradar24.com/static/images/data/operators/TST_logo0.png",
        )
        self.assertEqual(self.coordinator.listener_updates, 1)

    async def test_missing_logo_is_hidden_and_cached(self):
        self.enable_logo_response(status=404, body=b"")
        await self.coordinator._async_update_data()
        await self.coordinator._airline_logo_tasks["TST"]
        self.flight.id = "another-flight"
        data = await self.coordinator._async_update_data()
        self.assertIsNone(data[0]["data"]["airline_logo_link"])
        self.assertEqual(len(self.coordinator.hass.photo_session.calls), 1)
        self.assertGreater(self.coordinator._airline_logo_cache["TST"]["expires_at"] - time.monotonic(), 6 * 24 * 60 * 60)

    async def test_corrupt_or_placeholder_logos_are_hidden(self):
        for body in [b"not an image", logo_png(size=(1, 1)), logo_png(color=(0, 0, 0, 0))]:
            self.coordinator._airline_logo_cache.clear()
            self.coordinator._airline_logo_next_request_at = 0
            self.enable_logo_response(body=body)
            await self.coordinator._async_update_data()
            await self.coordinator._airline_logo_tasks["TST"]
            self.assertIsNone(self.coordinator.tracked_flights[self.flight.id]["data"]["airline_logo_link"])

    async def test_html_logo_response_is_hidden(self):
        self.enable_logo_response(headers={"Content-Type": "text/html"}, body=b"<html>Not found</html>")
        await self.coordinator._async_update_data()
        await self.coordinator._airline_logo_tasks["TST"]
        self.assertIsNone(self.coordinator._airline_logo_cache["TST"]["link"])

    async def test_valid_logo_cache_is_shared_by_airline(self):
        self.enable_logo_response()
        await self.coordinator._async_update_data()
        await self.coordinator._airline_logo_tasks["TST"]
        self.flight.id = "another-flight"
        data = await self.coordinator._async_update_data()
        self.assertTrue(data[0]["data"]["airline_logo_link"])
        self.assertEqual(len(self.coordinator.hass.photo_session.calls), 1)

    async def test_same_airline_shares_pending_logo_check(self):
        self.enable_logo_response()
        other_flight = SimpleNamespace(**vars(self.flight))
        other_flight.id = "other-flight"
        self.coordinator.fr_api.flights.append(other_flight)
        data = await self.coordinator._async_update_data()
        self.assertEqual(len(self.coordinator._airline_logo_tasks), 1)
        await self.coordinator._airline_logo_tasks["TST"]
        self.assertEqual(len(self.coordinator.hass.photo_session.calls), 1)
        self.assertTrue(all(flight["data"]["airline_logo_link"] for flight in data))

    async def test_oversized_logo_response_is_rejected(self):
        self.enable_logo_response(body=b"x" * (256 * 1024 + 1))
        await self.coordinator._async_update_data()
        await self.coordinator._airline_logo_tasks["TST"]
        self.assertIsNone(self.coordinator._airline_logo_cache["TST"]["link"])

    async def test_airline_change_does_not_publish_previous_logo(self):
        gate = asyncio.Event()
        self.enable_logo_response(gate=gate)
        await self.coordinator._async_update_data()
        task = self.coordinator._airline_logo_tasks["TST"]
        self.flight.airline_icao = "NEW"
        data = await self.coordinator._async_update_data()
        gate.set()
        await task
        link = data[0]["data"]["airline_logo_link"]
        self.assertTrue(link is None or "/NEW_logo0.png" in link)

    async def test_logo_transient_error_does_not_affect_flights(self):
        self.enable_logo_response(status=503)
        await self.coordinator._async_update_data()
        await self.coordinator._airline_logo_tasks["TST"]
        self.assertEqual(len(await self.coordinator._async_update_data()), 1)
        self.assertEqual(len(self.coordinator.hass.photo_session.calls), 1)
        self.assertIsNone(self.coordinator._airline_logo_cache["TST"]["link"])

    async def test_retry_after_date_and_invalid_values(self):
        retry_after = self.coordinator._async_validate_airline_logo.__func__.__globals__["_retry_after_seconds"]
        with patch.object(time, "time", return_value=0):
            self.assertEqual(retry_after({"Retry-After": "Thu, 01 Jan 1970 00:30:00 GMT"}, 900), 1800)
        for value in ["invalid", "NaN", "inf", "-10", ""]:
            self.assertEqual(retry_after({"Retry-After": value}, 900), 900)

    async def test_logo_arriving_after_exit_enriches_history(self):
        gate = asyncio.Event()
        self.enable_logo_response(gate=gate)
        await self.coordinator._async_update_data()
        task = self.coordinator._airline_logo_tasks["TST"]
        self.coordinator.fr_api.flights = []
        self.assertEqual(await self.coordinator._async_update_data(), [])
        gate.set()
        await task
        self.assertTrue(self.coordinator.historic_flights[0]["data"]["airline_logo_link"])
        self.assertEqual(self.coordinator.tracked_flights, {})

    async def test_logo_rate_limits_do_not_change_live_polling(self):
        self.enable_logo_response(status=429, headers={"Retry-After": "1800"})
        clock = SimpleNamespace(time=time.time, monotonic=lambda: 1000)
        self.coordinator._async_update_data.__func__.__globals__["time"] = clock
        await self.coordinator._async_update_data()
        await self.coordinator._airline_logo_tasks["TST"]
        self.assertEqual(self.coordinator._airline_logo_retry_after, 2800)
        self.flight.airline_icao = "NEW"
        self.assertEqual(len(await self.coordinator._async_update_data()), 1)
        self.assertEqual(len(self.coordinator.hass.photo_session.calls), 1)
        self.assertEqual(self.coordinator.update_interval, timedelta(seconds=10))
        self.assertFalse(self.coordinator._rate_limited)

    async def test_logo_tasks_are_cancelled_on_unload(self):
        self.enable_logo_response(gate=asyncio.Event())
        await self.coordinator._async_update_data()
        task = self.coordinator._airline_logo_tasks["TST"]
        await self.coordinator.async_cancel_photo_tasks()
        self.assertTrue(task.cancelled())
        self.assertEqual(self.coordinator._airline_logo_tasks, {})

    async def test_sensor_only_exposes_verified_logo_link(self):
        tree = ast.parse(SOURCE.with_name("sensor.py").read_text(encoding="utf-8"))
        formatter = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_format_flight_data")
        assignment = next(node for node in formatter.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "airline_logo_link" for target in node.targets))
        expression = compile(ast.Expression(assignment.value), "sensor.py", "eval")
        self.assertIsNone(eval(expression, {"flight": {"airline": {"code": {"icao": "TST"}}}}))
        self.assertEqual(eval(expression, {"flight": {"airline_logo_link": "verified-url"}}), "verified-url")

    async def test_photos_are_disabled_by_default(self):
        await self.coordinator._async_update_data()
        self.assertEqual(self.coordinator._planespotters_tasks, {})
        self.assertEqual(self.coordinator.hass.photo_session.calls, [])

    async def test_photo_lookup_does_not_block_live_poll(self):
        self.coordinator.config["use_planespotters_photos"] = True
        gate = asyncio.Event()
        self.coordinator.hass.photo_session.response.gate = gate
        data = await asyncio.wait_for(self.coordinator._async_update_data(), timeout=1)
        self.assertEqual(len(data), 1)
        task = self.coordinator._planespotters_tasks["abc123"]
        self.assertFalse(task.done())
        gate.set()
        await task
        self.assertEqual(data[0]["data"]["planespotters"]["photographer"], "Test Photographer")
        self.assertEqual(self.coordinator.listener_updates, 1)

    async def test_photo_cache_reuses_aircraft_result(self):
        self.coordinator.config["use_planespotters_photos"] = True
        await self.coordinator._async_update_data()
        await self.coordinator._planespotters_tasks["abc123"]
        self.flight.id = "another-flight"
        data = await self.coordinator._async_update_data()
        self.assertEqual(len(self.coordinator.hass.photo_session.calls), 1)
        self.assertEqual(data[0]["data"]["planespotters"]["link"], "https://example.com/aircraft.jpg")

    async def test_photo_arriving_after_exit_enriches_history(self):
        self.coordinator.config["use_planespotters_photos"] = True
        gate = asyncio.Event()
        self.coordinator.hass.photo_session.response.gate = gate
        await self.coordinator._async_update_data()
        task = self.coordinator._planespotters_tasks["abc123"]
        self.coordinator.fr_api.flights = []
        self.assertEqual(await self.coordinator._async_update_data(), [])
        gate.set()
        await task
        self.assertEqual(self.coordinator.historic_flights[0]["data"]["planespotters"]["photographer"], "Test Photographer")
        self.assertEqual(self.coordinator.tracked_flights, {})

    async def test_empty_photo_result_is_cached(self):
        self.coordinator.config["use_planespotters_photos"] = True
        self.coordinator.hass.photo_session.response = FakePhotoResponse()
        await self.coordinator._async_update_data()
        await self.coordinator._planespotters_tasks["abc123"]
        await self.coordinator._async_update_data()
        self.assertEqual(len(self.coordinator.hass.photo_session.calls), 1)
        self.assertEqual(self.coordinator._planespotters_cache["abc123"]["photo"], {})

    async def test_invalid_hex_does_not_request_photo(self):
        self.coordinator.config["use_planespotters_photos"] = True
        self.flight.icao_24bit = "not-a-hex"
        await self.coordinator._async_update_data()
        self.assertEqual(self.coordinator._planespotters_tasks, {})

    async def test_photo_rate_limit_cooldown_does_not_change_polling(self):
        self.coordinator.config["use_planespotters_photos"] = True
        self.coordinator.hass.photo_session.response = FakePhotoResponse(status=429, headers={"Retry-After": "600"})
        clock = SimpleNamespace(time=time.time, monotonic=lambda: 1000)
        self.coordinator._async_update_data.__func__.__globals__["time"] = clock
        await self.coordinator._async_update_data()
        await self.coordinator._planespotters_tasks["abc123"]
        self.assertEqual(self.coordinator._planespotters_retry_after, 1600)
        self.flight.icao_24bit = "def456"
        clock.monotonic = lambda: 1010
        self.assertEqual(len(await self.coordinator._async_update_data()), 1)
        self.assertEqual(len(self.coordinator.hass.photo_session.calls), 1)
        self.assertEqual(self.coordinator.update_interval, timedelta(seconds=10))
        self.assertFalse(self.coordinator._rate_limited)

    async def test_photo_failure_does_not_affect_live_flights(self):
        self.coordinator.config["use_planespotters_photos"] = True
        self.coordinator.hass.photo_session.response = FakePhotoResponse(status=503)
        await self.coordinator._async_update_data()
        await self.coordinator._planespotters_tasks["abc123"]
        self.assertEqual(len(await self.coordinator._async_update_data()), 1)
        self.assertEqual(len(self.coordinator.hass.photo_session.calls), 1)

    async def test_unload_cancels_photo_requests(self):
        self.coordinator.config["use_planespotters_photos"] = True
        self.coordinator.hass.photo_session.response.gate = asyncio.Event()
        await self.coordinator._async_update_data()
        task = self.coordinator._planespotters_tasks["abc123"]
        await self.coordinator.async_cancel_photo_tasks()
        self.assertTrue(task.cancelled())
        self.assertEqual(self.coordinator._planespotters_tasks, {})

    async def test_photo_cache_expires_after_one_day(self):
        self.coordinator.config["use_planespotters_photos"] = True
        clock = SimpleNamespace(time=time.time, monotonic=lambda: 1000)
        self.coordinator._async_update_data.__func__.__globals__["time"] = clock
        await self.coordinator._async_update_data()
        await self.coordinator._planespotters_tasks["abc123"]
        self.assertEqual(self.coordinator._planespotters_cache["abc123"]["expires_at"], 87400)
        clock.monotonic = lambda: 87401
        await self.coordinator._async_update_data()
        await self.coordinator._planespotters_tasks["abc123"]
        self.assertEqual(len(self.coordinator.hass.photo_session.calls), 2)

    async def test_duplicate_aircraft_share_one_pending_lookup(self):
        self.coordinator.config["use_planespotters_photos"] = True
        other_flight = SimpleNamespace(**vars(self.flight))
        other_flight.id = "other-flight"
        self.coordinator.fr_api.flights.append(other_flight)
        data = await self.coordinator._async_update_data()
        self.assertEqual(len(self.coordinator._planespotters_tasks), 1)
        await self.coordinator._planespotters_tasks["abc123"]
        self.assertEqual(len(self.coordinator.hass.photo_session.calls), 1)
        self.assertTrue(all(flight["data"]["planespotters"]["photographer"] for flight in data))

    async def test_queued_photo_lookups_respect_shared_cooldown(self):
        self.coordinator.config["use_planespotters_photos"] = True
        self.coordinator.hass.photo_session.response = FakePhotoResponse(status=429)
        other_flight = SimpleNamespace(**vars(self.flight))
        other_flight.id = "other-flight"
        other_flight.icao_24bit = "def456"
        self.coordinator.fr_api.flights.append(other_flight)
        await self.coordinator._async_update_data()
        await asyncio.gather(*self.coordinator._planespotters_tasks.values())
        self.assertEqual(len(self.coordinator.hass.photo_session.calls), 1)
        self.assertEqual(len(self.coordinator.tracked_flights), 2)

    async def test_photos_without_credit_are_not_used(self):
        self.coordinator.config["use_planespotters_photos"] = True
        self.coordinator.hass.photo_session.response.payload["photos"][0].pop("photographer")
        await self.coordinator._async_update_data()
        await self.coordinator._planespotters_tasks["abc123"]
        self.assertEqual(self.coordinator._planespotters_cache["abc123"]["photo"], {})

    async def test_malformed_photo_payload_is_isolated(self):
        self.coordinator.config["use_planespotters_photos"] = True
        self.coordinator.hass.photo_session.response.payload = []
        await self.coordinator._async_update_data()
        await self.coordinator._planespotters_tasks["abc123"]
        self.assertEqual(self.coordinator._planespotters_cache["abc123"]["photo"], {})
        self.assertEqual(len(self.coordinator.tracked_flights), 1)

    async def test_photo_cache_and_pending_work_are_bounded(self):
        self.coordinator.config["use_planespotters_photos"] = True
        self.coordinator._planespotters_cache = {
            f"{index:06x}": {"photo": {}, "expires_at": 0} for index in range(256)
        }
        await self.coordinator._async_update_data()
        await self.coordinator._planespotters_tasks["abc123"]
        self.assertEqual(len(self.coordinator._planespotters_cache), 256)
        self.assertNotIn("000000", self.coordinator._planespotters_cache)
        self.coordinator.hass.photo_session.response.gate = asyncio.Event()
        self.coordinator.fr_api.flights = [
            SimpleNamespace(**{**vars(self.flight), "id": str(index), "icao_24bit": f"{index + 256:06x}"})
            for index in range(32)
        ]
        self.assertEqual(len(await self.coordinator._async_update_data()), 32)
        self.assertEqual(len(self.coordinator._planespotters_tasks), 16)

    async def test_sensor_photo_fields_preserve_attribution(self):
        tree = ast.parse(SOURCE.with_name("sensor.py").read_text(encoding="utf-8"))
        formatter = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_format_flight_data")
        output = next(node.value for node in formatter.body if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict))
        fields = {"planespotters_photo_link", "planespotters_photographer", "planespotters_photo_page"}
        selected = [(key, value) for key, value in zip(output.keys, output.values) if key.value in fields]
        projected = ast.copy_location(
            ast.Dict(keys=[key for key, value in selected], values=[value for key, value in selected]), output
        )
        flight = {"planespotters": {"link": "image", "photographer": "Author", "page": "page"}}
        namespace = {"flight": flight, "dpath": SimpleNamespace(util=SimpleNamespace(get=nested_get))}
        data = eval(compile(ast.Expression(projected), "sensor.py", "eval"), namespace)
        self.assertEqual(data, {
            "planespotters_photo_link": "image", "planespotters_photographer": "Author", "planespotters_photo_page": "page",
        })
        namespace["flight"] = {}
        self.assertTrue(all(value is None for value in eval(compile(ast.Expression(projected), "sensor.py", "eval"), namespace).values()))

    async def test_photo_setting_translations_are_consistent(self):
        strings = json.loads(SOURCE.with_name("strings.json").read_text(encoding="utf-8"))
        translation = json.loads((SOURCE.parent / "translations" / "en.json").read_text(encoding="utf-8"))
        self.assertEqual(strings, translation)
        for section, step in [("config", "user"), ("options", "init")]:
            self.assertIn("Planespotters", strings[section]["step"][step]["data"]["use_planespotters_photos"])
            self.assertIn("credit", strings[section]["step"][step]["data_description"]["use_planespotters_photos"])

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
        self.assertEqual(self.coordinator.update_interval, timedelta(seconds=10))
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

    async def test_repeated_feed_rate_limits_keep_configured_interval(self):
        self.coordinator.fr_api.error = RateLimitError()
        for attempt in range(10):
            self.assertEqual(await self.coordinator._async_update_data(), [])
            self.assertEqual(self.coordinator.update_interval, timedelta(seconds=10))

    async def test_response_status_code_rate_limit_clears_live_flights(self):
        await self.coordinator._async_update_data()
        error = Exception("Too many requests")
        error.response = SimpleNamespace(status_code=429)
        self.coordinator.fr_api.error = error
        self.assertEqual(await self.coordinator._async_update_data(), [])
        self.assertEqual(len(self.coordinator.historic_flights), 1)

    async def test_successful_feed_recovers_after_rate_limit(self):
        await self.coordinator._async_update_data()
        self.coordinator.fr_api.error = RateLimitError()
        await self.coordinator._async_update_data()
        self.coordinator.fr_api.error = None
        self.assertEqual(len(await self.coordinator._async_update_data()), 1)
        self.assertEqual(self.coordinator.update_interval, timedelta(seconds=10))
        self.assertFalse(self.coordinator._rate_limited)

    async def test_feed_recovery_logs_duration_and_consecutive_failures(self):
        self.coordinator.fr_api.error = RateLimitError()
        with self.assertLogs(__name__, level="INFO") as captured:
            with patch.object(time, "monotonic", return_value=1000):
                await self.coordinator._async_update_data()
            with patch.object(time, "monotonic", return_value=1010):
                await self.coordinator._async_update_data()
            self.coordinator.fr_api.error = None
            with patch.object(time, "monotonic", return_value=1025):
                await self.coordinator._async_update_data()
        self.assertEqual(len(captured.records), 2)
        self.assertEqual(captured.records[0].levelno, logging.WARNING)
        self.assertEqual(captured.records[1].levelno, logging.INFO)
        self.assertIn("recovered after 25.0 seconds and 2 consecutive", captured.output[1])
        self.assertIn("received 1 flights", captured.output[1])
        self.assertIsNone(self.coordinator._feed_rate_limit_started_at)
        self.assertEqual(self.coordinator._feed_rate_limit_failures, 0)

    async def test_feed_recovery_timer_restarts_for_next_episode(self):
        for started_at, recovered_at in [(1000, 1020), (1100, 1105)]:
            with self.assertLogs(__name__, level="INFO") as captured:
                self.coordinator.fr_api.error = RateLimitError()
                with patch.object(time, "monotonic", return_value=started_at):
                    await self.coordinator._async_update_data()
                self.coordinator.fr_api.error = None
                with patch.object(time, "monotonic", return_value=recovered_at):
                    await self.coordinator._async_update_data()
            self.assertIn(
                f"recovered after {recovered_at - started_at:.1f} seconds and 1 consecutive",
                captured.output[1],
            )

    async def test_healthy_poll_summary_is_debug_only(self):
        with self.assertLogs(__name__, level="DEBUG") as captured:
            await self.coordinator._async_update_data()
        summary = [record for record in captured.records if "poll completed" in record.getMessage()]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0].levelno, logging.DEBUG)
        self.assertIn("received=1 visible=1 held=0 archived=0", summary[0].getMessage())
        self.assertFalse(any(record.levelno >= logging.INFO for record in captured.records))

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