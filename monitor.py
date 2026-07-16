import json
import os
import sys
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright


BOOKING_URL = (
    "https://b1009933.yclients.com/company/936902/personal/menu?o="
)

API_URL = (
    "https://platform.yclients.com/api/v1/b2c/booking/"
    "availability/search-timeslots"
)

API_PREFIX = "https://platform.yclients.com/api/v1/b2c/booking/"

LOCATION_ID = 936902
TARGET_TIME = "20:00"

TARGET_DATES = [
    "2026-07-21",
    "2026-07-28",
    "2026-08-04",
    "2026-08-11",
    "2026-08-18",
    "2026-08-25",
]

STATE_FILE = Path(".slot_state.json")

# Эти заголовки нельзя бездумно повторять в отдельном API-запросе.
EXCLUDED_HEADERS = {
    "host",
    "content-length",
    "connection",
    "cookie",
    "accept-encoding",
}


def get_required_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(f"Не задан GitHub Secret: {name}")

    return value


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"dates": {}}

    try:
        state = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return {"dates": {}}

    if not isinstance(state, dict):
        state = {}

    if not isinstance(state.get("dates"), dict):
        state["dates"] = {}

    return state


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def send_telegram(message: str) -> bool:
    try:
        bot_token = get_required_env("TELEGRAM_BOT_TOKEN")
        chat_id = get_required_env("TELEGRAM_CHAT_ID")

        response = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": False,
            },
            timeout=25,
        )

        print("Telegram status:", response.status_code)
        print("Telegram response:", response.text[:500])

        response.raise_for_status()
        return True

    except Exception as exc:
        print("Telegram error:", repr(exc))
        return False


def normalize_time(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    try:
        hour_text, minute_text = value.split(":", 1)

        hour = int(hour_text)
        minute = int(minute_text[:2])

        if not 0 <= hour <= 23:
            return None

        if not 0 <= minute <= 59:
            return None

        return f"{hour:02d}:{minute:02d}"

    except (ValueError, TypeError):
        return None


def extract_bookable_times(data: dict) -> list[str]:
    result: set[str] = set()

    items = data.get("data", [])

    if not isinstance(items, list):
        return []

    for item in items:
        if not isinstance(item, dict):
            continue

        attributes = item.get("attributes", {})

        if not isinstance(attributes, dict):
            continue

        if attributes.get("is_bookable") is not True:
            continue

        normalized = normalize_time(attributes.get("time"))

        if normalized:
            result.add(normalized)

    return sorted(result)


def capture_fresh_headers(page) -> dict[str, str]:
    captured: dict[str, str] = {}

    def handle_request(request) -> None:
        nonlocal captured

        if not request.url.startswith(API_PREFIX):
            return

        try:
            request_headers = request.all_headers()
        except Exception:
            request_headers = request.headers

        authorization = request_headers.get("authorization")
        client_context = request_headers.get("x-app-client-context")

        if not authorization or not client_context:
            return

        clean_headers = {}

        for name, value in request_headers.items():
            lower_name = name.lower()

            if lower_name in EXCLUDED_HEADERS:
                continue

            clean_headers[lower_name] = value

        clean_headers["accept"] = (
            "application/json, text/plain, */*"
        )
        clean_headers["content-type"] = "application/json"
        clean_headers["origin"] = "https://b1009933.yclients.com"
        clean_headers["referer"] = (
            "https://b1009933.yclients.com/"
        )

        captured = clean_headers

        print("Пойманы свежие заголовки из запроса:")
        print(request.url)

    page.on("request", handle_request)

    page.goto(
        BOOKING_URL,
        wait_until="domcontentloaded",
        timeout=60_000,
    )

    page.wait_for_timeout(7_000)

    if not captured:
        print("С первого раза заголовки не пойманы. Перезагрузка.")

        page.reload(
            wait_until="domcontentloaded",
            timeout=60_000,
        )

        page.wait_for_timeout(7_000)

    if not captured:
        # Иногда запрос появляется после открытия одного из пунктов меню.
        labels = [
            "Выбрать корт",
            "Выбрать дату и время",
            "Выбрать услугу",
        ]

        for label in labels:
            try:
                locator = page.get_by_text(label, exact=False)

                if locator.count() > 0:
                    locator.first.click(timeout=5_000)
                    page.wait_for_timeout(5_000)

                if captured:
                    break

            except Exception as exc:
                print(f"Не удалось нажать «{label}»:", repr(exc))

    if not captured:
        raise RuntimeError(
            "Не удалось получить свежие заголовки YCLIENTS"
        )

    return captured


def api_post(
    context,
    headers: dict[str, str],
    payload: dict,
) -> dict:
    response = context.request.post(
        API_URL,
        headers=headers,
        data=payload,
        timeout=30_000,
        fail_on_status_code=False,
    )

    status = response.status
    text = response.text()

    print("YCLIENTS status:", status)
    print("YCLIENTS response preview:", text[:1200])

    if status != 200:
        raise RuntimeError(
            f"YCLIENTS вернул HTTP {status}: {text[:350]}"
        )

    try:
        return json.loads(text)

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "YCLIENTS вернул ответ не в формате JSON"
        ) from exc


