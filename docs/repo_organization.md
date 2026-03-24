# Repo Organization Guide

這份文件定義此 repo 的「檔案放置規則」與「輸出歸位原則」，避免檔案越跑越散。

## 1) 目錄責任

- `src/`：只放可重用的核心邏輯，不放臨時腳本輸出。
- `scripts/`：CLI 與一次性操作入口。
- `config/`：版本可追蹤的設定與模板。
- `docs/`：文件。
- `tests/`：測試。
- `data/`：所有 runtime artifacts（輸入/中介/輸出/錯誤）。
- `logs/`：執行日誌。
- `local/`：本機私有備忘、cookies、個人清單。

## 2) data 目錄規範

- `data/raw/`：下載的音訊/影片中介檔。
- `data/transcripts/`：逐字稿 JSON。
- `data/signals/`：訊號輸出 JSON 與 recommendation list。
- `data/checkpoints/`：批次處理檢查點。
- `data/errors/`：失敗記錄（下載/處理）。
- `data/metadata/`：抓取清單與輔助 metadata。
- `data/debug/`：除錯輸出（例如 LLM 原始回應）。
- `data/reports/`：自動化報表輸出（矩陣表、每日摘要）。

## 3) 已完成的整理

- `failed_downloads.json` → `data/errors/failed_downloads.json`
- `failed_processing.json` → `data/errors/failed_processing.json`
- `video_list.json` → `data/metadata/video_list.json`
- `logs/last_gemini_multimodal_response.json` → `data/debug/last_gemini_multimodal_response.json`
- 根目錄零散檔案移至 `local/`：
  - `analysist_list.txt` → `local/analyst_list.txt`
  - `conda_commands.sh` → `local/conda_commands.sh`
  - `cookies.txt` → `local/cookies.txt`

## 4) 新增檔案時的判斷

- 若檔案是「執行會反覆覆蓋/新增」：放 `data/` 或 `logs/`。
- 若檔案是「部署/版本控制需要」：放 `src/`、`config/`、`scripts/`、`docs/`。
- 若檔案是「個人機器專用」：放 `local/`。

## 5) Git 管理

- runtime 輸出應持續忽略（`.gitignore` 已設定）。
- 私有憑證不可提交（`.env`、cookies、tokens）。
