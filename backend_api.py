import time
from datetime import datetime
from threading import Lock

import requests
from flask import Flask, jsonify, render_template

app = Flask(__name__, template_folder="templates")

CATCHMENT_REGIONS = {
    "shimla_01": {
        "name": "Shimla Catchment Matrix",
        "lat": 31.1048,
        "lng": 77.1734,
        "base_slope": 38.5,
        "sensor_nodes": 14,
    },
    "manali_02": {
        "name": "Manali Valley Core",
        "lat": 32.2396,
        "lng": 77.1887,
        "base_slope": 42.1,
        "sensor_nodes": 22,
    },
    "dharamshala_03": {
        "name": "Dharamshala Escarpment",
        "lat": 32.2190,
        "lng": 76.3234,
        "base_slope": 34.8,
        "sensor_nodes": 18,
    },
}

# Cache weather API results for 10 minutes so browser refreshes do not
# trigger a new upstream request every 60 seconds.
WEATHER_CACHE = {}
WEATHER_CACHE_LOCK = Lock()
WEATHER_CACHE_SECONDS = 600


def compute_accelerated_hazard_index(rain, saturation, runoff, slope):
    """Prototype weighted hazard index; not a validated flood prediction model."""
    weighted_sum = (
        rain * 0.45
        + saturation * 0.25
        + runoff * 0.20
        + slope * 0.10
    )
    if rain > 80:
        weighted_sum += (rain - 80) * 0.5
    return min(round(weighted_sum, 2), 100.00)


def fetch_weather(lat, lng):
    """Fetch current precipitation and hourly forecast from Open-Meteo."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lng,
        "current": "precipitation,rain",
        "hourly": "precipitation,rain",
        "forecast_days": 1,
        "timezone": "Asia/Kolkata",
    }
    response = requests.get(url, params=params, timeout=12)
    response.raise_for_status()
    data = response.json()

    current = data.get("current", {})
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    precipitation = hourly.get("precipitation", [])
    hourly_rain = hourly.get("rain", [])
    current_time = current.get("time")

    # Find the current hour in the hourly forecast; fall back to the first
    # available forecast hour if the API timestamps do not match exactly.
    try:
        index = times.index(current_time)
    except (ValueError, TypeError):
        index = 0

    next_6_hours = sum(
        float(value or 0) for value in precipitation[index:index + 6]
    )

    return {
        "current_precipitation_mm": current.get("precipitation"),
        "current_rain_mm": current.get("rain"),
        "next_6_hours_precipitation_mm": round(next_6_hours, 2),
        "hourly_times": times[index:index + 12],
        "hourly_precipitation_mm": precipitation[index:index + 12],
        "hourly_rain_mm": hourly_rain[index:index + 12],
        "weather_time": current_time,
        "timezone": data.get("timezone", "Asia/Kolkata"),
        "source": "Open-Meteo forecast API",
    }


def get_weather_cached(region_id, lat, lng):
    now = time.time()
    with WEATHER_CACHE_LOCK:
        cached = WEATHER_CACHE.get(region_id)
        if cached and now - cached["cached_at"] < WEATHER_CACHE_SECONDS:
            return cached["data"]

    weather = fetch_weather(lat, lng)
    with WEATHER_CACHE_LOCK:
        WEATHER_CACHE[region_id] = {"data": weather, "cached_at": now}
    return weather


@app.route("/")
def render_terminal():
    return render_template("frontend_ui.html")


@app.route("/api/v1/rainfall")
def get_rainfall():
    """Automatic rainfall and forecast data for all configured locations."""
    results = []
    for region_id, region in CATCHMENT_REGIONS.items():
        try:
            weather = get_weather_cached(
                region_id, region["lat"], region["lng"]
            )
            results.append({
                "id": region_id,
                "region_name": region["name"],
                "coordinates": {
                    "lat": region["lat"],
                    "lng": region["lng"],
                },
                "status": "LIVE",
                **weather,
            })
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            results.append({
                "id": region_id,
                "region_name": region["name"],
                "status": "UNAVAILABLE",
                "error": str(error),
            })

    return jsonify({
        "updated_at": datetime.now().astimezone().isoformat(),
        "refresh_interval_seconds": WEATHER_CACHE_SECONDS,
        "source": "Open-Meteo forecast API",
        "regions": results,
    })


@app.route("/api/v1/telemetry/nominal")
def get_nominal_stream():
    """Demo telemetry; rain, soil and stream values here remain simulated."""
    timestamp = float(time.time())
    metrics = [
        {"id": "shimla_01", "rain": 24.5, "sat": 42.1, "flow": 1.8},
        {"id": "manali_02", "rain": 38.2, "sat": 58.4, "flow": 2.9},
        {"id": "dharamshala_03", "rain": 14.1, "sat": 31.8, "flow": 1.2},
    ]
    payload = []
    for node in metrics:
        meta = CATCHMENT_REGIONS[node["id"]]
        risk = compute_accelerated_hazard_index(
            node["rain"], node["sat"], node["flow"], meta["base_slope"]
        )
        payload.append({
            "uuid": node["id"],
            "region_name": meta["name"],
            "coordinates": {"lat": meta["lat"], "lng": meta["lng"]},
            "hardware_profile": "NVIDIA Jetson Orin Nano (prototype target)",
            "telemetry": {
                "rainfall_nowcast_mm": node["rain"],
                "soil_saturation_pct": node["sat"],
                "river_gauge_velocity_ms": node["flow"],
            },
            "hazard_vector_index": risk,
            "system_status": "NOMINAL" if risk < 70 else "CRITICAL",
            "cycle_latency_ms": 0.42,
        })
    return jsonify({"timestamp": timestamp, "stream_profile": "SIMULATED", "nodes": payload})


@app.route("/api/v1/telemetry/cloudburst")
def get_critical_stream():
    """Simulates a cloudburst scenario for the frontend demo."""
    timestamp = float(time.time())
    meta_shimla = CATCHMENT_REGIONS["shimla_01"]
    critical_risk = compute_accelerated_hazard_index(
        114.8, 94.2, 8.7, meta_shimla["base_slope"]
    )
    payload = [{
        "uuid": "shimla_01",
        "region_name": meta_shimla["name"] + " [CLOUDBURST SIMULATION]",
        "coordinates": {"lat": meta_shimla["lat"], "lng": meta_shimla["lng"]},
        "hardware_profile": "NVIDIA Jetson Orin Nano (prototype target)",
        "telemetry": {
            "rainfall_nowcast_mm": 114.8,
            "soil_saturation_pct": 94.2,
            "river_gauge_velocity_ms": 8.7,
        },
        "hazard_vector_index": critical_risk,
        "system_status": "CRITICAL",
        "cycle_latency_ms": 0.58,
    }]
    for uid in ["manali_02", "dharamshala_03"]:
        meta = CATCHMENT_REGIONS[uid]
        risk = compute_accelerated_hazard_index(15.0, 40.0, 1.5, meta["base_slope"])
        payload.append({
            "uuid": uid,
            "region_name": meta["name"],
            "coordinates": {"lat": meta["lat"], "lng": meta["lng"]},
            "hardware_profile": "NVIDIA Jetson Orin Nano (prototype target)",
            "telemetry": {
                "rainfall_nowcast_mm": 15.0,
                "soil_saturation_pct": 40.0,
                "river_gauge_velocity_ms": 1.5,
            },
            "hazard_vector_index": risk,
            "system_status": "NOMINAL",
            "cycle_latency_ms": 0.41,
        })
    return jsonify({"timestamp": timestamp, "stream_profile": "SIMULATED_CLOUDBURST", "nodes": payload})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
