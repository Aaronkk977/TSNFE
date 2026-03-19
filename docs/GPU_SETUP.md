# GPU 加速轉錄 - 解決方案

## 現況

### 問題
- 系統有 **CUDA 13.0** (6 張 RTX 4090/3090 GPU)
- faster-whisper 需要 **CUDA 12** 的 cuBLAS 庫
- 當前使用 CPU 模式，轉錄速度**非常慢**

### 性能對比

| 模式 | 15分鐘影片轉錄時間 | 速度比 |
|------|-------------------|--------|
| **CPU** (int8) | ~20-30 分鐘 | 1x (基準) |
| **GPU** (float16) | ~1-3 分鐘 | **10-20x** |

**結論：GPU 加速是必要的！** 對於批量處理影片，GPU 可以節省大量時間。

---

## 解決方案

### ✅ 方案 1：創建符號鏈接 (推薦 - 最快)

CUDA 13 與 CUDA 12 基本兼容，可以創建符號鏈接：

```bash
# 創建 CUDA 12 符號鏈接指向 CUDA 13 庫
sudo ln -s /opt/cuda/lib64/libcublas.so.13 /opt/cuda/lib64/libcublas.so.12
sudo ln -s /opt/cuda/lib64/libcublasLt.so.13 /opt/cuda/lib64/libcublasLt.so.12

# 測試
python scripts/test_transcribe.py
```

**優點**：
- 快速實施（1 分鐘）
- 不需要重新安裝
- 通常可以正常工作

**缺點**：
- 理論上可能有微小兼容性問題
- 如果失敗，需要嘗試其他方案

---

### ✅ 方案 2：設置 LD_LIBRARY_PATH (無需 sudo)

不需要 root 權限，通過環境變量指向 CUDA 13 庫：

```bash
# 在 .env 文件添加
export LD_LIBRARY_PATH=/opt/cuda/lib64:$LD_LIBRARY_PATH

# 修改 whisper_engine.py 在加載模型前設置環境變量
import os
os.environ['LD_LIBRARY_PATH'] = '/opt/cuda/lib64:' + os.environ.get('LD_LIBRARY_PATH', '')
```

---

### ✅ 方案 3：安裝支持 CUDA 13 的版本

```bash
conda activate tw-analyst

# 卸載舊版本
pip uninstall faster-whisper ctranslate2 -y

# 安裝最新版本（可能支持 CUDA 13）
pip install faster-whisper --upgrade
```

---

### ⚠️ 方案 4：繼續使用 CPU (不推薦)

如果上述方案都失敗，可以繼續使用 CPU：

**適用場景**：
- 只處理少量影片（1-2 個）
- 不在意等待時間
- 沒有 GPU 訪問權限

**缺點**：
- 轉錄 1 個 15 分鐘影片需要 20-30 分鐘
- 處理 10 個影片需要 3-5 小時
- CPU 佔用率高

---

## 建議執行步驟

### 步驟 1：嘗試符號鏈接（最簡單）

```bash
# 創建符號鏈接
sudo ln -s /opt/cuda/lib64/libcublas.so.13 /opt/cuda/lib64/libcublas.so.12
sudo ln -s /opt/cuda/lib64/libcublasLt.so.13 /opt/cuda/lib64/libcublasLt.so.12

# 更新 .env 使用 GPU
sed -i 's/WHISPER_DEVICE=cpu/WHISPER_DEVICE=cuda/' .env
sed -i 's/WHISPER_COMPUTE_TYPE=int8/WHISPER_COMPUTE_TYPE=float16/' .env

# 測試
cd /tmp2/b12902115/tw-analyst-signal-pipeline
conda activate tw-analyst
python scripts/test_transcribe.py
```

### 步驟 2：如果成功，批量處理影片

```bash
# 處理林鈺凱分析師最近 5 個影片
python scripts/fetch_channel_videos.py @win16888 \
    --max-videos 5 \
    --transcribe \
    --log-level INFO
```

預計時間：
- **GPU 模式**：5-10 分鐘
- **CPU 模式**：1.5-2.5 小時

---

## 性能監控

```bash
# 監控 GPU 使用率
watch -n 1 nvidia-smi

# 應該看到：
# - GPU Utilization: 80-100%
# - Memory Usage: 2-4 GB
# - Power: 200-300W
```

---

## 總結

| 特性 | CPU 模式 | GPU 模式 |
|------|---------|---------|
| 轉錄速度 | 慢 (1x) | **快 (10-20x)** |
| 資源佔用 | CPU 100% | GPU 80% |
| 批量處理 | 不實用 | **實用** |
| **推薦度** | ⭐ | ⭐⭐⭐⭐⭐ |

**結論：強烈建議使用 GPU 加速！**
