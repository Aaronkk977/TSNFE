# Taiwan Analyst Signal Pipeline

A production-grade quantitative trading data pipeline that automatically extracts stock buy/sell signals from Taiwan stock analyst YouTube videos using AI-powered speech recognition and language models.

## 🎯 Project Overview

This system implements an **Alternative Data** pipeline for quantitative trading that:

1. **自動監控** YouTube 投顧分析師頻道
2. **智能轉錄** 使用 Gemini 2.5 Flash 將影片音檔轉為繁中逐字稿
3. **結構化萃取** 呼叫 Gemini 2.5 Flash 提取個股推薦訊號
4. **量化驗證** 以 Fugle API 驗證股票代碼真實存在
5. **即時保存** 訊號為可供回測的結構化格式

### Key Features

- ✅ **Gemini 語音轉錄** - Gemini 2.5 Flash 音訊理解
- ✅ **Gemini 訊號提取** - Gemini 2.5 Flash + 結構化輸出
- ✅ **推薦清單 Feature** - 時間 / 觀看數 / 推薦股票 / label
- ✅ **台股別名解析** - 「護國神山」→ 2330、「發哥」→ 2454
- ✅ **成本追蹤** - API 成本即時監控與預算告警
- ✅ **檢查點機制** - 支援斷點續傳，避免重複處理
- ✅ **模組化架構** - 易於擴充新分析師、模型、特徵

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- NVIDIA GPU (建議 6GB+ VRAM)，或 CPU（較慢）
- API Keys:
  - YouTube Data API v3
  - Google AI API (Gemini)
  - Fugle API

### Installation

```bash
# Clone & setup
git clone <repo>
cd tw-analyst-signal-pipeline
chmod +x scripts/setup.sh
./scripts/setup.sh

# Or manual setup
python3 -m venv venv
source venv/bin/activate
pip install -e .

# Configure environment
cp .env.example .env
# Edit .env and add your API keys
```

### Run System Tests

```bash
python3 scripts/test_system.py
```

### Process Your First Video

```bash
# Using YouTube URL
python3 scripts/process_video.py "https://www.youtube.com/watch?v=VIDEO_ID"

# Or just video ID
python3 scripts/process_video.py "VIDEO_ID"

# With analyst name
python3 scripts/process_video.py "VIDEO_ID" --analyst "股市分析師名稱"
```

## 📊 Output Format

Stock signals are saved as JSON in `data/signals/`:

```json
{
  "video_id": "abc123xyz",
  "analyst_name": null,
  "signals": [
    {
      "stock_code": "2330",
      "stock_name": "台積電",
      "action": "buy",
      "confidence": 0.85,
      "reasoning": "法說會展望佳，產能滿載",
      "mentioned_price": 1500.0,
      "technical_indicators": null
    },
    {
      "stock_code": "2454",
      "stock_name": "聯發科",
      "action": "hold",
      "confidence": 0.6,
      "reasoning": "技術面需加強",
      "mentioned_price": null,
      "technical_indicators": ["壓力"]
    }
  ],
  "market_outlook": "看多台股未來走勢",
  "processed_at": "2024-01-15T10:30:00",
  "processing_duration_seconds": 120.5,
  "transcript_length_chars": 15000,
  "confidence_score": 0.725
}
```

另外會維護彙總清單 `data/signals/recommendation_list.json`：

```json
{
  "updated_at": "2026-03-15T10:30:00Z",
  "count": 1,
  "items": [
    {
      "video_id": "abc123xyz",
      "timestamp": "2026-03-15T10:30:00",
      "view_count": 123456,
      "recommended_stocks": [
        {"stock_code": "2330", "stock_name": "台積電", "label": "買進"}
      ],
      "label": "買進"
    }
  ]
}
```

## 🏗️ Architecture

```
YouTube URL
    ↓ [YouTube Downloader]
  Audio File (WAV)
    ↓ [Gemini Transcriber]
  Full Transcript (中文逐字稿)
    ↓ [Gemini 2.5 Flash Extractor]
  Raw Signals (JSON)
    ↓ [Fugle Stock Validator]
  Validated Signals
    ↓ [Storage]
  signals/{video_id}.json + recommendation_list.json
```

### Core Modules

| Module | Purpose | Key Classes |
|--------|---------|------------|
| `youtube/` | YouTube 下載 | `AudioDownloader` |
| `transcription/` | 語音轉文字 | `GeminiTranscriber`, `WhisperTranscriber` |
| `extraction/` | LLM 訊號萃取 | `GoogleExtractor`, `LLMExtractorFactory` |
| `stock_data/` | 股票驗證 | `StockValidator` |
| `pipeline/` | 主流程編排 | `SignalPipeline` |
| `utils/` | 工具類 | `Settings`, `setup_logging`, `retry_with_backoff` |

## ⚙️ Configuration

### Environment Variables (.env)

