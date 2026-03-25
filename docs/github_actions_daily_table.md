# GitHub Actions: Daily Analyst × Stock Table

## Workflow

- File: `.github/workflows/daily-analyst-table.yml`
- Trigger:
  - Scheduled daily run
  - Manual `workflow_dispatch`

## Required Secrets

在 GitHub Repository：`Settings -> Secrets and variables -> Actions` 新增：

- `YOUTUBE_API_KEY`
- `GOOGLE_API_KEY`
- `FUGLE_API_KEY`
- `YT_COOKIES_TXT`（`local/cookies.txt` 內容，Netscape 格式）

## Analyst List

- Config file: `config/analysts.yaml`
- 格式：

```yaml
analysts:
  - name: "郭哲榮分析師"
    channel: "@s178"
  - name: "林鈺凱分析師"
    channel: "@win16888"
```

## Output Files

- `data/reports/analyst_stock_matrix.md`
- `data/reports/analyst_stock_matrix.csv`
- `data/reports/daily_run_summary.json`

Workflow 也會將上述檔案上傳為 artifact：`analyst-stock-table`。

## YouTube 反機器人驗證

Workflow 會先把 `YT_COOKIES_TXT` 寫入 `local/cookies.txt`，並執行：

`python scripts/clean_yt_cookies.py --input local/cookies.txt --output local/cookies.txt`

清理後只保留 YouTube 下載必要的 google/youtube 驗證 cookie。

## Run Mode Used in Workflow

目前 workflow 預設：
- `--mode text`
- `--text-source auto`
- `LLM_MODEL=gemini-2.5-pro`
- `GEMINI_TRANSCRIPTION_MODEL=gemini-2.5-flash`

也就是先用快取/CC 或 Gemini 較輕量轉錄拿文字，再交給 2.5 Pro 做文字理解。
