# 快速開始指南

## 1️⃣ 激活環境

```bash
# 方式 1: 直接激活 (Linux/Mac)
conda activate tw-analyst

# 方式 2: 如果上面失敗，使用完整路徑
source /tmp2/b12902115/conda/bin/activate tw-analyst
```

驗證激活成功：
```bash
python --version  # 應該顯示 Python 3.11.x
which python       # 應該指向 tw-analyst 環境
```

## 2️⃣ 配置 API Keys

複製範本並編輯：
```bash
cd /tmp2/b12902115/tw-analyst-signal-pipeline
cp .env.example .env
nano .env  # 或用任何喜歡的編輯器
```

至少需要設定：
```bash
YOUTUBE_API_KEY=your_youtube_api_key_here
GOOGLE_API_KEY=your_google_api_key_here
FUGLE_API_KEY=your_fugle_api_key_here
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-flash
TRANSCRIPTION_PROVIDER=gemini
STOCK_VALIDATION_PROVIDER=fugle
```

參考 [docs/api_setup.md](../docs/api_setup.md) 了解如何取得這些 keys。

## 3️⃣ 驗證系統

```bash
python3 scripts/test_system.py
```

應該看到所有測試都 ✓ PASS

## 4️⃣ 處理你的第一個影片

```bash
# 使用 YouTube URL
python3 scripts/process_video.py "https://www.youtube.com/watch?v=VIDEO_ID"

# 或只用影片 ID
python3 scripts/process_video.py "VIDEO_ID"

# 帶上分析師名稱
python3 scripts/process_video.py "VIDEO_ID" --analyst "分析師名稱"
```

結果會保存到：
- `data/signals/{video_id}.json`
- `data/signals/recommendation_list.json`

## 5️⃣（可選）Gemini 網頁版 CDP 自動化

當你要直接用已登入的 Gemini 網頁版（@YouTube）時：

1. 在 **Windows PowerShell** 執行：
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_chrome_cdp.ps1 -Port 9222 -OpenGemini
```

若你在 WSL 執行 Python，建議改用：
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_chrome_cdp.ps1 -Port 9222 -OpenGemini -BindAll
```

2. 在 WSL/專案終端執行：
```bash
python scripts/process_with_gemini_web.py "https://www.youtube.com/watch?v=VIDEO_ID" --cdp-url http://127.0.0.1:9222
```

若 `127.0.0.1` 在 WSL 無法連通，改用 Windows Host IP（通常是 `/etc/resolv.conf` 內 `nameserver` 的值）：
```bash
python scripts/process_with_gemini_web.py "https://www.youtube.com/watch?v=VIDEO_ID" --cdp-url http://<WINDOWS_HOST_IP>:9222
```

輸出會寫入：
- `data/signals/{video_id}_web.json`

## 📁 重要目錄

| 目錄 | 用途 |
|------|------|
| `src/tw_analyst_pipeline/` | 核心代碼 |
| `data/` | 資料存儲 (音檔、腳本、訊號) |
| `config/` | 配置檔案 |
| `scripts/` | 執行和測試腳本 |
| `logs/` | 日誌檔案 |

## 🔑 環境中的包

已安裝的主要依賴：
- `loguru` - 日誌管理
- `pydantic` - 資料驗證
- `yt-dlp` - YouTube 下載
- `google-generativeai` - Gemini 語音轉錄 + 訊號提取
- `faster-whisper` - Whisper fallback 轉錄
- `requests` - Fugle API 驗證
- `instructor` - 結構化輸出
- `tenacity` - 重試邏輯

## ⚙️ 常用命令

```bash
# 進入項目目錄
cd /tmp2/b12902115/tw-analyst-signal-pipeline

# 激活環境
conda activate tw-analyst

# 運行測試
python3 scripts/test_system.py

# 處理視頻
python3 scripts/process_video.py <URL>

# 查看日誌
tail -f logs/pipeline.log

# 列目錄結構
tree -L 2 -I '__pycache__|*.pyc'
```

## 📊 輸出格式

訊號保存為 JSON：
```json
{
  "video_id": "abc123xyz",
  "signals": [
    {
      "stock_code": "2330",
      "stock_name": "台積電",
      "action": "buy",
      "confidence": 0.8,
      "reasoning": "法說會展望佳"
    }
  ]
}
```

## 🆘 常見問題

### "ModuleNotFoundError: No module named 'XXX'"
確保你在 tw-analyst 環境中：
```bash
conda activate tw-analyst
pip list | grep loguru  # 應該找到所有依賴
```

### CUDA 相關問題
如果沒有 GPU：
```bash
# 編輯 .env
WHISPER_DEVICE=cpu
```

### "API key not found"
確保 `.env` 檔案存在並包含正確的金鑰。

## 📚 詳細文件

- [README.md](../README.md) - 完整專案說明
- [API Setup Guide](../docs/api_setup.md) - API 金鑰配置
- [examples/example_pipeline.py](../examples/example_pipeline.py) - 使用範例

## 🚀 下一步

1. 配置完 API keys
2. 運行 `python3 scripts/test_system.py` 驗證
3. 選一支 YouTube 影片測試
4. 檢查 `data/signals/` 中的輸出結果

---

環境已準備好，祝你使用愉快！ 🎉
