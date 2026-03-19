#!/bin/bash
# Monitor transcription progress

echo "=== 轉錄進度監控 ==="
echo ""

# Check if process is running
if ps aux | grep -E "python.*quick_transcribe" | grep -v grep > /dev/null; then
    echo "✓ 轉錄處理中..."
    
    # Show process info
    ps aux | grep -E "python.*quick_transcribe" | grep -v grep | awk '{printf "  CPU: %s%%  記憶體: %sMB  運行時間: %s\n", $3, int($6/1024), $10}'
    
    # Check for output files
    echo ""
    echo "=== 輸出文件 ==="
    if [ -d "data/transcripts" ]; then
        ls -lth data/transcripts/*.json 2>/dev/null | head -3 | awk '{printf "  %s  %s  %s\n", $5, $7" "$6, $9}'
    fi
    
    echo ""
    echo "💡 提示: 運行此腳本查看進度"
    echo "   或等待處理完成（預計 10-15 分鐘）"
    
else
    echo "✓ 轉錄已完成或未運行"
    echo ""
    echo "=== 轉錄結果 ==="
    if [ -d "data/transcripts" ]; then
        ls -lth data/transcripts/*.json 2>/dev/null | head -5
    else
        echo "  未找到轉錄文件"
    fi
fi

echo ""
