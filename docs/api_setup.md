# API Keys Setup Guide

本指南說明如何取得各項 API 金鑰。

## 1. OpenAI API Key (GPT-4o-mini)

### 步驟

1. 訪問 [OpenAI Platform](https://platform.openai.com/)
2. 使用 GitHub 或 Google 帳號登入
3. 點擊右上角 → **Account** → **API Keys**
4. 點擊 **Create new secret key**
5. 複製金鑰並保貼到 `.env` 檔案

```bash
OPENAI_API_KEY=sk-proj-...
```

### 計費

- **GPT-4o-mini**: $0.15 per 1M input tokens, $0.60 per 1M output tokens
- 10分鐘影片通常成本 $0.01-0.05
- 免費試用額度: $5 (30天內用完)

### 驗證

```bash
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

---

## 2. YouTube Data API v3

### 步驟

1. 訪問 [Google Cloud Console](https://console.cloud.google.com)
2. 點擊 **Create Project**，輸入 "tw-analyst-signal-pipeline"
3. 等待專案建立完成
4. 在搜尋框搜尋 "YouTube Data API v3"
5. 點擊 **Enable**
6. 點擊 **Create Credentials** → **API Key**
7. 複製金鑰到 `.env`

```bash
YOUTUBE_API_KEY=AIza...
```

### 配額

- 免費層: **10,000 units/day**
- 操作成本:
  - `search.list`: 100 units
  - `videos.list`: 1 unit 
  - `playlistItems.list`: 1 unit

### 計算用量

監控 1 個頻道的日新影片：
- 每天 1 次搜尋: 100 units
- 查詢 10 支最新影片: 10 units
- **總計**: 110 units/day (在配額內)

### 驗證

```bash
curl "https://www.googleapis.com/youtube/v3/videos?id=dQw4w9WgXcQ&key=$YOUTUBE_API_KEY&part=snippet"
```

---

## 3. 其他 LLM 提供商 (可選)

### Anthropic Claude API

1. 訪問 [Anthropic Console](https://console.anthropic.com)
2. 建立帳號
3. **API Keys** → **Create Key**
4. 複製到 `.env`

```bash
ANTHROPIC_API_KEY=sk-ant-...
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-sonnet-20240229
```

### Google Generative AI (Gemini)

1. 訪問 [AI Studio](https://aistudio.google.com/app/apikey)
2. 點擊 **Create API Key**
3. 複製到 `.env`

```bash
GOOGLE_API_KEY=AIza...
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.0-flash
```

---

## 4. GPU 驗證 (NVIDIA)

檢查是否有可用的 CUDA GPU：

```bash
nvidia-smi

# Output example:
# +--------+------+
# | GPU ID | GPU Name               | Memory Usage |
# +--------+------+
# | 0      | NVIDIA RTX 3060 Ti    | 1GB/8GB      |
# +--------+------+
```

如果沒有 GPU，設定為 CPU（較慢）:

```bash
WHISPER_DEVICE=cpu
```

---

## 5. 設置 .env 檔案

複製範本並填入實際的金鑰：

```bash
cp .env.example .env
nano .env  # 或用你喜歡的編輯器

# 必需項目
YOUTUBE_API_KEY=AIza...
OPENAI_API_KEY=sk-proj-...

# 可選 (有默認值)
WHISPER_MODEL=medium
WHISPER_DEVICE=cuda
LLM_MODEL=gpt-4o-mini
```

---

## 6. 成本預估

### 月度成本計算

**場景**: 每天處理 10 支影片

| 項目 | 單價 | 月量 | 成本 |
|------|------|------|------|
| GPT-4o-mini | ~$0.03/影片 | 300 | $9.00 |
| YouTube API | 免費 (10K units/day) | - | $0 |
| Whisper (GPU) | 無成本 | - | $0 |

**預估月成本**: ~$9-15 (取決於影片長度)

---

## 7. 故障排除

### "403 Unauthorized" - OpenAI

```bash
# 檢查 API Key 是否正確
echo $OPENAI_API_KEY

# 確保金鑰有效期限未過期
```

### "quotaExceeded" - YouTube API

```yaml
# 解決方案:
1. 使用備用 API Key:
   YOUTUBE_API_KEY_BACKUP=AIza...

2. 減少查詢頻率 (從每小時改為每天)

3. 僅查詢播放清單，不搜尋
```

### "CUDA out of memory"

```bash
# 降級 Whisper 模型
WHISPER_MODEL=small   # 而不是 medium

# 或切換到 CPU
WHISPER_DEVICE=cpu
```

---

## 8. 安全最佳實踐

⚠️ **重要**: 永遠不要提交 `.env` 到版本控制!

```bash
# 檢查 .gitignore
grep "\.env" .gitignore  # 應該返回 .env

# 如果不小心提交了，移除
git rm --cached .env
git commit -m "Remove .env from tracking"
```

---

## 9. 驗證完整設置

```bash
# 執行系統測試
python3 scripts/test_system.py

# 預期輸出:
# ✓ Configuration
# ✓ Schemas
# ✓ Logging
# ✓ Dependencies
# ✓ Stock Validator
# ✓ All tests passed!
```

---

如有問題，請告知頻道上的常見 YouTube 分析師，以便測試。
