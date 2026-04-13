import yfinance as yf
import pandas as pd
import numpy as np
import csv
from pathlib import Path
from typing import Dict, Optional, Tuple

from ..utils.logging import LoggerMixin
from ..utils.config import Settings

class MarketDataClient(LoggerMixin):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.stock_suffix_map = {}
        self._load_stock_mappings()

    def _load_stock_mappings(self):
        """Load TWSE and TPEX stock lists to know whether to append .TW or .TWO"""
        data_dir = Path(self.settings.data_stock_codes_dir)
        
        twse_file = data_dir / "twse_stocks.csv"
        tpex_file = data_dir / "tpex_stocks.csv"
        
        if twse_file.exists():
            with open(twse_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("code"):
                        self.stock_suffix_map[str(row["code"]).strip().zfill(4)] = ".TW"
                        
        if tpex_file.exists():
            with open(tpex_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("code"):
                        self.stock_suffix_map[str(row["code"]).strip().zfill(4)] = ".TWO"
                        
        self.logger.info(f"Loaded {len(self.stock_suffix_map)} stock suffx mappings for yfinance.")

    def get_yfinance_ticker(self, stock_code: str) -> str:
        """Get the full yfinance ticker with suffix for a Taiwan stock."""
        clean_code = str(stock_code).strip().zfill(4)
        suffix = self.stock_suffix_map.get(clean_code)
        
        # Default to .TW if unknown, it's a reasonable guess for Taiwan stocks
        if not suffix:
            self.logger.warning(f"Unknown stock suffix for {clean_code}, defaulting to .TW")
            suffix = ".TW"
            
        return f"{clean_code}{suffix}"

    def get_forward_return(self, stock_code: str, date: str, n_days: int = 5) -> Optional[float]:
        """
        Fetch the N-day forward return for a stock from a specific date.
        Returns the percentage return: (Price_{t+N} - Price_{t+1_open}) / Price_{t+1_open}.
        Or simply (Price_{t+N_close} - Price_{t_close}) / Price_{t_close}.
        We'll use (Price_{t+N_close} - Price_{t_close}) / Price_{t_close} for simplicity.
        """
        ticker = self.get_yfinance_ticker(stock_code)
        
        # Download data from date to date + say 15 days to ensure we get enough trading days
        start_date = pd.to_datetime(date)
        end_date = start_date + pd.Timedelta(days=20)
        
        try:
            # We use history with period to minimize payload instead of downloading the whole history
            # But downloading whole history once and caching is much faster for batch processing.
            ticker_obj = yf.Ticker(ticker)
            df = ticker_obj.history(start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
            
            if df.empty or len(df) <= n_days:
                return None
                
            # The row at index 0 should be the 'date' or the first trading day on/after 'date'
            # The return is calculated from the close of the 0-th index to the close of the n_days-th index.
            # Using T to T+N close-to-close return:
            price_t = df['Close'].iloc[0]
            price_t_plus_n = df['Close'].iloc[n_days]
            
            return (price_t_plus_n - price_t) / price_t
            
        except Exception as e:
            self.logger.error(f"Failed to fetch market data for {ticker} on {date}: {e}")
            return None
            
    def get_historical_data_batch(self, stock_codes: list, start_date: str, end_date: str) -> pd.DataFrame:
        """Batch download for multiple stocks over a period. Good for caching."""
        tickers = [self.get_yfinance_ticker(code) for code in stock_codes]
        
        # yf.download returns a MultiIndex DataFrame if multiple tickers
        df = yf.download(tickers, start=start_date, end=end_date, group_by='ticker', auto_adjust=True, progress=False)
        return df

