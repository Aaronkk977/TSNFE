import requests
import csv
import os
import logging
import re
from io import StringIO

import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TWSE_EQUITIES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_EQUITIES_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TWSE_ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
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


def fetch_twse_etf_etn_list():
    """Fetch ETF/ETN listings from TWSE ISIN page.

    The official stock open APIs mainly cover listed/OTC companies. ETF/ETN symbols
    are more complete on the ISIN list page, so we merge them into local stock codes.
    """
    try:
        response = requests.get(TWSE_ISIN_URL, timeout=30)
        response.raise_for_status()
        # TWSE ISIN page is typically big5-based. Fall back to apparent encoding.
        response.encoding = response.apparent_encoding or "big5"

        tables = pd.read_html(StringIO(response.text), flavor="lxml")
        if not tables:
            return {}

        df = tables[0]
        if df.empty or df.shape[1] == 0:
            return {}

        etf_like = {}
        current_section = ""

        for raw_value in df.iloc[:, 0].tolist():
            value = str(raw_value or "").replace("\u3000", " ").strip()
            if not value or value.lower() == "nan":
                continue

            # Section titles in this table include strings like
            # "受益證券-ETF" / "受益證券-ETN" / "...槓桿/反向...".
            if "ETF" in value or "ETN" in value:
                current_section = value
                continue

            # Skip until ETF/ETN sections begin.
            if not current_section:
                continue

            # When section changes to non ETF/ETN groups, stop collecting.
            if "ETF" not in current_section and "ETN" not in current_section:
                continue

            # First column stores code+name, e.g. "006208 富邦台50".
            match = re.match(r"^([0-9]{4,6}[A-Z]?)\s+(.+)$", value)
            if not match:
                continue

            code = match.group(1).strip().upper()
            name = match.group(2).strip()
            if not re.match(r"^\d{4,6}[A-Z]?$", code):
                continue
            if not name:
                continue

            etf_like[code] = {
                "code": code,
                "name": name,
                "english_name": "",
            }

        return etf_like

    except Exception as e:
        logger.warning(f"Failed to fetch ETF/ETN list from TWSE ISIN: {e}")
        return {}

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

    logger.info("Fetching ETF/ETN instruments from TWSE ISIN page...")
    etf_etn_stocks = fetch_twse_etf_etn_list()
    if etf_etn_stocks:
        logger.info("Fetched %s ETF/ETN records", len(etf_etn_stocks))
        for code, record in etf_etn_stocks.items():
            # TWSE file should contain listed ETFs/ETNs for local validation.
            twse_stocks[code] = record
            combined_stocks[code] = record
    else:
        logger.warning("No ETF/ETN records fetched from TWSE ISIN page")

    # Prepare directories
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Keep a tiny fallback list in case remote ETF source is temporarily unavailable.
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
