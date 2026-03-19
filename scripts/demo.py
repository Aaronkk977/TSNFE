#!/usr/bin/env python3
"""
Demo: Extract signals from sample transcript (no API keys required)
Shows what the system can do without needing to download real videos
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.extraction.schemas import StockSignal, TradeAction, VideoAnalysis
from tw_analyst_pipeline.stock_data.validators import StockValidator
from tw_analyst_pipeline.utils.config import Settings
from tw_analyst_pipeline.utils.logging import setup_logging

# Setup
setup_logging(level="INFO")

# Sample analyst transcript (Traditional Chinese)
SAMPLE_TRANSCRIPT = """
各位觀眾大家好，我是股市分析師，今天來討論台股未來的走勢。

首先看大盤，加權指數今天收在 18,500 點，我認為台股整體走勢向上，
未來應該會挑戰 19,000 點的壓力。

現在我要討論几支重點股票：

第一支是台積電，代碼 2330。這家公司最近法說會展望很樂觀，
核心業務產能目前滿載，我認為投資人可以加碼買進，
目標價設在 1500 元。台積電這支股票的投資評級是買進。

第二個要講的是聯發科，就是代碼 2454。聯發科最近面臨技術面的反壓，
雖然營收成長不錯，但股價在 1100 元附近有比較強的壓力，
目前建議投資人先觀望，不要急著買進。我對聯發科的評級是持有。

第三支是長榮，也就是 2603。長榮最近運價指數開始下跌，
這對長榮的營收會有負面影響，我建議現在持有的投資人可以考慮減碼，
或者先出脫部分部位。對長榮的推薦是賣出。

然後講一支 ETF，0050（台灣50）。0050 是追蹤台灣前五十大公司的 ETF，
目前基本面看起來還不錯，適合長期投資人布局，可以買進。

最後我想說，鴻海（2317）這家公司雖然營業利益不錯，
但我暫時沒有明確的買賣場景，建議持有即可。

