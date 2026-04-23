#!/usr/bin/env python3
"""ETL-06: Convert daily report outputs to ML dataset."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_analyst_pipeline.stock_data.market_data import MarketDataClient
from tw_analyst_pipeline.utils.config import get_settings


def process_daily_folder(date_folder: Path, market_client: MarketDataClient, wait_days: int) -> pd.DataFrame:
    summary_files = list(date_folder.glob("daily_run_summary_*.json"))
    csv_files = list(date_folder.glob("analyst_stock_matrix_*.csv"))

    if not summary_files or not csv_files:
        return pd.DataFrame()

    summary_file = max(summary_files, key=lambda p: p.stat().st_mtime)
    csv_file = max(csv_files, key=lambda p: p.stat().st_mtime)
    date_str = date_folder.name

    with open(summary_file, "r", encoding="utf-8") as f:
        summary_data = json.load(f)
    rankings = summary_data.get("stock_rankings", {})

    df_matrix = pd.read_csv(csv_file, index_col=0)

    records = []
    stocks = df_matrix.columns.tolist()

    for stock_col in stocks:
        parts = str(stock_col).split(" ", 1)
        if not parts[0].isdigit():
            continue
        stock_code = parts[0]

        col_data = df_matrix[stock_col]
        buy_count = int((col_data == "買進").sum())
        sell_count = int((col_data == "賣出").sum())

        if buy_count == 0 and sell_count == 0:
            continue

        is_top_rec = int(any(s.get("stock_code") == stock_code for s in rankings.get("most_recommended_by_analysts", [])))
        is_top_not_rec = int(any(s.get("stock_code") == stock_code for s in rankings.get("most_not_recommended_by_analysts", [])))

        view_sum_rec = 0
        for s in rankings.get("most_viewed_recommended_stocks", []):
            if s.get("stock_code") == stock_code:
                view_sum_rec = s.get("view_sum", 0)
                break

        view_sum_not_rec = 0
        for s in rankings.get("most_viewed_not_recommended_stocks", []):
            if s.get("stock_code") == stock_code:
                view_sum_not_rec = s.get("view_sum", 0)
                break

        fwd_return = market_client.get_forward_return(stock_code, date_str, n_days=wait_days)

        records.append(
            {
                "date": date_str,
                "stock_code": stock_code,
                "buy_count": buy_count,
                "sell_count": sell_count,
                "is_top_recommended": is_top_rec,
                "is_top_not_recommended": is_top_not_rec,
                "recommended_view_sum": view_sum_rec,
                "not_recommended_view_sum": view_sum_not_rec,
                f"return_{wait_days}d": fwd_return,
            }
        )

    return pd.DataFrame(records)


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL-06 sync ML dataset")
    parser.add_argument("--days", type=int, default=5, help="Forward days for return target")
    parser.add_argument("--subfolder", type=str, default=None, help="Override PIPELINE_OUTPUT_SUBFOLDER")
    args = parser.parse_args()

    settings = get_settings()
    market_client = MarketDataClient(settings)

    subfolder = args.subfolder or os.environ.get("PIPELINE_OUTPUT_SUBFOLDER", "daily")
    daily_reports_dir = settings.data_reports_dir / subfolder

    if not daily_reports_dir.exists():
        print(f"Directory {daily_reports_dir} does not exist.")
        return 1

    all_dates = sorted([d for d in daily_reports_dir.iterdir() if d.is_dir()])
    all_dfs = []
    print(f"Building dataset mapping signals to {args.days}-day forward returns...")

    for date_folder in tqdm(all_dates):
        df_day = process_daily_folder(date_folder, market_client, args.days)
        if not df_day.empty:
            all_dfs.append(df_day)

    if not all_dfs:
        print("No valid data constructed.")
        return 1

    final_df = pd.concat(all_dfs, ignore_index=True)
    out_file = Path(settings.data_dir) / f"ml_dataset_{args.days}d.csv"
    final_df.to_csv(out_file, index=False)
    print(f"Dataset successfully saved to {out_file} with {len(final_df)} records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
