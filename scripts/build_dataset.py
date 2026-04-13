import json
import pandas as pd
from pathlib import Path
from tqdm import tqdm
import argparse
import sys
import os

# Add parent path to import our modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.tw_analyst_pipeline.stock_data.market_data import MarketDataClient
from src.tw_analyst_pipeline.utils.config import Settings

def process_daily_folder(date_folder: Path, market_client: MarketDataClient, wait_days: int) -> pd.DataFrame:
    summary_files = list(date_folder.glob("daily_run_summary_*.json"))
    csv_files = list(date_folder.glob("analyst_stock_matrix_*.csv"))
    
    if not summary_files or not csv_files:
        return pd.DataFrame()
        
    # Read the latest summary if multiple
    summary_file = max(summary_files, key=lambda p: p.stat().st_mtime)
    csv_file = max(csv_files, key=lambda p: p.stat().st_mtime)
    
    date_str = date_folder.name
    
    with open(summary_file, 'r', encoding='utf-8') as f:
        summary_data = json.load(f)
        
    rankings = summary_data.get("stock_rankings", {})
    
    # We want features for each stock: 
    # buy_count, sell_count
    # is_top_recommended, is_top_not_recommended
    # view_sum_recommended, view_sum_not_recommended
    
    # First, parse the matrix for buy/sell counts
    df_matrix = pd.read_csv(csv_file, index_col=0) # Index is analyst name
    
    records = []
    
    # Collect all stocks mentioned in the matrix
    stocks = df_matrix.columns.tolist()
    
    # Extract codes from column names (e.g. "2317 鴻海" -> "2317")
    for stock_col in stocks:
        parts = str(stock_col).split(' ', 1)
        if not parts[0].isdigit():
            continue
        stock_code = parts[0]
        
        # Calculate buy/sell counts from matrix
        col_data = df_matrix[stock_col]
        buy_count = (col_data == '買進').sum()
        sell_count = (col_data == '賣出').sum()
        
        if buy_count == 0 and sell_count == 0:
            continue # Skip if only neutral or mostly empty
            
        # Top list features
        is_top_rec = int(any(s.get('stock_code') == stock_code for s in rankings.get('most_recommended_by_analysts', [])))
        is_top_not_rec = int(any(s.get('stock_code') == stock_code for s in rankings.get('most_not_recommended_by_analysts', [])))
        
        # View sum features
        view_sum_rec = 0
        for s in rankings.get('most_viewed_recommended_stocks', []):
            if s.get('stock_code') == stock_code:
                view_sum_rec = s.get('view_sum', 0)
                break
                
        view_sum_not_rec = 0
        for s in rankings.get('most_viewed_not_recommended_stocks', []):
            if s.get('stock_code') == stock_code:
                view_sum_not_rec = s.get('view_sum', 0)
                break
                
        # Get target return
        fwd_return = market_client.get_forward_return(stock_code, date_str, n_days=wait_days)
        
        records.append({
            'date': date_str,
            'stock_code': stock_code,
            'buy_count': buy_count,
            'sell_count': sell_count,
            'is_top_recommended': is_top_rec,
            'is_top_not_recommended': is_top_not_rec,
            'recommended_view_sum': view_sum_rec,
            'not_recommended_view_sum': view_sum_not_rec,
            f'return_{wait_days}d': fwd_return
        })
        
    return pd.DataFrame(records)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=5, help='Number of forward days for return target')
    args = parser.parse_args()
    
    settings = Settings()
    market_client = MarketDataClient(settings)
    
    daily_reports_dir = Path("data/reports/daily")
    if not daily_reports_dir.exists():
        print(f"Directory {daily_reports_dir} does not exist.")
        return
        
    all_dates = sorted([d for d in daily_reports_dir.iterdir() if d.is_dir()])
    
    all_dfs = []
    print(f"Building dataset mapping signals to {args.days}-day forward returns...")
    for date_folder in tqdm(all_dates):
        df_day = process_daily_folder(date_folder, market_client, args.days)
        if not df_day.empty:
            all_dfs.append(df_day)
            
    if not all_dfs:
        print("No valid data constructed.")
        return
        
    final_df = pd.concat(all_dfs, ignore_index=True)
    out_file = f"data/ml_dataset_{args.days}d.csv"
    final_df.to_csv(out_file, index=False)
    print(f"Dataset successfully saved to {out_file} with {len(final_df)} records.")
    
if __name__ == "__main__":
    main()
