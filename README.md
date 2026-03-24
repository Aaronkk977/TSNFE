# Taiwan Analyst Signal Pipeline

台股投顧影片訊號擷取流程：從 YouTube 影片自動擷取買賣訊號，並產出可用於回測/特徵工程的結構化 JSON。

## 功能重點

- 自動下載或直接讀取 YouTube 影片進行多模態萃取
- Gemini 為預設流程，Whisper 保留 fallback
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
│  └─ debug/
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
- `pytest`：跑測試

## 設定檔

- `.env`：API keys 與環境變數（敏感資料）
- `config/config.yaml`：pipeline 預設參數
- `config/prompts.yaml`：prompt 模板

## 注意事項

- 不要提交 `.env`、cookie、或任何私有憑證。
- 若曾提交敏感資訊，請立即 rotate key / cookie。
- `.gitignore` 已忽略 runtime 輸出與 `local/cookies.txt`。

## 延伸文件

- `QUICKSTART.md`：快速上手
- `docs/api_setup.md`：API 設定
- `examples/example_pipeline.py`：程式呼叫範例