總結來說，台股大盤應該繼續向上，但要注意技術面的賣壓。
謝謝各位，再見。
"""


def parse_sample_signals():
    """Parse signals from the sample transcript."""
    signals = [
        StockSignal(
            stock_code="2330",
            stock_name="台積電",
            action=TradeAction.BUY,
            confidence=0.9,
            reasoning="法說會展望佳，產能滿載，目標價 1500",
            mentioned_price=1500.0,
            technical_indicators=None,
        ),
        StockSignal(
            stock_code="2454",
            stock_name="聯發科",
            action=TradeAction.HOLD,
            confidence=0.7,
            reasoning="技術面反壓，營收成長但股價受到壓力",
            mentioned_price=None,
            technical_indicators=["壓力"],
        ),
        StockSignal(
            stock_code="2603",
            stock_name="長榮",
            action=TradeAction.SELL,
            confidence=0.8,
            reasoning="運價指數下跌，對營收有負面影響",
            mentioned_price=None,
            technical_indicators=None,
        ),
        StockSignal(
            stock_code="0050",
            stock_name="台灣50",
            action=TradeAction.BUY,
            confidence=0.75,
            reasoning="基本面良好，適合長期投資布局",
            mentioned_price=None,
            technical_indicators=None,
        ),
        StockSignal(
            stock_code="2317",
            stock_name="鴻海",
            action=TradeAction.HOLD,
            confidence=0.6,
            reasoning="營業利益不錯但暫無明確買賣訊號",
            mentioned_price=None,
            technical_indicators=None,
        ),
    ]
    
    return signals


def main():
    """Run demo."""
    print("\n" + "=" * 80)
    print("Taiwan Analyst Signal Pipeline - 示範")
    print("=" * 80)
    
    # Show sample transcript
    print("\n📝 分析師逐字稿（示範）：")
    print("-" * 80)
    print(SAMPLE_TRANSCRIPT[:600] + "\n... [省略部分] ...\n")
    
    # Parse signals manually (simulating what LLM would do)
    signals = parse_sample_signals()
    analysis = VideoAnalysis(
        video_id="demo_sample",
        analyst_name="示範分析師",
        signals=signals,
        market_outlook="台股大盤應該繼續向上，但要注意技術面的賣壓",
    )
    
    # Validate signals
    print("\n✓ 訊號萃取完成")
    print("-" * 80)
    
    settings = Settings()
    validator = StockValidator(settings)
    
    print(f"\n📊 提取的股票訊號（共 {len(analysis.signals)} 支）：\n")
    
    buy_signals = []
    sell_signals = []
    hold_signals = []
    
    for i, signal in enumerate(analysis.signals, 1):
        # Validate stock code
        is_valid = validator.validate_stock_code(signal.stock_code)
        status = "✓" if is_valid else "✗"
        
        print(f"{status} {i}. {signal.stock_code:>4} {signal.stock_name:6}")
        print(f"   動作：{signal.action.value:6} | 信心度：{signal.confidence:>3.0%}")
        print(f"   理由：{signal.reasoning}")
        
        if signal.mentioned_price:
            print(f"   目標價：${signal.mentioned_price:.2f}")
        
        if signal.technical_indicators:
            print(f"   技術面：{', '.join(signal.technical_indicators)}")
        
        # Categorize
        if signal.action == TradeAction.BUY:
            buy_signals.append(signal)
        elif signal.action == TradeAction.SELL:
            sell_signals.append(signal)
        else:
            hold_signals.append(signal)
        
        print()
    
    # Summary
    print("\n📈 訊號統計：")
    print("-" * 80)
    print(f"  買進訊號 (Buy):  {len(buy_signals)} 支")
    for sig in buy_signals:
        print(f"    • {sig.stock_code} {sig.stock_name} ({sig.confidence:.0%})")
    
    print(f"\n  賣出訊號 (Sell): {len(sell_signals)} 支")
    for sig in sell_signals:
        print(f"    • {sig.stock_code} {sig.stock_name} ({sig.confidence:.0%})")
    
    print(f"\n  持有訊號 (Hold): {len(hold_signals)} 支")
    for sig in hold_signals:
        print(f"    • {sig.stock_code} {sig.stock_name} ({sig.confidence:.0%})")
    
    # Market outlook
    print(f"\n🎯 大盤看法：")
    print(f"  {analysis.market_outlook}")
    
    # Stock validation
    print(f"\n✓ 股票代碼驗證：")
    print("-" * 80)
    print(f"  已加載 {len(validator.valid_codes)} 支有效台股")
    print(f"  已加載 {len(validator.aliases)} 個股票別名")
    
    # Show alias resolution example
    print(f"\n📍 別名解析範例：")
    test_aliases = ["護國神山", "發哥", "鴻海", "台灣50"]
    for alias in test_aliases:
        resolved = validator.resolve_stock_code(alias)
        if resolved:
            name = validator.get_stock_name(resolved)
            print(f"  '{alias}' → {resolved} ({name})")
        else:
            print(f"  '{alias}' → 未找到")
    
    # Output format
    print(f"\n💾 JSON 輸出格式（會保存到 data/signals/）：")
    print("-" * 80)
    import json
    output = {
        "video_id": analysis.video_id,
        "analyst_name": analysis.analyst_name,
        "signals": [sig.model_dump() for sig in analysis.signals[:2]],  # Show first 2
        "market_outlook": analysis.market_outlook,
        "processed_at": analysis.processed_at.isoformat(),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2)[:500] + "\n  ... [省略] ...\n")
    
    # Next steps
    print("\n" + "=" * 80)
    print("🚀 下一步：")
    print("=" * 80)
    print("""
1. 配置 API Keys （見 docs/api_setup.md）：
   - YOUTUBE_API_KEY
   - OPENAI_API_KEY

2. 編輯 .env 檔案：
   cp .env.example .env
   nano .env

3. 運行完整管線：
   python3 scripts/process_video.py "https://youtube.com/watch?v=VIDEO_ID"

4. 檢查輸出結果：
   cat data/signals/VIDEO_ID.json
""")
    
    print("=" * 80)
    print("✓ 示範完成！系統已準備好進行實際處理。\n")


if __name__ == "__main__":
    main()
