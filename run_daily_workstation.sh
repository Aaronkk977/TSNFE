#!/bin/bash
# 進入專案目錄
cd /tmp2/b12902115/TSNFE

# 1. 自動偵測並啟動 Conda
# 根據您的 which 輸出，嘗試使用 miniconda3 的路徑
export CONDA_EXE=/tmp2/b12902115/miniconda3/bin/conda
export CONDA_SH=/tmp2/b12902115/miniconda3/etc/profile.d/conda.sh

if [ -f "$CONDA_SH" ]; then
    source "$CONDA_SH"
else
    # 如果路徑還是不對，嘗試使用 conda_commands.sh 提到的建議方法
    eval "$($CONDA_EXE shell.bash hook)"
fi

# 2. 激活環境
conda activate tw-analyst

# 3. 執行分析任務
# 使用 local/analyst_list.txt，抓取 24 小時內更新的影片，並加上影片間延遲
python3 scripts/daily_analyst_table.py --analysts-file local/analyst_list.txt --mode text --text-source auto --days-back 1 --max-videos 10 >> logs/daily_cron.log 2>&1

# 4. 執行通知腳本 (下一步會建立此檔案)
python3 scripts/send_notification.py >> logs/daily_cron.log 2>&1