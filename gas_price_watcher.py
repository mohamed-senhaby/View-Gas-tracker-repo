#!/usr/bin/env python3
"""
German gas price watcher (Tankerkönig API) + Telegram alert
=============================================================
Finds the gas stations nearest to your city/postal code, tracks the
price, and sends you a Telegram message the moment it drops below
the threshold you set.

Before running:
  1) Request a free API key from Tankerkönig:
     https://creativecommons.tankerkoenig.de/
     (arrives by email within a day or two)
  2) Create a Telegram bot via @BotFather and get the token
  3) Get your chat_id (message the bot once, then run:
     get_telegram_chat_id.py, included alongside this script)
  4) Fill in config.json with the above

Run:
    pip install requests
    python gas_price_watcher.py
"""

import json
import os
import sys
import time
from datetime import datetime

import requests

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
TANKERKOENIG_LIST_URL = "https://creativecommons.tankerkoenig.de/json/list.php"
TANKERKOENIG_PRICES_URL = "https://creativecommons.tankerkoenig.de/json/prices.php"
TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_GET_UPDATES_URL = "https://api.telegram.org/bot{token}/getUpdates"


def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(f"❌ Config file not found: {CONFIG_PATH}\nCopy config.example.json and fill it in.")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "already_notified": False,
        "station_ids": None,
        "coords": None,
        "stations": {},
        "telegram_update_offset": None,
        "price_threshold_override": None,
    }


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def geocode_location(location: str):
    """Converts a city name or postal code into (lat, lng) coordinates."""
    params = {"q": f"{location}, Germany", "format": "json", "limit": 1}
    headers = {"User-Agent": "gas-price-watcher/1.0"}
    resp = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        sys.exit(f"❌ Couldn't find coordinates for location: {location}")
    return float(results[0]["lat"]), float(results[0]["lon"])


