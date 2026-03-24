# QUICKSTART

## 1. 建立與啟用環境

```bash
conda create -n tw-analyst python=3.10 -y
conda activate tw-analyst
pip install -r requirements-dev.txt
pip install -e .
```

## 2. 設定 API Keys

```bash
cp .env.example .env
# 編輯 .env
```

至少填入：

```bash
YOUTUBE_API_KEY=...
GOOGLE_API_KEY=...
FUGLE_API_KEY=...
LLM_PROVIDER=gemini
TRANSCRIPTION_PROVIDER=gemini
```

## 3. 驗證系統

```bash
python scripts/test_system.py
```

## 4. 跑第一支影片

```bash
python scripts/process_video.py "https://www.youtube.com/watch?v=VIDEO_ID"

# 三種模式
python scripts/process_video.py "VIDEO_ID" --mode audio
python scripts/process_video.py "VIDEO_ID" --mode url
python scripts/process_video.py "VIDEO_ID" --mode text --text-source auto
```

## 5. 主要輸出位置

- `data/signals/{video_id}.json`
- `data/signals/recommendation_list.json`
- `data/transcripts/{video_id}.json`（若走 transcript 流程）
- `data/errors/failed_processing.json`
- `data/errors/failed_downloads.json`

## 6. 頻道批次抓取（可選）

```bash
python scripts/fetch_channel_videos.py @win16888 --max-videos 5
```

輸出：`data/metadata/video_list.json`

## 7. Gemini Web/CDP（可選）

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_chrome_cdp.ps1 -Port 9222 -OpenGemini
```

WSL / 專案終端：

```bash
python scripts/process_with_gemini_web.py "https://www.youtube.com/watch?v=VIDEO_ID" --cdp-url http://127.0.0.1:9222
```

## 8. 產出分析師 × 股票矩陣（可選）

```bash
python scripts/daily_analyst_table.py --mode text --text-source auto --days-back 2 --max-videos 1
```

輸出：
- `data/reports/analyst_stock_matrix.md`
- `data/reports/analyst_stock_matrix.csv`
