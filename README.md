# What's that plane?!
A Home Assistant integration made for my partner who enjoys looking up flight information, specifically for planes that pass by her office window.

The unique part about this integration is that it will simulate a cone of vision in a specified direction and only report back flight information within the FOV. This cone of vision acts as the filter for returned flight information rather than an entire circle radius from the defined home position (though this is also still possible by setting your FOV cone to 360°).

Once dialled in, you or your partner can also scream **"WHAT'S THAT PLANE?!"** every time a plane passes by and view a bunch of interesting stats while it's in line of sight. This can quickly become out of hand and you may start collecting sightings of planes' shiny custom livery variants.

The flight data is pulled using the unofficial SDK for FlightRadar24; [FlightRadarAPI](https://github.com/JeanExtreme002/FlightRadarAPI).

Compared with the upstream project, this fork now exposes a broader set of flight attributes including flight number, live status, status icon, airline codes and logo, aircraft ICAO hex, heading compass, origin/destination flag metadata, and last seen timestamps for historic flights.

The exposed sensor information can be used to create interesting dashboard cards such as the example markdown card below:

![Example card](https://raw.githubusercontent.com/8bither0/whats-that-plane/main/example.jpg)

See [Adding visible flight information card to your dashboard](#adding-visible-flight-information-card-to-your-dashboard) below for the template code to add this card to your own dashboard.

## Installation
### HACS via link (Recommended)
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=8bither0&repository=whats-that-plane&category=integration)
1. Click the button above to open the integration in Home Assistant Community Store in Home Assistant.
2. Click `Add`.
3. Click the `Download` button in the bottom right corner.
4. Restart Home Assistant.
5. Now go to your Home Assistant settings and click on `Devices & services`.
6. Click the `Add Integration` button in the bottom right corner and search for `What's that plane?!`.
7. Select `What's that plane?!` and move onto the [Configuration](#configuration) section.

### HACS via custom repositories
1. Go to the Home Assistant Community Store in Home Assistant.
2. Click on the kebab icon in the top right corner and choose `Custom repositories`.
3. In the `Repository` field, enter `https://github.com/LeonArmston/whats-that-plane-leon` and select `Integration` as the `Type`.
4. Click `Add` then close `Custom repositories`.
5. If you now search HACS for `What's that plane?!` you should see the integration in the repository list. Click on the `What's that plane?!` integration.
6. Click the `Download` button in the bottom right corner.
7. Restart Home Assistant.
8. Now go to your Home Assistant settings and click on `Devices & services`.
9. Click the `Add Integration` button in the bottom right corner and search for `What's that plane?!`.
10. Select `What's that plane?!` and move onto the [Configuration](#configuration) section.

### Manual
1. Clone this repository to your local machine.
2. Copy the `custom_components/whats_that_plane` directory to the `custom_components` directory in your Home Assistant file system.
3. Restart Home Assistant.
4. Now go to your Home Assistant settings and click on `Devices & services`.
5. Click the `Add Integration` button in the bottom right corner and search for `What's that plane?!`.
6. Select `What's that plane?!` and move onto the [Configuration](#configuration) section.

## Configuration
To initially configure the integration, define the information below. This can be reconfigured via configuration entry options after initial setup:

| Option                              | Required | Example value                     | Description |
| :-------------------                | :------: | :-----------:                     | :---------- |
| `location_name`                     | ❌       | `Home`                            | A friendly name for your defined coordinates. This will be appended to the integration entry in the format `Visible Flights (Home)`. If empty, the integration entry will simply be called `Visible Flights`. This is useful when defining multiple entries. |
| `latitude`                          | ✅       | `51.5285262`                      | The latitude of your viewing location. This will default to the coordinates defined in your [homeassistant.local:8123/config/zone](http://homeassistant.local:8123/config/zone). |
| `longitude`                         | ✅       | `-0.2663999`                      | The longitude of your viewing location. This will default to the coordinates defined in your [homeassistant.local:8123/config/zone](http://homeassistant.local:8123/config/zone). |
| `radius_km`                         | ✅       | `5`                               | The radius distance boundary from your current location. e.g. `5` = 5km |
| `facing_direction`                  | ✅       | `0`                               | The degree bearing of the viewing direction. e.g. `0` = North, `90` = East, `180` = South, `270` = West. |
| `fov_cone`                          | ✅       | `90`                              | The number of degrees the field of view cone should be. |
| `update_interval`                   | ✅       | `10`                              | The number of seconds between each poll for flight information. |
| `filter_flight_altitude_ft_minimum` | ❌       | `0`                               | The minimum flight altitude in feet for flights to be recorded. |
| `filter_flight_altitude_ft_maximum` | ❌       | `60000`                           | The maximum flight altitude in feet for flights to be recorded. |
| `hold_flight_data_seconds`          | ❌       | `0`                               | The total number of seconds to keep a flight's data after it leaves your field of view. This can act as a grace period before a flight disappears from the sensor. |
| `historic_flights_max_count`        | ❌       | `0`                               | The total number of past flights to store in history. Can be used to show x number of flights that have recently left your field of view. |
| `distance_units`                    | ❌       | `metric (kilometres (km))`        | The unit of measurement to record flight distance in. |
| `altitude_units`                    | ❌       | `imperial (feet (ft))`            | The unit of measurement to record flight altitude in. |
| `speed_units`                       | ❌       | `imperial (miles per hour (mph))` | The unit of measurement to record flight speed in. |

> 💡 **TIP**: To make the initial configuration process easier, you can use the map card to easily visualise your FOV cone settings while you adjust the initial settings. See [Visualising recorded flights on a map card](#visualising-recorded-flights-on-a-map-card).

After configuring the integration, a new sensor named `sensor.visible_flights` will be created. This will update at the frequency defined by the option `update_interval` and list flights visible within your defined FOV cone.

## Sensor data model
The sensor exposes three top-level attributes:

- `config`: your active integration configuration used to calculate visibility and unit conversions.
- `flights`: a list of currently visible flights.
- `historic_flights`: a list of recently visible flights when `historic_flights_max_count` is enabled.

### `config` attributes

| Attribute | Description |
| :-- | :-- |
| `latitude` | Viewing location latitude. |
| `longitude` | Viewing location longitude. |
| `radius_km` | Search radius from the viewing location. |
| `facing_direction` | Bearing used as the centre of the field-of-view cone. |
| `fov_cone` | Width of the field-of-view cone in degrees. |
| `distance_units` | Selected distance unit label used by the integration. |
| `altitude_units` | Selected altitude unit label used by the integration. |
| `speed_units` | Selected speed unit label used by the integration. |

### Flight object attributes
Every entry in `flights` and `historic_flights` can expose the following fields, depending on what FlightRadar24 returns for that aircraft.

#### Identity and status

| Attribute | Description |
| :-- | :-- |
| `callsign` | Flight callsign. |
| `flight_id` | FlightRadar24 flight identifier. |
| `flight_number` | Published flight number. |
| `flightradar_link` | Direct link to the flight on FlightRadar24. |
| `status_live` | Whether the flight is currently marked as live by FlightRadar24. |
| `status_icon` | Status icon value returned by FlightRadar24. |

#### Airline and aircraft

| Attribute | Description |
| :-- | :-- |
| `airline_name` | Airline name. |
| `airline_iata` | Airline IATA code. |
| `airline_icao` | Airline ICAO code. |
| `airline_logo_link` | Derived FlightRadar24 airline logo URL based on the ICAO code. |
| `aircraft_model` | Aircraft model name. |
| `aircraft_type` | Aircraft type code. |
| `aircraft_icao` | Aircraft ICAO hex code. |
| `aircraft_registration` | Aircraft registration. |
| `large_aircraft_image_link` | Large aircraft image URL. |
| `medium_aircraft_image_link` | Medium aircraft image URL. |
| `small_aircraft_image_link` | Small aircraft image URL. |
| `thumbnail_aircraft_image_link` | Thumbnail aircraft image URL. |

#### Position, motion, and route progress

| Attribute | Description |
| :-- | :-- |
| `latitude` | Current aircraft latitude. |
| `longitude` | Current aircraft longitude. |
| `altitude` | Current aircraft altitude in the selected units. |
| `ground_speed` | Current ground speed in the selected units. |
| `ground_speed_kts` | Current ground speed in knots. |
| `heading` | Current heading in degrees. |
| `heading_compass` | Cardinal or intercardinal heading derived from `heading`. |
| `total_distance` | Total route distance in the selected distance units. |
| `distance_traveled` | Distance already traveled in the selected distance units. |
| `progress_percent` | Percent of route completed. |
| `total_flight_time_formatted` | Calculated total flight time as a formatted string. |
| `trail` | Recorded flight trail points used by the map card. |

#### Origin and destination

| Attribute | Description |
| :-- | :-- |
| `origin_city` | Origin city. |
| `origin_country` | Origin country name. |
| `origin_country_code` | Origin country code from FlightRadar24. |
| `origin_country_code_flagsapi` | Origin country code normalised for flag/image usage. |
| `origin_country_code_long` | Origin country long code. |
| `origin_flag_emoji` | Emoji flag derived from the origin country code. |
| `origin_airport_name` | Origin airport name. |
| `origin_airport_code` | Origin airport IATA code. |
| `origin_latitude` | Origin airport latitude. |
| `origin_longitude` | Origin airport longitude. |
| `destination_city` | Destination city. |
| `destination_country` | Destination country name. |
| `destination_country_code` | Destination country code from FlightRadar24. |
| `destination_country_code_flagsapi` | Destination country code normalised for flag/image usage. |
| `destination_country_code_long` | Destination country long code. |
| `destination_flag_emoji` | Emoji flag derived from the destination country code. |
| `destination_airport_name` | Destination airport name. |
| `destination_airport_code` | Destination airport IATA code. |
| `destination_latitude` | Destination airport latitude. |
| `destination_longitude` | Destination airport longitude. |

#### Schedule and delay data

| Attribute | Description |
| :-- | :-- |
| `scheduled_departure_time_local` | Scheduled departure time converted to the origin timezone. |
| `estimated_departure_time_local` | Estimated departure time converted to the origin timezone. |
| `real_departure_time_local` | Actual departure time converted to the origin timezone. |
| `estimated_departure_delay_mins` | Estimated departure delay in minutes. |
| `departure_delay_mins` | Actual departure delay in minutes. |
| `scheduled_arrival_time_local` | Scheduled arrival time converted to the destination timezone. |
| `estimated_arrival_time_local` | Estimated arrival time converted to the destination timezone. |
| `real_arrival_time_local` | Actual arrival time converted to the destination timezone. |
| `estimated_arrival_delay_mins` | Estimated arrival delay in minutes. |
| `arrival_delay_mins` | Actual arrival delay in minutes. |

#### Historic flight fields

| Attribute | Description |
| :-- | :-- |
| `last_seen_timestamp` | Timestamp for when the aircraft was last seen in your FOV. |
| `last_seen_time_formatted` | Human-readable relative last-seen string such as `5m ago`. |

## Adding visible flight information card to your dashboard
The template code required to achieve the card shown in the screenshot above can be found below. To create the card in dashboards that you have control over and are able to add cards to:
1. Click the pencil icon in the top right corner to `Edit dashboard`.
2. Click the `Add card` button in the bottom right corner.
3. Search for and click on the `Manual` card type.
4. Copy and paste the code below into the code text field.
5. Click `Save`.
6. Click `Done` in the top right corner.

> If you'd like to extend this card to show historic flight data of flights that have recently left your FOV cone, complete this section first then ensure that your configuration setting `historic_flights_max_count` is set to `1` or more.

> If you haven't changed the default name of the sensor, you should simply be able to copy and paste the code below and it should work with no changes required. Otherwise, please find and replace every instance of `sensor.visible_flights` with your sensor's name.

```
type: markdown
title: What's that plane?!
content: >-
  {% set flights = state_attr('sensor.visible_flights', 'flights') %}
  {% if flights and flights | count > 0 %}
  {% for flight in flights %}

  {% if flight.callsign == "Blocked" %} 🚫 [**{{ flight.callsign }}**]({{ flight.flightradar_link }})
  {% if flight.aircraft_model %}
  **{{ flight.aircraft_model }}** *({{ flight.aircraft_type }})* | **Registration:** {{ flight.aircraft_registration }}
  {% endif %}
  {%- set image = flight.large_aircraft_image_link or flight.medium_aircraft_image_link or flight.small_aircraft_image_link or flight.thumbnail_aircraft_image_link %}
  {% if image %}
    ![]({{ image }})
  {% endif %}

  {% elif flight.callsign %}
  ✈️ **{{ flight.airline_name }} [**{{ flight.callsign }}**]({{ flight.flightradar_link }}) (**{{ flight.origin_airport_code }} → {{ flight.destination_airport_code }}**)**


  {% if flight.total_distance and flight.total_distance > 0 %}
    {%- set bar_width = 20 -%}
    {%- set plane_pos = max(1, (bar_width * flight.progress_percent / 100) | round | int) -%}
    **{{ flight.origin_country_code_long or flight.origin_country_code }} {{ flight.origin_flag_emoji or flight.origin_airport_code }}** `{{ '─' * (plane_pos - 1) }}✈️{{ '─' * (bar_width - plane_pos) }}` **{{ flight.destination_country_code_long or flight.destination_country_code }} {{ flight.destination_flag_emoji or flight.destination_airport_code }}**
    📏 **Distance:** *{{ flight.distance_traveled }} of {{ flight.total_distance }} {{ state_attr('sensor.visible_flights', 'config')['distance_units'].split('(')[-1] | replace(')', '') }} ({{ flight.progress_percent }}%)*
    📈 **Altitude:** {{ flight.altitude | default(0, true) | round(0) }} {{ state_attr('sensor.visible_flights', 'config')['altitude_units'].split('(')[-1] | replace(')', '') }} | **Speed:** {{ flight.ground_speed | default(0, true) | round(0) }} {{ state_attr('sensor.visible_flights', 'config')['speed_units'].split('(')[-1] | replace(')', '') }} | **Heading:** {{ flight.heading_compass or flight.heading }}
    {% if flight.total_flight_time_formatted %} 🕑 **Total Flight Time:** {{ flight.total_flight_time_formatted }}
    {% endif %}
  {% endif %}

  {% if flight.origin_city or flight.origin_country or flight.destination_city or flight.destination_country or flight.origin_airport_name or flight.destination_airport_name %}
    🌍 {{ flight.origin_city }}, _**{{ flight.origin_country }}**_ → {{ flight.destination_city }}, _**{{ flight.destination_country }}**_
    🛂 {{ flight.origin_airport_name | replace('Airport', '') | trim }} → {{ flight.destination_airport_name | replace('Airport', '') | trim }}
  {% endif %}

  {% if flight.scheduled_departure_time_local %} {% set departure_delay = flight.departure_delay_mins if flight.departure_delay_mins is not none else flight.estimated_departure_delay_mins %}
  🛫 **Scheduled Departure:** {{ flight.scheduled_departure_time_local }}
  {% if departure_delay is not none %}
  {% if departure_delay > 0 %}
    - ⚠️ **Delayed: {{ departure_delay }} minutes**
  {% elif departure_delay < 0 %}
    - ✅ **Early: {{ departure_delay | abs }} minutes**
  {% endif %}
  {% endif %}
  {% if flight.real_departure_time_local %}
    - **Actual Departure:** {{ flight.real_departure_time_local }}
  {% elif flight.estimated_departure_time_local %}
    - **Estimated Departure:** {{ flight.estimated_departure_time_local }}
  {% endif %}
  {% endif %}

  {% if flight.scheduled_arrival_time_local %} {% set arrival_delay = flight.arrival_delay_mins if flight.arrival_delay_mins is not none else flight.estimated_arrival_delay_mins %}
  🛬 **Scheduled Arrival:** {{ flight.scheduled_arrival_time_local }}
  {% if arrival_delay is not none %}
  {% if arrival_delay > 0 %}
    - ⚠️ **Delayed: {{ arrival_delay }} minutes**
  {% elif arrival_delay < 0 %}
    - ✅ **Early: {{ arrival_delay | abs }} minutes**
  {% endif %} {% endif %} {% if flight.real_arrival_time_local %}
    - **Actual Arrival:** {{ flight.real_arrival_time_local }}
  {% elif flight.estimated_arrival_time_local %}
    - **Estimated Arrival:** {{ flight.estimated_arrival_time_local }}
  {% endif %} {% endif %}

  {% if flight.aircraft_model %}
    **{{ flight.aircraft_model }}** *({{ flight.aircraft_type }})* | **Registration:** {{ flight.aircraft_registration }} {% endif %}
  {%- set image = flight.large_aircraft_image_link or flight.medium_aircraft_image_link or flight.small_aircraft_image_link or flight.thumbnail_aircraft_image_link %}
  {% if image %}
    ![]({{ image }})
  {% endif %}

  ***
  
  {% endif %}
  {% endfor %}
  {% else %}
    No visible flights at the moment.
  {% endif %}
```

## Viewing historic flight information
If your configuration setting for `historic_flights_max_count` is set to 1 or more, you can utilise the `historic_flights` attribute to view a log of flights that have left your defined FOV cone.

After you've already configured your main dashboard card (see [Adding visible flight information card to your dashboard](#adding-visible-flight-information-card-to-your-dashboard)), you can extend the existing card with the template below.

This section basically clones the existing card but only shows it for historic flights. The only real change is the addition of a last seen time on the title line for each flight.

**N.B.** It's important to note that when a plane leaves your defined FOV cone, its flight information will stop updating as the integration stops tracking the flight at this point. Stats are correct as of the aircraft's last visible position.

![Example history](https://raw.githubusercontent.com/8bither0/whats-that-plane/main/example_history.jpg)

> If you haven't changed the default name of the sensor, you should simply be able to copy and paste the code below and it should work with no changes required. Otherwise, please find and replace every instance of `sensor.visible_flights` with your sensor's name.

```


  ***
  
  # What was that plane?!

  {% set historic_flights = state_attr('sensor.visible_flights', 'historic_flights') %}
  {% if historic_flights and historic_flights | count > 0 %}
  {% for flight in historic_flights %}

  {% if flight.callsign == "Blocked" %} 🚫 [**{{ flight.callsign }}**]({{ flight.flightradar_link }})
  {% if flight.aircraft_model %}
  **{{ flight.aircraft_model }}** *({{ flight.aircraft_type }})* | **Registration:** {{ flight.aircraft_registration }}
  {% endif %}
  {%- set image = flight.large_aircraft_image_link or flight.medium_aircraft_image_link or flight.small_aircraft_image_link or flight.thumbnail_aircraft_image_link %}
  {% if image %}
    ![]({{ image }})
  {% endif %}

  {% elif flight.callsign %}
  ✈️ **{{ flight.airline_name }} [**{{ flight.callsign }}**]({{ flight.flightradar_link }}) (**{{ flight.origin_airport_code }} → {{ flight.destination_airport_code }}**)** {% if flight.last_seen_time_formatted %}_(Last seen {{ flight.last_seen_time_formatted }})_{% endif %}


  {% if flight.total_distance and flight.total_distance > 0 %}
    {%- set bar_width = 20 -%}
    {%- set plane_pos = max(1, (bar_width * flight.progress_percent / 100) | round | int) -%}
    **{{ flight.origin_country_code_long or flight.origin_country_code }} {{ flight.origin_flag_emoji or flight.origin_airport_code }}** `{{ '─' * (plane_pos - 1) }}✈️{{ '─' * (bar_width - plane_pos) }}` **{{ flight.destination_country_code_long or flight.destination_country_code }} {{ flight.destination_flag_emoji or flight.destination_airport_code }}**
    📏 **Distance:** *{{ flight.distance_traveled }} of {{ flight.total_distance }} {{ state_attr('sensor.visible_flights', 'config')['distance_units'].split('(')[-1] | replace(')', '') }} ({{ flight.progress_percent }}%)*
    📈 **Altitude:** {{ flight.altitude | default(0, true) | round(0) }} {{ state_attr('sensor.visible_flights', 'config')['altitude_units'].split('(')[-1] | replace(')', '') }} | **Speed:** {{ flight.ground_speed | default(0, true) | round(0) }} {{ state_attr('sensor.visible_flights', 'config')['speed_units'].split('(')[-1] | replace(')', '') }} | **Heading:** {{ flight.heading_compass or flight.heading }}
    {% if flight.total_flight_time_formatted %} 🕑 **Total Flight Time:** {{ flight.total_flight_time_formatted }}
    {% endif %}
  {% endif %}

  {% if flight.origin_city or flight.origin_country or flight.destination_city or flight.destination_country or flight.origin_airport_name or flight.destination_airport_name %}
    🌍 {{ flight.origin_city }}, _**{{ flight.origin_country }}**_ → {{ flight.destination_city }}, _**{{ flight.destination_country }}**_
    🛂 {{ flight.origin_airport_name | replace('Airport', '') | trim }} → {{ flight.destination_airport_name | replace('Airport', '') | trim }}
  {% endif %}

  {% if flight.scheduled_departure_time_local %} {% set departure_delay = flight.departure_delay_mins if flight.departure_delay_mins is not none else flight.estimated_departure_delay_mins %}
  🛫 **Scheduled Departure:** {{ flight.scheduled_departure_time_local }}
  {% if departure_delay is not none %}
  {% if departure_delay > 0 %}
    - ⚠️ **Delayed: {{ departure_delay }} minutes**
  {% elif departure_delay < 0 %}
    - ✅ **Early: {{ departure_delay | abs }} minutes**
  {% endif %}
  {% endif %}
  {% if flight.real_departure_time_local %}
    - **Actual Departure:** {{ flight.real_departure_time_local }}
  {% elif flight.estimated_departure_time_local %}
    - **Estimated Departure:** {{ flight.estimated_departure_time_local }}
  {% endif %}
  {% endif %}

  {% if flight.scheduled_arrival_time_local %} {% set arrival_delay = flight.arrival_delay_mins if flight.arrival_delay_mins is not none else flight.estimated_arrival_delay_mins %}
  🛬 **Scheduled Arrival:** {{ flight.scheduled_arrival_time_local }}
  {% if arrival_delay is not none %}
  {% if arrival_delay > 0 %}
    - ⚠️ **Delayed: {{ arrival_delay }} minutes**
  {% elif arrival_delay < 0 %}
    - ✅ **Early: {{ arrival_delay | abs }} minutes**
  {% endif %} {% endif %} {% if flight.real_arrival_time_local %}
    - **Actual Arrival:** {{ flight.real_arrival_time_local }}
  {% elif flight.estimated_arrival_time_local %}
    - **Estimated Arrival:** {{ flight.estimated_arrival_time_local }}
  {% endif %} {% endif %}

  {% if flight.aircraft_model %}
    **{{ flight.aircraft_model }}** *({{ flight.aircraft_type }})* | **Registration:** {{ flight.aircraft_registration }} {% endif %}
  {%- set image = flight.large_aircraft_image_link or flight.medium_aircraft_image_link or flight.small_aircraft_image_link or flight.thumbnail_aircraft_image_link %}
  {% if image %}
    ![]({{ image }})
  {% endif %}

  ***
  
  {% endif %}
  {% endfor %}
  {% else %}
    No recent flight history.
  {% endif %}
```

## Visualising recorded flights on a map card
It's possible to visualise recorded flights and their flight trails on a map card to achieve the map card shown in the video demonstration below. This is a great way to make your dashboard more interactive.

https://github.com/user-attachments/assets/43a910b3-c2c1-41b1-8d23-74874c7dbaf3

> ⚠️ Ensure that you have at least one configured entry before trying to use the map card.

To add the map card to dashboards that you have control over and are able to add cards to:
1. Click the pencil icon in the top right corner to `Edit dashboard`.
2. Click the `Add card` button in the bottom right corner.
3. Search for and click on the `Manual` card type.
4. Copy and paste the code below into the code text field.
5. Click `Save`.
6. Click `Done` in the top right corner.

> If you haven't changed the default name of the sensor, you should simply be able to copy and paste the code below and it should work with no changes required. Otherwise, please find and replace every instance of `sensor.visible_flights` with your sensor's name.

```
square: true
type: grid
cards:
  - type: custom:whats-that-plane-map
    entity: sensor.visible_flights
columns: 1
grid_options:
  columns: full
```

> 💡 **TIP**: To make the initial configuration process easier, you can use the map card to easily visualise your FOV cone settings.
>
> ![Example map](https://raw.githubusercontent.com/8bither0/whats-that-plane/main/example_map.jpg)
>
> Once the map card is added to your dashboard, simply change your configuration settings then refer back to the dashboard card to view how your edits change the FOV cone **(you will need to refresh the dashboard after each configuration change)**.

**N.B.** It's important to note that when a plane leaves your defined FOV cone, its flight information will stop updating as the integration stops tracking the flight at this point. Stats are correct as of the aircraft's last visible position.

## Support
This was a fun little weekend project and I'm unlikely to actively support this. However, if you encounter any issues or have questions, please open an [issue](https://github.com/LeonArmston/whats-that-plane-leon/issues).

## License
This project is licensed under the MIT License. See the [LICENSE](https://github.com/LeonArmston/whats-that-plane-leon/blob/main/LICENSE) file for details.
