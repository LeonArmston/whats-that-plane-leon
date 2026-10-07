import os
import shutil
import logging
import asyncio
import math
import time
import dpath.util
from datetime import timedelta
from email.utils import parsedate_to_datetime
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, CoreState, Event
from homeassistant.const import EVENT_HOMEASSISTANT_START
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from FlightRadarAPI import FlightRadar24API
from geopy.distance import geodesic
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor"]
MIN_UPDATE_INTERVAL_SECONDS = 10
DEFAULT_UPDATE_INTERVAL_SECONDS = 60
DETAILS_CACHE_TTL_SECONDS = 5 * 60
DETAILS_RETRY_COOLDOWN_SECONDS = 3 * 60
PHOTO_CACHE_TTL_SECONDS = 24 * 60 * 60
PHOTO_EMPTY_CACHE_TTL_SECONDS = 6 * 60 * 60
PHOTO_RETRY_COOLDOWN_SECONDS = 3 * 60
PHOTO_REQUEST_INTERVAL_SECONDS = 0.25
PHOTO_CACHE_MAX_COUNT = 256
PHOTO_PENDING_MAX_COUNT = 16
ORIGIN_LATITUDE = 'airport/origin/position/latitude'
ORIGIN_LONGITUDE = 'airport/origin/position/longitude'
DESTINATION_LATITUDE = 'airport/destination/position/latitude'
DESTINATION_LONGITUDE = 'airport/destination/position/longitude'

def _is_rate_limit_error(error):
    return (
        getattr(error, "code", None) == 429
        or getattr(getattr(error, "response", None), "status_code", None) == 429
    )

def setup_frontend_files(hass: HomeAssistant) -> None:
    source_dir = os.path.join(os.path.dirname(__file__), 'www')
    destination_dir = hass.config.path(f"www/community/{DOMAIN}")

    if not os.path.exists(source_dir):
        _LOGGER.error(f"www source directory not found at {source_dir}")
        return

    if not os.path.exists(destination_dir):
        os.makedirs(destination_dir)
        _LOGGER.info(f"Created www destination directory at {destination_dir}")

    for filename in os.listdir(source_dir):
        source_file = os.path.join(source_dir, filename)
        destination_file = os.path.join(destination_dir, filename)

        if os.path.isdir(source_file):
            shutil.copytree(source_file, destination_file, dirs_exist_ok=True)
            continue

        should_copy = False
        if not os.path.exists(destination_file):
            should_copy = True
        else:
            try:
                source_time = os.path.getmtime(source_file)
                destination_time = os.path.getmtime(destination_file)
                if source_time > destination_time:
                    should_copy = True
            except OSError as e:
                _LOGGER.warning(f"Could not compare file times for {filename}: {e}")
                should_copy = True

        if should_copy:
            try:
                shutil.copy2(source_file, destination_file)
                _LOGGER.info(f"Copied/Updated {filename} in {destination_dir}")
            except OSError as e:
                _LOGGER.error(f"Failed to copy {filename}: {e}")

