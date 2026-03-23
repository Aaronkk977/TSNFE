import requests
import csv
import os
import json
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TWSE_EQUITIES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_EQUITIES_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "stock_codes", "twse_stocks.csv")

def fetch_json(url):
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch from {url}: {e}")
        return []

def main():
    logger.info("Fetching TWSE listed companies...")
    twse_data = fetch_json(TWSE_EQUITIES_URL)
    
    logger.info("Fetching TPEX OTC companies...")
    tpex_data = fetch_json(TPEX_EQUITIES_URL)

    # Some data might not have english names readily available in these APIs, handle gracefully
    combined_stocks = {}
    
    for item in twse_data:
        code = item.get('公司代號')
        name = item.get('公司名稱')
        if code and name:
            combined_stocks[code] = {'code': code, 'name': name, 'english_name': item.get('英文簡稱', '')}

    for item in tpex_data:
        code = item.get('公司代號')
        name = item.get('公司名稱')
        # Skip overriding if it already exists (unlikely, but safe)
        if code and name and code not in combined_stocks:
            combined_stocks[code] = {'code': code, 'name': name, 'english_name': item.get('英文簡稱', '')}

    # Prepare directories
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    
    # Save to CSV
    # Ensure important indices (ETFs like 0050) are also added if not present
    default_etfs = [
        {'code': '0050', 'name': '元大台灣50', 'english_name': 'Yuanta Taiwan 50'},
        {'code': '0056', 'name': '元大高股息', 'english_name': 'Yuanta High Dividend'},
        {'code': '00878', 'name': '國泰永續高股息', 'english_name': 'Cathay ESG ETF'},
        {'code': '00929', 'name': '復華台灣科技優息', 'english_name': 'Fuh Hwa Taiwan Tech Dividend'}
    ]
    for etf in default_etfs:
        if etf['code'] not in combined_stocks:
            combined_stocks[etf['code']] = etf

    # Sort primarily by stock code
    sorted_stocks = sorted(combined_stocks.values(), key=lambda x: str(x['code']))
    
    logger.info(f"Saving {len(sorted_stocks)} records to {OUTPUT_FILE} ...")
    
    with open(OUTPUT_FILE, mode='w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['code', 'name', 'english_name'])
        for stock in sorted_stocks:
            writer.writerow([stock['code'], stock['name'], stock['english_name']])
            
    logger.info("Stock list update completed successfully.")

if __name__ == "__main__":
    main()