def find_nearby_stations(api_key: str, lat: float, lng: float, radius_km: float):
    params = {
        "lat": lat,
        "lng": lng,
        "rad": radius_km,
        "sort": "dist",
        "type": "all",
        "apikey": api_key,
    }
    resp = requests.get(TANKERKOENIG_LIST_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        sys.exit(f"❌ Error from Tankerkönig API: {data.get('message', data)}")
    stations = data.get("stations", [])
    if not stations:
        sys.exit("❌ No stations found nearby within that radius — increase radius_km in config.json")
    return stations


def get_prices(api_key: str, station_ids):
    params = {"ids": ",".join(station_ids), "apikey": api_key}
    resp = requests.get(TANKERKOENIG_PRICES_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        sys.exit(f"❌ Error from Tankerkönig API: {data.get('message', data)}")
    return data.get("prices", {})


def send_telegram_message(bot_token: str, chat_id: str, text: str):
    url = TELEGRAM_SEND_URL.format(token=bot_token)
    resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
    if resp.status_code != 200:
        print(f"⚠️ Failed to send Telegram message: {resp.text}")


def poll_threshold_command(config: dict, state: dict):
    """Checks for a /threshold <price> message sent to the bot and, if
    found, overrides the price threshold in state.json. Only messages from
    the configured telegram_chat_id are honored."""
    bot_token = config["telegram_bot_token"]
    chat_id = str(config["telegram_chat_id"])
    offset = state.get("telegram_update_offset")

    url = TELEGRAM_GET_UPDATES_URL.format(token=bot_token)
    params = {"timeout": 0}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        print(f"⚠️ Failed to poll Telegram for commands: {data}")
        return

    updates = data.get("result", [])
    if not updates:
        return

    state["telegram_update_offset"] = updates[-1]["update_id"] + 1

    # First ever poll: just record the offset, don't act on old backlog messages
    if offset is None:
        save_state(state)
        return

    for update in updates:
        message = update.get("message") or update.get("edited_message")
        if not message:
            continue
        if str(message.get("chat", {}).get("id")) != chat_id:
            continue
        text = (message.get("text") or "").strip()
        if not text.lower().startswith("/threshold"):
            continue

        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            send_telegram_message(bot_token, chat_id, "Usage: /threshold <price>, e.g. /threshold 1.70")
            continue
        try:
            new_threshold = float(parts[1].replace(",", "."))
        except ValueError:
            send_telegram_message(bot_token, chat_id, f"Couldn't read '{parts[1]}' as a price.")
            continue

        state["price_threshold_override"] = new_threshold
        state["already_notified"] = False
        send_telegram_message(bot_token, chat_id, f"✅ Threshold set to {new_threshold:.3f} €")
        print(f"🔧 Threshold updated via Telegram: {new_threshold:.3f} €")

    save_state(state)


def describe_station(state: dict, station_id: str) -> str:
    info = state.get("stations", {}).get(station_id)
    if not info:
        return station_id
    name = info.get("name", "").strip()
    address = ", ".join(
        part for part in [info.get("street", "").strip(), info.get("place", "").strip()] if part
    )
    return f"{name} ({address})" if address else name or station_id


def check_prices(config: dict, state: dict):
    fuel_type = config["fuel_type"]  # e5, e10, diesel
    override = state.get("price_threshold_override")
    threshold = float(override) if override is not None else float(config["price_threshold"])
    station_ids = state["station_ids"]

    prices = get_prices(config["tankerkoenig_api_key"], station_ids)

    best_price = None
    best_station_id = None
    for station_id, info in prices.items():
        if not info.get("status") == "open":
            continue
        price = info.get(fuel_type)
        if price is None:
            continue
        if best_price is None or price < best_price:
            best_price = price
            best_station_id = station_id

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    if best_price is None:
        print(f"[{timestamp}] No prices available right now (all stations closed?)")
        return

    best_station_label = describe_station(state, best_station_id)
    print(f"[{timestamp}] Cheapest {fuel_type} price: {best_price:.3f} € (station {best_station_label})")

    if best_price < threshold:
        if not state["already_notified"]:
            message = (
                f"⛽ Price dropped!\n"
                f"{fuel_type.upper()}: {best_price:.3f} €\n"
                f"Your threshold: {threshold:.3f} €\n"
                f"Station: {best_station_label}"
            )
            send_telegram_message(config["telegram_bot_token"], config["telegram_chat_id"], message)
            state["already_notified"] = True
            save_state(state)
            print("✅ Telegram message sent")
    else:
        if state["already_notified"]:
            state["already_notified"] = False
            save_state(state)


def main():
    config = load_config()
    state = load_state()

    # Fetch coordinates and nearby stations once, then keep them cached
    if not state.get("station_ids") or not state.get("stations"):
        lat, lng = geocode_location(config["location"])
        stations = find_nearby_stations(
            config["tankerkoenig_api_key"], lat, lng, config.get("radius_km", 5)
        )
        state["coords"] = [lat, lng]
        state["station_ids"] = [s["id"] for s in stations]
        state["stations"] = {
            s["id"]: {
                "name": s.get("name", ""),
                "street": f"{s.get('street', '')} {s.get('houseNumber', '')}".strip(),
                "place": f"{s.get('postCode', '')} {s.get('place', '')}".strip(),
            }
            for s in stations
        }
        save_state(state)
        print(f"📍 Now tracking {len(stations)} stations near {config['location']}")

    interval_minutes = int(config.get("check_interval_minutes", 15))

    if "--once" in sys.argv:
        poll_threshold_command(config, state)
        check_prices(config, state)
        return

    print(f"🔄 Watching started, checking every {interval_minutes} minute(s)... (Ctrl+C to stop)")
    while True:
        try:
            poll_threshold_command(config, state)
            check_prices(config, state)
        except Exception as e:
            print(f"⚠️ An error occurred: {e}")
        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    main()