async def register_lovelace_resource(hass, url):
    lovelace = hass.data.get("lovelace")
    if lovelace is None:
        _LOGGER.warning("Could not register Lovelace resource, 'lovelace' not found in hass.data")
        return

    if not hasattr(lovelace, "resources"):
        _LOGGER.warning("Could not register Lovelace resource, 'resources' attribute not found in lovelace data")
        return

    resources = lovelace.resources
    if resources is None:
        _LOGGER.warning("Lovelace resources is None.")
        return

    if any(res["url"] == url for res in resources.async_items()):
        _LOGGER.info(f"Lovelace resource '{url}' is already registered.")
        return

    _LOGGER.info(f"Registering Lovelace resource: {url}")
    try:
        await resources.async_create_item({
            "res_type": "module",
            "url": url,
        })
    except Exception as e:
        _LOGGER.error(f"Failed to register Lovelace resource: {e}")

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.async_add_executor_job(setup_frontend_files, hass)

    async def _register_resource(event: Event | None = None) -> None:
        await register_lovelace_resource(
            hass, f"/local/community/{DOMAIN}/whats-that-plane-map.js"
        )

        hass.data.pop("whats_that_plane_listener", None)

    if hass.state is CoreState.running:
        await _register_resource()
    else:
        hass.data["whats_that_plane_listener"] = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_START, _register_resource
        )

    hass.data.setdefault(DOMAIN, {})

    coordinator = WhatsThatPlaneCoordinator(hass, entry=entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True

async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if len(hass.config_entries.async_entries(DOMAIN)) == 1:
        _LOGGER.info("Last entry for What's that plane?! being removed, cleaning up resources.")
        await async_remove_lovelace_resource(hass, f"/local/community/{DOMAIN}/whats-that-plane-map.js")
        await hass.async_add_executor_job(remove_frontend_files, hass)


    if listener := hass.data.pop("whats_that_plane_listener", None):
        listener()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await hass.data[DOMAIN][entry.entry_id].async_cancel_photo_tasks()
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

def remove_frontend_files(hass: HomeAssistant) -> None:
    destination_dir = hass.config.path(f"www/community/{DOMAIN}")
    if os.path.exists(destination_dir):
        shutil.rmtree(destination_dir)
        _LOGGER.info(f"Removed www directory at {destination_dir}")

async def async_remove_lovelace_resource(hass: HomeAssistant, url: str):
    lovelace = hass.data.get("lovelace")
    if lovelace and hasattr(lovelace, "resources"):
        resources = lovelace.resources
        resource_to_remove = next((res for res in resources.async_items() if res["url"] == url), None)
        
        if resource_to_remove:
            try:
                await resources.async_delete_item(resource_to_remove["id"])
                _LOGGER.info(f"Removed Lovelace resource: {url}")
            except Exception as e:
                _LOGGER.error(f"Failed to remove Lovelace resource: {e}")
        else:
            _LOGGER.warning(f"Could not find Lovelace resource to remove: {url}")

class WhatsThatPlaneCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry | None = None, config: dict | None = None):
        if entry:
            self.config_entry = entry
            self._config = {**entry.data, **entry.options}
        elif config:
            self.config_entry = None
            self._config = config
        else:
            raise ValueError("Coordinator must be initialized with either an entry or a config dict.")

        update_seconds = self._config.get("update_interval", DEFAULT_UPDATE_INTERVAL_SECONDS)
        try:
            if (
                not isinstance(update_seconds, (int, float))
                or isinstance(update_seconds, bool)
                or not math.isfinite(update_seconds)
            ):
                raise ValueError("Invalid update interval")
            update_interval = timedelta(seconds=update_seconds)
        except (ValueError, OverflowError):
            _LOGGER.warning(
                "Invalid update_interval %r; using default %s seconds",
                update_seconds, DEFAULT_UPDATE_INTERVAL_SECONDS,
            )
            update_interval = timedelta(seconds=DEFAULT_UPDATE_INTERVAL_SECONDS)
        else:
            if update_seconds < MIN_UPDATE_INTERVAL_SECONDS:
                _LOGGER.warning(
                    "update_interval %s is below minimum %s; clamping to %s seconds",
                    update_seconds, MIN_UPDATE_INTERVAL_SECONDS, MIN_UPDATE_INTERVAL_SECONDS,
                )
                update_interval = timedelta(seconds=MIN_UPDATE_INTERVAL_SECONDS)

        self.fr_api = FlightRadar24API()
        self.scraper = None                           
        self.tracked_flights = {}
        self.historic_flights = []
        self._normal_update_interval = update_interval
        self._rate_limited = False
        self._feed_rate_limit_started_at = None
        self._feed_rate_limit_failures = 0
        self._planespotters_cache = {}
        self._planespotters_tasks = {}
        self._planespotters_semaphore = asyncio.Semaphore(1)
        self._planespotters_retry_after = 0
        self._planespotters_next_request_at = 0

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=self._normal_update_interval,
        )

    def _get_flight_details_scraper(self, flight_id: str) -> dict:
        import cloudscraper
        if self.scraper is None:
            self.scraper = cloudscraper.create_scraper()
            self.scraper.get("https://www.flightradar24.com/")
        
        url = f"https://data-live.flightradar24.com/clickhandler/?flight={flight_id}"
        response = self.scraper.get(url, timeout=10)
        if response.status_code == 403:
            self.scraper.get("https://www.flightradar24.com/")
            response = self.scraper.get(url, timeout=10)
        response.raise_for_status()
        return response.json()

    @property
    def config(self):
        return self._config

    def _details_are_fresh(self, flight_info: dict) -> bool:
        updated_at = flight_info.get("details_updated_at")
        return updated_at is not None and time.time() - updated_at < DETAILS_CACHE_TTL_SECONDS

    def _details_retry_allowed(self, flight_info: dict) -> bool:
        return time.time() >= flight_info.get("details_retry_after", 0)

    def _schedule_planespotters_photo(self, flight, flight_info):
        if not self.config.get("use_planespotters_photos", False):
            return
        hex_code = dpath.util.get(flight_info["data"], "aircraft/hex", default=None)
        hex_code = hex_code or getattr(flight, "icao_24bit", None)
        if not isinstance(hex_code, str):
            return
        hex_code = hex_code.strip().lower()
        if len(hex_code) != 6 or any(char not in "0123456789abcdef" for char in hex_code):
            return
        flight_info["photo_hex"] = hex_code
        cached = self._planespotters_cache.get(hex_code)
        now = time.monotonic()
        if cached and now < cached["expires_at"]:
            flight_info["data"]["planespotters"] = cached["photo"]
            return
        if (
            now < self._planespotters_retry_after
            or hex_code in self._planespotters_tasks
            or len(self._planespotters_tasks) >= PHOTO_PENDING_MAX_COUNT
        ):
            return
        task = self.hass.async_create_background_task(
            self._async_fetch_planespotters_photo(hex_code),
            f"{DOMAIN}_photo_{hex_code}",
        )
        self._planespotters_tasks[hex_code] = task
        task.add_done_callback(lambda completed: self._planespotters_tasks.pop(hex_code, None))

    async def _async_fetch_planespotters_photo(self, hex_code):
        async with self._planespotters_semaphore:
            if time.monotonic() < self._planespotters_retry_after:
                return
            delay = self._planespotters_next_request_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._planespotters_next_request_at = time.monotonic() + PHOTO_REQUEST_INTERVAL_SECONDS
            photo = {}
            cache_seconds = PHOTO_RETRY_COOLDOWN_SECONDS
            try:
                session = async_get_clientsession(self.hass)
                async with session.get(
                    f"https://api.planespotters.net/pub/photos/hex/{hex_code}",
                    headers={"User-Agent": "WhatsThatPlaneHA (https://github.com/LeonArmston/whats-that-plane-leon)"},
                    timeout=10,
                ) as response:
                    if response.status == 429:
                        retry_after = response.headers.get("Retry-After", "")
                        try:
                            retry_seconds = float(retry_after)
                        except (ValueError, TypeError):
                            try:
                                retry_seconds = parsedate_to_datetime(retry_after).timestamp() - time.time()
                            except (ValueError, TypeError, OverflowError):
                                retry_seconds = 0
                        if not math.isfinite(retry_seconds):
                            retry_seconds = 0
                        cache_seconds = max(PHOTO_RETRY_COOLDOWN_SECONDS, retry_seconds)
                        self._planespotters_retry_after = time.monotonic() + cache_seconds
                        _LOGGER.warning(
                            "Planespotters is rate limiting (HTTP 429); pausing photo lookups for %.0f seconds; "
                            "live flight polling is unaffected",
                            cache_seconds,
                        )
                    else:
                        response.raise_for_status()
                        payload = await response.json()
                        if not isinstance(payload, dict) or not isinstance(payload.get("photos", []), list):
                            raise ValueError("Invalid Planespotters photo response")
                        for candidate in payload.get("photos", []):
                            if not isinstance(candidate, dict):
                                continue
                            thumbnail = candidate.get("thumbnail_large") or candidate.get("thumbnail") or {}
                            if not isinstance(thumbnail, dict):
                                continue
                            link = thumbnail.get("src")
                            page = candidate.get("link")
                            photographer = candidate.get("photographer")
                            if (
                                isinstance(link, str) and link.startswith("https://")
                                and isinstance(page, str) and page.startswith("https://")
                                and isinstance(photographer, str) and photographer.strip()
                            ):
                                photo = {"link": link, "photographer": photographer, "page": page}
                                break
                        cache_seconds = PHOTO_CACHE_TTL_SECONDS if photo else PHOTO_EMPTY_CACHE_TTL_SECONDS
            except Exception as err:
                _LOGGER.debug("Could not fetch Planespotters photo for %s: %s", hex_code, err)

            if hex_code not in self._planespotters_cache and len(self._planespotters_cache) >= PHOTO_CACHE_MAX_COUNT:
                self._planespotters_cache.pop(next(iter(self._planespotters_cache)))
            self._planespotters_cache[hex_code] = {
                "photo": photo,
                "expires_at": time.monotonic() + cache_seconds,
            }
            updated = False
            for flight_info in list(self.tracked_flights.values()) + self.historic_flights:
                if flight_info.get("photo_hex") == hex_code:
                    flight_info["data"]["planespotters"] = photo
                    updated = True
            if updated:
                self.async_update_listeners()

    async def async_cancel_photo_tasks(self):
        tasks = list(self._planespotters_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._planespotters_tasks.clear()

    def _archive_flights(self, flight_ids):
        for flight_id in flight_ids:
            self.historic_flights.insert(0, self.tracked_flights.pop(flight_id))
        historic_max_count = self._config.get("historic_flights_max_count", 0)
        self.historic_flights = self.historic_flights[:historic_max_count]

    def _calculate_bearing(self, your_latitude, your_longitude, flight_latitude, flight_longitude):
        delta_longitude = math.radians(flight_longitude - your_longitude)
        your_latitude = math.radians(your_latitude)
        flight_latitude = math.radians(flight_latitude)
        y = math.sin(delta_longitude) * math.cos(flight_latitude)
        x = math.cos(your_latitude) * math.sin(flight_latitude) - math.sin(your_latitude) * math.cos(flight_latitude) * math.cos(delta_longitude)
        initial_bearing = math.atan2(y, x)
        return (math.degrees(initial_bearing) + 360) % 360

    def _is_within_fov(self, bearing, direction, fov):
        if fov >= 360:
            return True
        half_fov = fov / 2
        lower_bound = (direction - half_fov) % 360
        upper_bound = (direction + half_fov) % 360
        return lower_bound <= bearing <= upper_bound if lower_bound < upper_bound else bearing >= lower_bound or bearing <= upper_bound

    async def _async_update_data(self):
        poll_started_at = time.monotonic()
        try:
            config = self.config
            your_latitude = config["latitude"]
            your_longitude = config["longitude"]
            radius_km = config["radius_km"] * 1000
            minimum_altitude = config.get("filter_flight_altitude_ft_minimum", 0)
            maximum_altitude = config.get("filter_flight_altitude_ft_maximum", 60000)
            hold_seconds = config.get("hold_flight_data_seconds", 0)
            distance_units = config.get("distance_units", "imperial (miles (mi))")
            altitude_units = config.get("altitude_units", "imperial (feet (ft))")
            speed_units = config.get("speed_units", "imperial (miles per hour (mph))")


            bounds = await self.hass.async_add_executor_job(
                self.fr_api.get_bounds_by_point, your_latitude, your_longitude, radius_km
            )
            all_flights = await self.hass.async_add_executor_job(
                self.fr_api.get_flights, None, bounds
            )
            feed_received_at = time.monotonic()
            if self._feed_rate_limit_started_at is not None:
                _LOGGER.info(
                    "FR24 flight-list feed recovered after %.1f seconds and %s consecutive "
                    "HTTP 429 responses; received %s flights",
                    feed_received_at - self._feed_rate_limit_started_at,
                    self._feed_rate_limit_failures,
                    len(all_flights),
                )
            self._feed_rate_limit_started_at = None
            self._feed_rate_limit_failures = 0
            self._rate_limited = False

            all_flights_map = {flight.id: flight for flight in all_flights if flight.id}
            currently_visible_ids = set()

            for flight_id, flight in all_flights_map.items():
                if flight.latitude is None or flight.longitude is None:
                    continue

                flight_distance_km = geodesic((your_latitude, your_longitude), (flight.latitude, flight.longitude)).km
                if flight_distance_km > config["radius_km"]:
                    continue

                flight_altitude_for_filter = flight.altitude if flight.altitude is not None else 0
                if not (minimum_altitude <= flight_altitude_for_filter <= maximum_altitude):
                    continue

                flight_bearing = self._calculate_bearing(your_latitude, your_longitude, flight.latitude, flight.longitude)

                if self._is_within_fov(flight_bearing, config["facing_direction"], config["fov_cone"]):
                    currently_visible_ids.add(flight_id)

                    if flight_id not in self.tracked_flights:
                        _LOGGER.debug(f"New flight in FOV: {flight_id}")

                    flight_info = self.tracked_flights.setdefault(flight_id, {"data": {}})
                    flight_details = flight_info["data"]
                    # Even flights without cached details must respect the 429 cooldown.
                    if not self._details_are_fresh(flight_info) and self._details_retry_allowed(flight_info):
                        try:
                            flight_details.update(await self.hass.async_add_executor_job(self._get_flight_details_scraper, flight.id))
                        except Exception as e:
                            flight_info["details_pending"] = _is_rate_limit_error(e)
                            if flight_info["details_pending"]:
                                flight_info["details_retry_after"] = time.time() + DETAILS_RETRY_COOLDOWN_SECONDS
                                _LOGGER.warning(
                                    "FR24 is rate limiting (HTTP 429); delaying detail retry for %s by %s seconds",
                                    flight_id, DETAILS_RETRY_COOLDOWN_SECONDS,
                                )
                            else:
                                _LOGGER.warning(f"Could not fetch details for {flight_id}: {e}")
                        else:
                            flight_info["details_updated_at"] = time.time()
                            flight_info.pop("details_pending", None)
                            flight_info.pop("details_retry_after", None)

                    flight_details['latitude'] = flight.latitude
                    flight_details['longitude'] = flight.longitude

                    if flight.altitude is not None:
                        if altitude_units.startswith('metric'):
                            flight_details['altitude'] = round(flight.altitude * 0.3048)
                        else:
                            flight_details['altitude'] = flight.altitude
                    else:
                        flight_details['altitude'] = 0

                    flight_details['heading'] = flight.heading

                    if flight.ground_speed is not None:
                        flight_details['ground_speed_kts'] = flight.ground_speed
                        if speed_units.startswith('metric'):
                            flight_details['ground_speed'] = round(flight.ground_speed * 1.852)
                        else:
                            flight_details['ground_speed'] = round(flight.ground_speed * 1.15078)
                    else:
                        flight_details['ground_speed_kts'] = 0
                        flight_details['ground_speed'] = 0
                        
                    flight_details['callsign'] = flight.callsign

                    if 'trail' not in flight_details:
                        flight_details['trail'] = []

                    latest_point = {
                        "lat": flight.latitude,
                        "lng": flight.longitude,
                        "alt": flight.altitude,
                        "spd": flight.ground_speed,
                        "hd": flight.heading,
                        "ts": int(time.time())
                    }
                    if not flight_details['trail'] or (flight_details['trail'][0]['lat'] != latest_point['lat'] and flight_details['trail'][0]['lng'] != latest_point['lng']):
                        flight_details['trail'].insert(0, latest_point)

                    dpath.util.new(flight_details, 'identification/id', flight.id)
                    dpath.util.new(flight_details, 'identification/callsign', flight.callsign)
                    
                    origin_position = (dpath.util.get(flight_details, ORIGIN_LATITUDE, default=None), dpath.util.get(flight_details, ORIGIN_LONGITUDE, default=None))
                    destination_position = (dpath.util.get(flight_details, DESTINATION_LATITUDE, default=None), dpath.util.get(flight_details, DESTINATION_LONGITUDE, default=None))
                    current_position = (flight.latitude, flight.longitude)
                    
                    total_distance, distance_traveled, progress_percent = 0, 0, 0

                    if all(position is not None for position in origin_position) and all(position is not None for position in destination_position) and all(position is not None for position in current_position):
                        total_dist_val_km = geodesic(origin_position, destination_position).km
                        distance_traveled_val_km = geodesic(origin_position, current_position).km
                        
                        if distance_units.startswith('imperial'):
                            total_distance = round(total_dist_val_km * 0.621371)
                            distance_traveled = round(distance_traveled_val_km * 0.621371)
                        else:
                            total_distance = round(total_dist_val_km)
                            distance_traveled = round(distance_traveled_val_km)

                        if total_distance > 0:
                            progress_percent = min(round((distance_traveled / total_distance) * 100), 100)
                    
                    flight_details['total_distance'] = total_distance
                    flight_details['distance_traveled'] = distance_traveled
                    flight_details['progress_percent'] = progress_percent
                    
                    self.tracked_flights[flight_id]["last_seen"] = time.time()
                    self._schedule_planespotters_photo(flight, flight_info)

            expired_flight_ids = []
            for flight_id, flight_info in self.tracked_flights.items():
                if flight_id not in currently_visible_ids:
                    if time.time() - flight_info.get("last_seen", 0) > hold_seconds:
                        _LOGGER.debug(f"Flight {flight_id} has expired and will be removed.")
                        expired_flight_ids.append(flight_id)

            self._archive_flights(expired_flight_ids)

            self.update_interval = self._normal_update_interval
            _LOGGER.debug(
                "FR24 poll completed in %.1f seconds (flight list %.1f seconds): "
                "received=%s visible=%s held=%s archived=%s; interval=%.1f seconds",
                time.monotonic() - poll_started_at,
                feed_received_at - poll_started_at,
                len(all_flights),
                len(currently_visible_ids),
                len(self.tracked_flights) - len(currently_visible_ids),
                len(expired_flight_ids),
                self.update_interval.total_seconds(),
            )
            return list(self.tracked_flights.values())

        except Exception as err:
            if _is_rate_limit_error(err):
                rate_limited_at = time.monotonic()
                if not self._rate_limited:
                    self._feed_rate_limit_started_at = rate_limited_at
                    _LOGGER.warning(
                        "FR24 is rate limiting (HTTP 429); moving unverified live flights to history; "
                        "next poll uses the configured interval of %s seconds",
                        self._normal_update_interval.total_seconds(),
                    )
                self._feed_rate_limit_failures += 1
                _LOGGER.debug(
                    "FR24 flight-list still rate limited after %.1f seconds "
                    "(%s consecutive HTTP 429 responses); failed poll took %.1f seconds",
                    rate_limited_at - self._feed_rate_limit_started_at,
                    self._feed_rate_limit_failures,
                    rate_limited_at - poll_started_at,
                )
                self._archive_flights(list(self.tracked_flights))
                self._rate_limited = True
                self.update_interval = self._normal_update_interval
                return list(self.tracked_flights.values())
            raise UpdateFailed(f"Error communicating with API: {err}")
