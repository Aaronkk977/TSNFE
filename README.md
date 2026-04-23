# Taiwan Analyst Signal Pipeline

- 台股投顧影片訊號擷取流程：從 YouTube 影片自動擷取買賣訊號，並產出可用於回測/特徵工程的結構化 JSON。
- 驗證訊號：利用統計與簡單模型分析訊號預測力 （待辦）

## 功能重點

- 每天追蹤分析師Youtube頻道更新影片即時抓取
- 將影音檔轉錄成文字檔，交給LLM擷取分析師推薦與不推薦訊號
- 股票代碼驗證（Fugle / local）
- 產出單檔訊號 + recommendation 清單

## 快速開始

```bash
# 0) 安裝 miniconda
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh # 下載安裝腳本
bash Miniconda3-latest-Linux-x86_64.sh                                     # 執行安裝腳本
source ~/.bashrc                                                           # 載入 Shell 設定

# 1) 建立環境
cd TSNFE
conda create -n tw-analyst python=3.10 -y
conda activate tw-analyst
pip install -r requirements-dev.txt
pip install -e .

# 2) 設定金鑰
cp .env.example .env
# 編輯 .env：填入 YOUTUBE_API_KEY / GOOGLE_API_KEY / FUGLE_API_KEY

# 3) 處理單支影片
python scripts/process_video.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

## 主要輸出位置（已整理）

### 系統性輸出（集中到 data 子目錄）

- `data/raw/`：下載的音訊/影片中介檔
- `data/processing/transcripts/`：逐字稿 JSON
- `data/processing/checkpoints/`：批次處理檢查點（預留，現階段幾乎不使用）
- `data/processing/errors/`：下載/處理失敗紀錄
- `data/processing/metadata/`：抓取清單與輔助 metadata
- `data/processing/debug/`：除錯輸出，例如 Gemini 原始回應
- `data/processed/signals/`：訊號輸出 JSON 與 recommendation list
- `data/processed/reports/`：每日報表與摘要
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
│  ├─ processing/
│  │  ├─ transcripts/
│  │  ├─ checkpoints/
│  │  ├─ errors/
│  │  ├─ metadata/
│  │  └─ debug/
│  └─ processed/
│     ├─ signals/
│     └─ reports/
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
- `python scripts/build_table_from_signals.py --days-back 1`：只用現有 signal JSON 重新生表


## 設定檔

- `.env`：只放「敏感資訊與部署覆蓋」，例如 API Keys
- `config/config.yaml`：只放「預設行為與參數」，例如 execution mode、timeout、模型、chunk、provider
- `config/prompts.yaml`：prompt 模板，包含語音轉錄模型（Whisper initial prompt）和特徵擷取LLM

模型優先順序：
1. `config/config.yaml` 的對應模型設定
2. CLI `--llm-model` / `--llm-provider`（臨時覆蓋，非必要）

執行模式（`scripts/process_video.py`）：
- `--mode audio`：讀音檔做多模態萃取（最穩，但費用高）
- `--mode url`：直接讀 YouTube URL（較不穩，可能誤判影片）
- `--mode text`：先產生文字，再由 LLM 讀文字

文字模式來源（`--mode text` 時）：
- `--text-source auto`：先快取/CC，再回退到 Gemini 轉錄
- `--text-source cc`：只用快取/YouTube CC
- `--text-source gemini`：直接用 Gemini 轉錄文字

模型與 chunk 設定（主要放在 `config/config.yaml`）：
- `transcription.gemini_model`：Gemini 轉錄模型
- `transcription.model`：Whisper 模型
- `extraction.models.gemini` / `extraction.models.qwen` / `extraction.models.local_hf`：擷取模型
- `extraction.chunking`：小模型文字擷取切塊設定

成本追蹤（Pipeline Statistics）：
- 會讀 Gemini 回傳的 `usage_metadata` token 數
- 依 `config/config.yaml` 的 `extraction.pricing.gemini` 估算 USD（分 Flash / Pro / Pro>200K）
- 像 `extraction.pricing.gemini` 這類固定值，也可以留在 config 裡；只有需要改動行為/模型切換的設定才優先放 config。

本地股票代碼資料：
- `python scripts/update_stock_list.py` 會更新
	- `data/stock_codes/twse_stocks.csv`（上市）
	- `data/stock_codes/tpex_stocks.csv`（上櫃）
	- `data/stock_codes/all_stocks.csv`（合併）

目錄用途補充：
- `checkpoints` 目前是批次續跑的保留欄位，現有流程幾乎不依賴它；如果你不做 resume，可以刪檔，但保留空目錄也沒有壞處。
- `metadata` 目前有實際用途，像 `video_list.json` 會寫在這裡，不建議刪掉。

音檔轉文字模型：
- 預設為 `GeminiTranscriber`，模型由 `config/config.yaml` 的 `transcription.gemini_model` 控制
- `--mode text --text-source auto` 會先用快取/YouTube CC，失敗才做 Gemini 轉錄
- 在 `auto` 且 Gemini 轉錄失敗時，會 fallback 到 `Whisper`，模型由 `config/config.yaml` 的 `transcription.model` 控制

## GitHub Actions 每日自動化

本 repo 已內建 workflow：`.github/workflows/daily-analyst-table.yml`，每天會自動產出：

- `data/reports/daily/YYYY-MM-DD/analyst_stock_matrix_YYYYMMDD_HHMMSS.md`
- `data/reports/daily/YYYY-MM-DD/analyst_stock_matrix_YYYYMMDD_HHMMSS.csv`
- `data/reports/daily/YYYY-MM-DD/daily_run_summary_YYYYMMDD_HHMMSS.json`

分析師清單在 `config/analysts.yaml`。

## 待實作功能
- 分析產業說明影片，需學會如何根據產業和優勢條件推理適當標的
- Daily 產出的 table 可以加上觀看數欄位，分析師由觀看數大排到小
- 公司清單要每天更新一次


## 注意事項

- 不要提交 `.env`、cookie、或任何私有憑證。
- 若曾提交敏感資訊，請立即 rotate key / cookie。
- `.gitignore` 已忽略 runtime 輸出與 `local/cookies.txt`。

## 最新功能新增

- **本地端開源大模型支援 (Local vLLM Integration)**：現已支援切換至本地端 OpenAI-compatible API（設定 `provider: qwen` 呼叫本地 vLLM API，如 `Qwen-2.5-14B-Instruct-AWQ`），大幅降低長影音大流量分析的 API 成本，同時藉由 `instructor` 嚴格約束 JSON 工具取值。
- **長文本滑動視窗切塊 (Sliding Window Chunking)**：針對長達數萬字的逐字稿，支援以 2,500 字為單位（預設 250 字重疊）切分輸入，有效避免突破 LLM 的 Token 長度上限，同時減少模型尾部注意力渙散造成的抓取遺漏。
- **訊號合併與智慧衝突處理 (Signal Deduplication & Conflict Resolution)**：
  - 各切塊單元的分析結果會根據同檔股票自動 Deduplicate 合併。
  - **行動權重設計**：強烈明確操作 (`buy`/`sell`) 的權重自動覆蓋較保守的訊號 (`hold`)。
  - **自動廢除矛盾觀點**：若一檔股票在同一影片的各文字塊中同時被抽出 `buy` 和 `sell` 訊號，系統會將其判定為矛盾而直接拋棄 (Drop)。
  - **邏輯結集**：抽取出的標的推薦原因 (`reasoning`) 自動進行 `|` 拼接，還原最全面的評斷軌跡。

## report.csv 說明

系統透過每日腳本（如 `daily_analyst_table.py` 或 `build_table_from_signals.py`）所產出的報表會以 CSV 格式（如 `data/reports/daily/YYYY-MM-DD/analyst_stock_matrix...csv`，統稱 `report.csv`）產出，這份檔案有以下特點：

- **矩陣化資料結構**：將龐大而零碎的每日訊號 JSON，聚合成以「日期與分析師」或「股票代碼」為欄位的觀測特徵，讓你一目了然看清每日多空方向與市場共識。
- **量化模型特徵準備**：內建標準化的 DataFrame 格式，預先清洗好的台股分類標籤（買進/賣出/中立），能作為回測系統、資金控管系統或機器學習模型的直接 input 特徵。
- **結合聲量熱度**：依據部分設定與 metadata，報表可結合 YouTube 分析頻道的觀看數排序或點擊流作為聲量指標，賦予了台股基本面與籌碼面之外的「散戶情緒指標」。

## 延伸文件

- `QUICKSTART.md`：快速上手
- `docs/api_setup.md`：API 設定
- `docs/github_actions_daily_table.md`：每日自動報表（含 Secrets）
- `examples/example_pipeline.py`：程式呼叫範例
