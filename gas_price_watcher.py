#!/usr/bin/env python3
"""
مراقب سعر البنزين في ألمانيا (Tankerkönig API) + إشعار تليجرام
================================================================
بيدور على أقرب محطات بنزين لمدينتك/الرمز البريدي، وبيتابع السعر،
وأول ما ينزل تحت القيمة اللي انت حددها هيبعتلك رسالة على تليجرام.

قبل التشغيل لازم:
  1) تطلب API key مجاني من Tankerkönig:
     https://creativecommons.tankerkoenig.de/
     (بيوصلك بالإيميل خلال يوم أو اتنين)
  2) تعمل بوت تليجرام عن طريق @BotFather وتاخد الـ token
  3) تجيب الـ chat_id بتاعك (اتكلم مع البوت مرة، وبعدين شغّل:
     get_telegram_chat_id.py المرفق مع السكريبت ده)
  4) تملى ملف config.json بالبيانات دي

تشغيل:
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


def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(f"❌ ملف الإعدادات مش موجود: {CONFIG_PATH}\nاعمل نسخة من config.example.json واملأه.")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"already_notified": False, "station_ids": None, "coords": None, "stations": {}}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def geocode_location(location: str):
    """يحول اسم المدينة أو الرمز البريدي لإحداثيات (lat, lng)."""
    params = {"q": f"{location}, Germany", "format": "json", "limit": 1}
    headers = {"User-Agent": "gas-price-watcher/1.0"}
    resp = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        sys.exit(f"❌ مقدرتش ألاقي إحداثيات للمكان: {location}")
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
        sys.exit(f"❌ خطأ من Tankerkönig API: {data.get('message', data)}")
    stations = data.get("stations", [])
    if not stations:
        sys.exit("❌ مفيش محطات قريبة منك في الرادِيوس ده، زوّد radius_km في config.json")
    return stations


def get_prices(api_key: str, station_ids):
    params = {"ids": ",".join(station_ids), "apikey": api_key}
    resp = requests.get(TANKERKOENIG_PRICES_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        sys.exit(f"❌ خطأ من Tankerkönig API: {data.get('message', data)}")
    return data.get("prices", {})


def send_telegram_message(bot_token: str, chat_id: str, text: str):
    url = TELEGRAM_SEND_URL.format(token=bot_token)
    resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
    if resp.status_code != 200:
        print(f"⚠️ فشل إرسال رسالة تليجرام: {resp.text}")


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
    threshold = float(config["price_threshold"])
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
        print(f"[{timestamp}] مفيش أسعار متاحة دلوقتي (كل المحطات مقفولة؟)")
        return

    best_station_label = describe_station(state, best_station_id)
    print(f"[{timestamp}] أرخص سعر {fuel_type}: {best_price:.3f} € (محطة {best_station_label})")

    if best_price < threshold:
        if not state["already_notified"]:
            message = (
                f"⛽ السعر نزل!\n"
                f"{fuel_type.upper()}: {best_price:.3f} €\n"
                f"العتبة اللي انت حددها: {threshold:.3f} €\n"
                f"محطة: {best_station_label}"
            )
            send_telegram_message(config["telegram_bot_token"], config["telegram_chat_id"], message)
            state["already_notified"] = True
            save_state(state)
            print("✅ اتبعت رسالة تليجرام")
    else:
        if state["already_notified"]:
            state["already_notified"] = False
            save_state(state)


def main():
    config = load_config()
    state = load_state()

    # هات الإحداثيات ومحطات القريبة مرة واحدة بس، وبعدين احتفظ بيهم
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
        print(f"📍 هيتم متابعة {len(stations)} محطة حوالين {config['location']}")

    interval_minutes = int(config.get("check_interval_minutes", 15))

    if "--once" in sys.argv:
        check_prices(config, state)
        return

    print(f"🔄 المتابعة شغالة، هيتم الفحص كل {interval_minutes} دقيقة... (Ctrl+C للإيقاف)")
    while True:
        try:
            check_prices(config, state)
        except Exception as e:
            print(f"⚠️ حصل خطأ: {e}")
        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    main()