def check_all_dates() -> dict[str, dict]:
    results: dict[str, dict] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        context = browser.new_context(
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={
                "width": 1280,
                "height": 900,
            },
        )

        page = context.new_page()

        try:
            headers = capture_fresh_headers(page)

            for target_date in TARGET_DATES:
                print("=" * 60)
                print("Checking date:", target_date)

                payload = {
                    "context": {
                        "location_id": LOCATION_ID,
                    },
                    "filter": {
                        "date": target_date,
                        "records": [
                            {
                                "staff_id": None,
                                "attendance_service_items": [],
                            }
                        ],
                    },
                }

                data = api_post(
                    context,
                    headers,
                    payload,
                )

                found_times = extract_bookable_times(data)
                is_free = TARGET_TIME in found_times

                print("Found times:", found_times)
                print(
                    f"Target {target_date} "
                    f"{TARGET_TIME} free:",
                    is_free,
                )

                results[target_date] = {
                    "is_free": is_free,
                    "found_times": found_times,
                }

        finally:
            context.close()
            browser.close()

    return results


def main() -> int:
    state = load_state()

    try:
        results = check_all_dates()

    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"

        print("MONITOR ERROR:", error_text)

        # Пишем об ошибке один раз, а не каждые пять минут.
        if not state.get("monitor_error"):
            send_telegram(
                "⚠️ Монитор кортов временно не смог "
                "проверить расписание YCLIENTS.\n\n"
                f"Ошибка: {error_text[:350]}"
            )

        state["monitor_error"] = True
        state["last_error"] = error_text[:500]

        save_state(state)

        # GitHub будет зелёным и не станет спамить письмами.
        return 0

    monitor_was_broken = bool(state.get("monitor_error"))
    startup_not_confirmed = not bool(
        state.get("browser_monitor_started")
    )

    state["monitor_error"] = False
    state.pop("last_error", None)

    # После ремонта или первого успешного запуска проверяем Telegram.
    if monitor_was_broken or startup_not_confirmed:
        telegram_ok = send_telegram(
            "✅ Монитор кортов работает.\n\n"
            "Проверяю все вторники до конца августа "
            "на 20:00."
        )

        if telegram_ok:
            state["browser_monitor_started"] = True

    newly_free_dates: list[str] = []

    for target_date, result in results.items():
        previous = state["dates"].get(target_date, {})

        was_free = bool(previous.get("was_free", False))
        is_free = bool(result["is_free"])

        if is_free and not was_free:
            newly_free_dates.append(target_date)

    alert_sent = True

    if newly_free_dates:
        dates_text = "\n".join(
            f"— {target_date} в {TARGET_TIME}"
            for target_date in newly_free_dates
        )

        alert_sent = send_telegram(
            "🚨 КОРТ ОСВОБОДИЛСЯ!\n\n"
            f"{dates_text}\n\n"
            "Беги бронировать:\n"
            f"{BOOKING_URL}"
        )

    for target_date, result in results.items():
        is_free = bool(result["is_free"])

        # Если Telegram не отправил алерт, сохраняем старое
        # состояние, чтобы повторить попытку через пять минут.
        if target_date in newly_free_dates and not alert_sent:
            continue

        state["dates"][target_date] = {
            "was_free": is_free,
            "found_times": result["found_times"],
        }

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
