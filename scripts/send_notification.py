import json
import os
import requests
from datetime import datetime, timedelta
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


def load_signal_fallback(video_id):
    if not video_id:
        return {}

    signal_path = Path("data/signals") / f"{video_id}.json"
    if not signal_path.exists():
        return {}

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
    # 指向 daily_analyst_table.py 產出的摘要檔
    summary_path = Path("data/reports/daily_run_summary.json")
    debug_print(f"CWD: {Path.cwd()}")
    debug_print(f"Summary path: {summary_path.resolve()}")
    debug_print(f"Summary exists: {summary_path.exists()}")
    
    if not summary_path.exists():
        msg = "⚠️ 每日排程執行失敗：找不到摘要檔案。"
    else:
        with open(summary_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        completed_at_raw = data.get("completed_at")
        completed_dt = parse_datetime(completed_at_raw)
        if completed_dt is None:
            completed_dt = datetime.fromtimestamp(summary_path.stat().st_mtime)

        window_end_raw = data.get("window_end")
        window_start_raw = data.get("window_start")
        window_end_dt = parse_datetime(window_end_raw) or completed_dt
        window_start_dt = parse_datetime(window_start_raw) or (window_end_dt - timedelta(days=1))

        tracking_list_count = data.get("tracking_list_count")
        updated_video_total = data.get("updated_video_total")
        processed_video_total = data.get("processed_video_total")
        completed_at = format_datetime(completed_dt.isoformat())
        window_start = format_datetime(window_start_dt.isoformat())
        window_end = format_datetime(window_end_dt.isoformat())

        count = data.get("count", 0)
        items = data.get("items", [])
        debug_print(f"Summary count: {count}")
        debug_print(f"Summary items: {len(items)}")

        if tracking_list_count is None:
            tracking_list_count = len({item.get("analyst") for item in items if item.get("analyst")})
        if updated_video_total is None:
            updated_video_total = len(items)
        if processed_video_total is None:
            processed_video_total = len(items)
        
        msg = "每日分析師影片處理摘要\n"
        msg += f"0) 完成時間: {completed_at}\n"
        msg += (
            f"1) 追蹤名單: {tracking_list_count if tracking_list_count is not None else 'N/A'} 位, "
            f"24小時更新總影片數: {updated_video_total if updated_video_total is not None else len(items)} 部 "
            f"(範圍: {window_start} ~ {window_end})\n"
        )
        msg = "\n"

        msg += f"2) 總處理影片數: {processed_video_total if processed_video_total is not None else len(items)}\n\n"
        msg = "\n"
        
        msg += "3) 逐支影片摘要\n"
        
        for idx, item in enumerate(items, start=1):
            status_icon = "OK" if item.get('status') == 'ok' else "FAIL"
            analyst = item.get('analyst', 'Unknown')
            status = item.get('status', 'unknown')
            video_id = item.get('video_id')
            video_title = item.get('video_title')
            video_url = (
                f"https://youtube.com/watch?v={video_id}"
                if video_id
                else "N/A"
            )

            fallback = load_signal_fallback(video_id)
            if not video_title and video_id:
                video_title = fetch_video_title(video_id)

            view_count = item.get('video_view_count', fallback.get("video_view_count", 'N/A'))
            recommended_count = item.get('recommended_count', fallback.get("recommended_count", 0))
            not_recommended_count = item.get('not_recommended_count', fallback.get("not_recommended_count", 0))

            msg += f"{idx}. [{status_icon}] {analyst} ({status})\n"
            msg += f"   標題: {video_title or 'N/A'}\n"
            msg += f"   網址: {video_url}\n"
            msg += f"   觀看數: {view_count}\n"
            msg += f"   推薦數: {recommended_count}, 不推薦數: {not_recommended_count}\n"
            if item.get('error'):
                msg += f"   錯誤: {item['error']}\n"

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
