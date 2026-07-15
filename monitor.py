import json
import os
import re
import sys
from pathlib import Path

import requests


URL = "https://platform.yclients.com/api/v1/b2c/booking/availability/search-timeslots"

LOCATION_ID = 936902
TARGET_DATE = "2026-07-21"
TARGET_TIME = "20:00"

STATE_FILE = Path(".slot_state.json")


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing env var: {name}")
        sys.exit(1)
    return value


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def walk_strings(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from walk_strings(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk_strings(item)
    elif obj is not None:
        yield str(obj)


def send_telegram(message: str) -> None:
    bot_token = get_required_env("TELEGRAM_BOT_TOKEN")
    chat_id = get_required_env("TELEGRAM_CHAT_ID")

    r = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )

    print("Telegram status:", r.status_code)
    print("Telegram response:", r.text[:500])
    r.raise_for_status()


def main() -> None:
    yclients_bearer = get_required_env("YCLIENTS_BEARER")
    yclients_context = get_required_env("YCLIENTS_CONTEXT")

    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru-RU",
        "authorization": f"Bearer {yclients_bearer}",
        "content-type": "application/json",
        "origin": "https://b1009933.yclients.com",
        "referer": "https://b1009933.yclients.com/",
        "priority": "u=1, i",
        "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
        "x-app-client-context": yclients_context,
        "x-app-client-context-analytics-udid": "24284246-dc85-4d77-8367-01cd116d94ae",
        "x-app-client-context-version": "2",
        "x-app-signature": "",
        "x-yclients-application-action": "",
        "x-yclients-application-name": "client.booking",
        "x-yclients-application-platform": "angular-18.2.13",
        "x-yclients-application-version": "1284397.b9c480ff",
    }

    payload = {
        "context": {
            "location_id": LOCATION_ID,
        },
        "filter": {
            "date": TARGET_DATE,
            "records": [
                {
                    "staff_id": None,
                    "attendance_service_items": [],
                }
            ],
        },
    }

    r = requests.post(URL, headers=headers, json=payload, timeout=30)

    print("YCLIENTS status:", r.status_code)
    print("YCLIENTS response preview:", r.text[:1500])

    r.raise_for_status()

    data = r.json()
    all_text = "\n".join(walk_strings(data))

    found_times = sorted(set(re.findall(r"\b(?:[01]\d|2[0-3]):[0-5]\d\b", all_text)))

    time_pattern = rf"(?<!\d){re.escape(TARGET_TIME)}(?::00)?(?!\d)"
    is_free = bool(re.search(time_pattern, all_text))

    print("Found times:", found_times)
    print(f"Target {TARGET_DATE} {TARGET_TIME} free:", is_free)

    state = load_state()
    was_free = state.get("was_free", False)

    if is_free and not was_free:
        send_telegram(
            "🚨 КОРТ ОСВОБОДИЛСЯ!\n\n"
            f"Дата: {TARGET_DATE}\n"
            f"Время: {TARGET_TIME}\n\n"
            "Беги бронировать:\n"
            "https://b1009933.yclients.com/company/936902/personal/menu?o="
        )

    state["was_free"] = is_free
    state["last_checked"] = f"{TARGET_DATE} {TARGET_TIME}"
    state["found_times"] = found_times

    save_state(state)


if __name__ == "__main__":
    main()