```bash
# LLM Configuration
GOOGLE_API_KEY=...
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-flash
LLM_TEMPERATURE=0.1

# Stock validation
FUGLE_API_KEY=...
STOCK_VALIDATION_PROVIDER=fugle

# Transcription provider
TRANSCRIPTION_PROVIDER=gemini
GEMINI_TRANSCRIPTION_MODEL=gemini-2.5-flash

# Processing
MAX_RETRIES=3
MAX_CONCURRENT_DOWNLOADS=3
ENABLE_CACHING=true
VALIDATE_STOCK_CODES=true

# Cost Management
DAILY_BUDGET_USD=100.0
ENABLE_COST_TRACKING=true
```

### Pipeline Configuration (config/config.yaml)

主要的管線參數配置，包括：
- 重試策略（retry logic）
- Whisper 模型參數
- LLM 提取提示詞
- 輸出格式與目錄

## 📈 Performance Metrics

- **轉錄速度**: ~10-15分鐘影片 → 2-3分鐘 (GPU, medium 模型)
- **LLM 萃取**: ~30-50 tokens 平均消耗 per signal
- **API 成本**: 視 Gemini/Fugle 用量而定
- **訊號準確度**: ~85-95% (取決於分析師的清楚度)

## 🔄 Workflow Example

```python
from tw_analyst_pipeline.pipeline.orchestrator import SignalPipeline
from tw_analyst_pipeline.utils.config import get_settings, get_pipeline_config

# Initialize
settings = get_settings()
pipeline = SignalPipeline(settings, get_pipeline_config())

# Process video
analysis = pipeline.process_video(
    video_url="https://www.youtube.com/watch?v=abc123",
    analyst_name="股市分析師"
)

# Access results
for signal in analysis.signals:
    print(f"{signal.stock_code} {signal.stock_name}: {signal.action}")
    print(f"  Confidence: {signal.confidence:.1%}")
    print(f"  Reasoning: {signal.reasoning}")

# Get statistics
stats = pipeline.get_stats()
print(f"Total cost: ${stats['total_cost_usd']:.2f}")
```

## 📝 Project Structure

```
tw-analyst-signal-pipeline/
├── src/tw_analyst_pipeline/
│   ├── youtube/           # YouTube 資料獲取
│   ├── transcription/     # 語音轉錄模組
│   ├── extraction/        # LLM 萃取模組
│   ├── stock_data/        # 股票驗證
│   ├── pipeline/          # 主流程編排
│   └── utils/             # 配置、日誌、重試
├── tests/                 # 單元測試與整合測試
├── config/                # 配置檔案
├── data/                  # 資料目錄（運行時創建）
├── scripts/               # 執行、測試腳本
├── pyproject.toml         # 項目元數據
└── README.md
```

## 🧪 Testing

```bash
# Run all system tests
python3 scripts/test_system.py

# Run unit tests
pytest tests/unit/ -v

# Run integration tests (requires API keys)
pytest tests/integration/ -v --slow
```

## 🎨 LLM Prompt Engineering

核心提示詞在 `config/prompts.yaml` 中定義，包括：

- **system_prompt**: 定義 AI 角色與約束
- **extraction_prompt**: 訊號萃取的具體指示
- **validation_prompt**: 訊號驗證的邏輯

可根據不同分析師風格調整提示詞以提高準確度。

## ⚠️ Known Limitations & Challenges

1. **長文本限制**: GPT-4o-mini 的 128K context 可能不足（1小時影片 → ~15K tokens）
   - 解決: 使用 Gemini 1.5 Flash (1M context) 或 Map-Reduce 策略

2. **分析師風格差異**: 不同分析師的表達方式差異大，可能影響萃取準確度
   - 解決: 建立分析師特定的 prompt 模板

3. **幻覺現象**: LLM 可能編造股票代碼或訊號
   - 解決: 嚴格的驗證層，反向校對

4. **成本管理**: 高頻處理可能導致 API 成本快速增長
   - 解決: 實施日預算限制與智能快取

## 🚀 Next Steps (Phase 2)

- [ ] YouTube API 整合 - 自動監控分析師頻道
- [ ] 排程系統 - 每天自動執行 (Airflow/Prefect)
- [ ] 特徵工程 - 轉換訊號為量化特徵
- [ ] 回測整合 - 與 backtrader/zipline 連結
- [ ] Web 儀表板 - 即時信號監控

## 📚 References

### Technology Stack

- **Whisper**: https://github.com/openai/whisper
- **faster-whisper**: https://github.com/guillaumekln/faster-whisper
- **instructor**: https://github.com/jxnl/instructor
- **OpenAI API**: https://platform.openai.com/docs
- **yt-dlp**: https://github.com/yt-dlp/yt-dlp

### Taiwan Stock Market

- 台灣證券交易所: https://www.twse.com.tw/
- 台灣證券櫃檯買賣中心: https://www.tpex.org.tw/

## 📄 License

MIT License - See LICENSE file for details

## 👥 Contributing

歡迎貢獻改進與新功能！

---

**Built with ❤️ for Taiwan's quantitative trading community**
