# Taiwan Analyst Signal Pipeline

台股投顧影片訊號擷取流程：從 YouTube 影片自動擷取買賣訊號，並產出可用於回測/特徵工程的結構化 JSON。

## 功能重點

- 每天追蹤分析師Youtube頻道更新影片即時抓取
- 自動下載或直接讀取 YouTube 影片進行多模態萃取
- 使用 Gemini 可支援文字、音檔和連結多種消息來源
- 股票代碼驗證（Fugle / local）
- 產出單檔訊號 + recommendation 清單
- 支援檢查點、快取、錯誤記錄

## 快速開始

```bash
# 1) 建立環境
conda create -n tw-analyst python=3.10 -y
conda activate tw-analyst
pip install -r requirements-dev.txt
pip install -e .

# 2) 設定金鑰
cp .env.example .env
# 編輯 .env：填入 YOUTUBE_API_KEY / GOOGLE_API_KEY / FUGLE_API_KEY

# 3) 驗證
python scripts/test_system.py

# 4) 處理單支影片
python scripts/process_video.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

## 主要輸出位置（已整理）

### 核心資料輸出

- `data/signals/{video_id}.json`：單支影片訊號
- `data/signals/recommendation_list.json`：彙總推薦清單
- `data/transcripts/{video_id}.json`：逐字稿（transcript 流程時）
- `data/raw/{video_id}.wav`：下載音訊（中介檔）

### 系統性輸出（集中到 data 子目錄）

- `data/errors/failed_downloads.json`：下載失敗紀錄
- `data/errors/failed_processing.json`：流程失敗紀錄
- `data/metadata/video_list.json`：頻道抓取影片清單
- `data/debug/last_gemini_multimodal_response.json`：最近一次 Gemini 原始回應（除錯）
- `logs/`：程式執行 log（pipeline logger）

## Repo 結構（精簡版）

```text
TSNFE/
├─ src/tw_analyst_pipeline/   # 核心程式
│  ├─ extraction/
│  ├─ pipeline/
│  ├─ stock_data/
│  ├─ transcription/
│  ├─ youtube/
│  └─ utils/
├─ scripts/                   # CLI / 操作腳本
├─ config/                    # YAML 與 prompt 設定
├─ data/                      # 執行輸出（runtime artifacts）
│  ├─ raw/
│  ├─ transcripts/
│  ├─ signals/
│  ├─ checkpoints/
│  ├─ errors/
│  ├─ metadata/
│  ├─ debug/
│  └─ reports/
├─ logs/                      # 執行 log
├─ tests/                     # 測試
├─ docs/                      # 文件
└─ local/                     # 本機私有輔助檔（不放核心邏輯）
```

> `local/` 用來收納本機備忘與私有檔（例如 cookies、個人清單），避免 repo 根目錄雜亂。

## 常用腳本

- `python scripts/process_video.py <url_or_id>`：處理單支影片
- `python scripts/fetch_channel_videos.py @channel --max-videos 5`：抓頻道影片清單
- `python scripts/process_with_gemini_web.py <url>`：走 Gemini Web/CDP 流程
- `python scripts/daily_analyst_table.py`：產生「分析師 × 股票」日報表
- `pytest`：跑測試

## 設定檔

- `.env`：只放「敏感資訊與部署覆蓋」，例如 API Keys 與 `LLM_MODEL`
- `config/config.yaml`：只放「預設行為與參數」，例如 execution mode、timeout、prompt
- `config/prompts.yaml`：prompt 模板

模型優先順序（Gemini）：
1. CLI `--llm-model`（若有給）
2. `.env` 的 `LLM_MODEL`
3. `config/config.yaml` 的 `extraction.models.gemini`

執行模式（`scripts/process_video.py`）：
- `--mode audio`：讀音檔做多模態萃取（預設、最穩）
- `--mode url`：直接讀 YouTube URL（較不穩，可能誤判影片）
- `--mode text`：先產生文字，再由 LLM 讀文字

文字模式來源（`--mode text` 時）：
- `--text-source auto`：先快取/CC，再回退到 Gemini 轉錄
- `--text-source cc`：只用快取/YouTube CC
- `--text-source gemini`：直接用 Gemini 轉錄文字

成本追蹤（Pipeline Statistics）：
- 會讀 Gemini 回傳的 `usage_metadata` token 數
- 依 `config/config.yaml` 的 `extraction.pricing.gemini` 估算 USD（分 Flash / Pro / Pro>200K）

本地股票代碼資料：
- `python scripts/update_stock_list.py` 會更新
	- `data/stock_codes/twse_stocks.csv`（上市）
	- `data/stock_codes/tpex_stocks.csv`（上櫃）
	- `data/stock_codes/all_stocks.csv`（合併）

音檔轉文字模型：
- 預設為 `GeminiTranscriber`，模型來自 `GEMINI_TRANSCRIPTION_MODEL`（預設 `gemini-2.5-flash`）
- `--mode text --text-source auto` 會先用快取/YouTube CC，失敗才做 Gemini 轉錄
- 在 `auto` 且 Gemini 轉錄失敗時，會 fallback 到 `Whisper`（`WHISPER_MODEL`，預設 `medium`）

## GitHub Actions 每日自動化

本 repo 已內建 workflow：`.github/workflows/daily-analyst-table.yml`，每天會自動產出：

- `data/reports/analyst_stock_matrix.md`
- `data/reports/analyst_stock_matrix.csv`
- `data/reports/daily_run_summary.json`

分析師清單在 `config/analysts.yaml`。

請先在 GitHub Repository 設定 Secrets：
- `YOUTUBE_API_KEY`
- `GOOGLE_API_KEY`
- `FUGLE_API_KEY`
- `YT_COOKIES_TXT`（Netscape cookie 文字）

## 待實作功能
- 分析產業說明影片，需學會如何根據產業和優勢條件推理適當標的
- Daily 產出的 table 可以加上觀看數欄位，分析師由觀看數大排到小
- 公司清單要每天更新一次


## 注意事項

- 不要提交 `.env`、cookie、或任何私有憑證。
- 若曾提交敏感資訊，請立即 rotate key / cookie。
- `.gitignore` 已忽略 runtime 輸出與 `local/cookies.txt`。

## 延伸文件

- `QUICKSTART.md`：快速上手
- `docs/api_setup.md`：API 設定
- `docs/github_actions_daily_table.md`：每日自動報表（含 Secrets）
- `examples/example_pipeline.py`：程式呼叫範例
