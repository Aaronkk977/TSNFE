import requests
import csv
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TWSE_EQUITIES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_EQUITIES_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "stock_codes")
TWSE_OUTPUT_FILE = os.path.join(OUTPUT_DIR, "twse_stocks.csv")
TPEX_OUTPUT_FILE = os.path.join(OUTPUT_DIR, "tpex_stocks.csv")
ALL_OUTPUT_FILE = os.path.join(OUTPUT_DIR, "all_stocks.csv")


def _extract_code_name(item):
    code = (
        item.get('公司代號')
        or item.get('SecuritiesCompanyCode')
        or item.get('股票代號')
        or item.get('代號')
        or item.get('code')
    )
    name = (
        item.get('公司名稱')
        or item.get('CompanyName')
        or item.get('SecuritiesCompanyName')
        or item.get('股票名稱')
        or item.get('名稱')
        or item.get('name')
    )
    english_name = (
        item.get('英文簡稱')
        or item.get('CompanyAbbreviation')
        or item.get('EnglishAbbreviation')
        or item.get('english_name')
        or ""
    )

    if not code or not name:
        return None

    return {
        'code': str(code).strip(),
        'name': str(name).strip(),
        'english_name': str(english_name).strip(),
    }


def _write_csv(path, rows):
    with open(path, mode='w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['code', 'name', 'english_name'])
        for stock in rows:
            writer.writerow([stock['code'], stock['name'], stock.get('english_name', '')])

def fetch_json(url):
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        if response.encoding is None or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch from {url}: {e}")
        return []

def main():
    logger.info("Fetching TWSE listed companies...")
    twse_data = fetch_json(TWSE_EQUITIES_URL)
    
    logger.info("Fetching TPEX OTC companies...")
    tpex_data = fetch_json(TPEX_EQUITIES_URL)

    twse_stocks = {}
    for item in twse_data:
        parsed = _extract_code_name(item)
        if parsed:
            twse_stocks[parsed['code']] = parsed

    tpex_stocks = {}
    for item in tpex_data:
        parsed = _extract_code_name(item)
        if parsed:
            tpex_stocks[parsed['code']] = parsed

    combined_stocks = {}
    combined_stocks.update(twse_stocks)
    combined_stocks.update(tpex_stocks)

    # Prepare directories
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Ensure important ETFs are also added if not present
    default_etfs = [
        {'code': '0050', 'name': '元大台灣50', 'english_name': 'Yuanta Taiwan 50'},
        {'code': '0056', 'name': '元大高股息', 'english_name': 'Yuanta High Dividend'},
        {'code': '00878', 'name': '國泰永續高股息', 'english_name': 'Cathay ESG ETF'},
        {'code': '00929', 'name': '復華台灣科技優息', 'english_name': 'Fuh Hwa Taiwan Tech Dividend'}
    ]
    for etf in default_etfs:
        code = etf['code']
        if code not in twse_stocks:
            twse_stocks[code] = etf
        if code not in combined_stocks:
            combined_stocks[code] = etf

    # Sort by stock code
    sorted_twse = sorted(twse_stocks.values(), key=lambda x: str(x['code']))
    sorted_tpex = sorted(tpex_stocks.values(), key=lambda x: str(x['code']))
    sorted_stocks = sorted(combined_stocks.values(), key=lambda x: str(x['code']))

    logger.info(f"Saving {len(sorted_twse)} TWSE records to {TWSE_OUTPUT_FILE} ...")
    _write_csv(TWSE_OUTPUT_FILE, sorted_twse)

    logger.info(f"Saving {len(sorted_tpex)} TPEX records to {TPEX_OUTPUT_FILE} ...")
    _write_csv(TPEX_OUTPUT_FILE, sorted_tpex)

    logger.info(f"Saving {len(sorted_stocks)} merged records to {ALL_OUTPUT_FILE} ...")
    _write_csv(ALL_OUTPUT_FILE, sorted_stocks)

    logger.info("Stock list update completed successfully (TWSE + TPEX + ALL).")

if __name__ == "__main__":
    main()
