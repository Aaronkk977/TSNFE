import json
import os
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# 載入 .env 中的 API Keys
load_dotenv()


def debug_print(message):
    print(f"[DEBUG] {message}", flush=True)


def format_datetime(value):
    if not value:
        return "N/A"
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def fetch_video_title(video_id):
    if not video_id:
        return None
    try:
        url = "https://www.youtube.com/oembed"
        params = {
            "url": f"https://youtube.com/watch?v={video_id}",
            "format": "json",
        }
        response = requests.get(url, params=params, timeout=15)
        if response.ok:
            return response.json().get("title")
    except Exception:
        return None
    return None


def _find_latest_daily_summary() -> Path:
    summary_files = sorted(
        Path("data/reports/daily").glob("*/daily_run_summary_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not summary_files:
        raise FileNotFoundError("No daily summary file found under data/reports/daily")
    return summary_files[0]


def load_signal_fallback(video_id):
    if not video_id:
        return {}

    signal_candidates = sorted(
        Path("data/signals/daily").glob(f"*/{video_id}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not signal_candidates:
        return {}
    signal_path = signal_candidates[0]

    try:
        with open(signal_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return {}

    recommended_count = 0
    not_recommended_count = 0
    for signal in payload.get("signals", []):
        label = signal.get("normalized_label") or signal.get("implied_label")
        if label == "買進":
            recommended_count += 1
        elif label == "賣出":
            not_recommended_count += 1

    return {
        "video_view_count": payload.get("video_view_count"),
        "recommended_count": recommended_count,
        "not_recommended_count": not_recommended_count,
    }


def to_int_or_default(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default

def send_to_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    debug_print(f"Telegram token set: {bool(token)}")
    debug_print(f"Telegram chat_id set: {bool(chat_id)}")
    if not token or not chat_id:
        debug_print("Skip Telegram: missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    debug_print(f"Telegram payload length: {len(message)}")
    response = requests.post(
        url,
        data={"chat_id": chat_id, "text": message},
        timeout=30,
    )
    debug_print(f"Telegram status: {response.status_code}")
    debug_print(f"Telegram response: {response.text[:500]}")
    if not response.ok:
        raise RuntimeError(f"Telegram request failed with status {response.status_code}")

def send_to_discord(message):
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    debug_print(f"Discord webhook set: {bool(webhook_url)}")
    if not webhook_url:
        debug_print("Skip Discord: missing DISCORD_WEBHOOK_URL")
        return
    response = requests.post(webhook_url, json={"content": message}, timeout=30)
    debug_print(f"Discord status: {response.status_code}")
    debug_print(f"Discord response: {response.text[:500]}")
    if not response.ok:
        raise RuntimeError(f"Discord request failed with status {response.status_code}")

def main():
    summary_path = _find_latest_daily_summary()
    debug_print(f"CWD: {Path.cwd()}")
    debug_print(f"Summary path: {summary_path.resolve()}")
    debug_print(f"Summary exists: {summary_path.exists()}")

    with open(summary_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    completed_dt = parse_datetime(data.get("completed_at"))
    window_start_dt = parse_datetime(data.get("window_start"))
    window_end_dt = parse_datetime(data.get("window_end"))

    if completed_dt is None or window_start_dt is None or window_end_dt is None:
        raise ValueError("Invalid summary format: missing completed/window timestamps")

    tracking_list_count = data.get("tracking_list_count")
    updated_video_total = data.get("updated_video_total")
    processed_video_total = data.get("processed_video_total")
    items = data.get("items", [])

    if tracking_list_count is None or updated_video_total is None or processed_video_total is None:
        raise ValueError("Invalid summary format: missing required counters")

    debug_print(f"Summary items: {len(items)}")

    msg = "每日分析師影片處理摘要\n"
    msg += f"0) 完成時間: {format_datetime(completed_dt.isoformat())}\n"
    msg += (
        f"1) 追蹤名單: {tracking_list_count} 位, "
        f"24小時更新總影片數: {updated_video_total} 部 "
        f"(範圍: {format_datetime(window_start_dt.isoformat())} ~ {format_datetime(window_end_dt.isoformat())})\n"
    )
    msg += f"2) 總處理影片數: {processed_video_total}\n\n"
    msg += "3) 逐支影片摘要\n"

    fallback_cache = {}

    def get_fallback(video_id):
        if not video_id:
            return {}
        if video_id not in fallback_cache:
            fallback_cache[video_id] = load_signal_fallback(video_id)
        return fallback_cache[video_id]

    def get_view_count(item):
        direct = item.get("video_view_count")
        if direct is not None:
            return to_int_or_default(direct, 0)
        fallback = get_fallback(item.get("video_id"))
        return to_int_or_default(fallback.get("video_view_count"), 0)

    video_items = [item for item in items if item.get("video_id")]
    sorted_items = sorted(video_items, key=get_view_count, reverse=True)

    display_items = sorted_items[:10]
    for idx, item in enumerate(display_items, start=1):
        status_icon = "OK" if item.get("status") == "ok" else "FAIL"
        analyst = item.get("analyst", "Unknown")
        status = item.get("status", "unknown")
        video_id = item.get("video_id")
        video_title = item.get("video_title")
        video_url = f"https://youtube.com/watch?v={video_id}" if video_id else "N/A"

        fallback = get_fallback(video_id)
        if not video_title and video_id:
            video_title = fetch_video_title(video_id)

        view_count = item.get("video_view_count")
        if view_count is None:
            view_count = fallback.get("video_view_count", "N/A")

        recommended_count = item.get("recommended_count")
        if recommended_count is None:
            recommended_count = fallback.get("recommended_count", 0)

        not_recommended_count = item.get("not_recommended_count")
        if not_recommended_count is None:
            not_recommended_count = fallback.get("not_recommended_count", 0)

        msg += f"{idx}. [{status_icon}] {analyst} ({status})\n"
        msg += f"   標題: {video_title or 'N/A'}\n"
        msg += f"   網址: {video_url}\n"
        msg += f"   觀看數: {view_count}\n"
        msg += f"   推薦數: {recommended_count}, 不推薦數: {not_recommended_count}\n"
        if item.get("error"):
            msg += f"   錯誤: {item['error']}\n"

    if len(video_items) > 10:
        msg += f"\n... 後面還有 {max(0, len(video_items) - 10)} 部影片，但因長度而截斷。\n"

    debug_print(f"Message preview: {msg[:500]}")

    # 發送通知
    try:
        send_to_telegram(msg)
    except Exception as e:
        debug_print(f"Telegram send failed: {e}")

    try:
        send_to_discord(msg)
    except Exception as e:
        debug_print(f"Discord send failed: {e}")

if __name__ == "__main__":
    main()
