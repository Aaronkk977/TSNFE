import requests
import csv
import os
import logging
import re
from html import unescape

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


def _is_supported_symbol(code: str) -> bool:
    """Keep common TW equity/ETF symbols and skip warrant-like products."""
    code = str(code or "").strip().upper()
    if not re.match(r"^\d{4,6}[A-Z]?$", code):
        return False

    # 4-digit common stock code
    if re.match(r"^\d{4}$", code):
        return True
    # 5-digit ETF/fund-like code
    if re.match(r"^\d{5}$", code):
        return True
    # 5-digit code with one suffix letter (e.g. 00981A)
    if re.match(r"^\d{5}[A-Z]$", code):
        return True
    # 6-digit codes are usually ETF/fund families when starting with 00.
    if re.match(r"^00\d{4}$", code):
        return True
    # 6-digit + suffix letter variants, keep only 00-prefix family.
    if re.match(r"^00\d{4}[A-Z]$", code):
        return True
    return False


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


def fetch_twse_isin_instruments():
    """Fetch listed instruments from TWSE ISIN page.

    The ISIN page covers common stocks, ETFs/ETNs and some special products
    (e.g. codes with trailing letters like 00981A). We parse all rows with
    valid Taiwan-style symbols and merge them into local stock codes.
    """
    try:
        response = requests.get(TWSE_ISIN_URL, timeout=30)
        response.raise_for_status()
        # TWSE ISIN page is big5-based.
        response.encoding = "big5"

        html = response.text

        def clean_text(raw: str) -> str:
            text = re.sub(r"<[^>]+>", " ", raw)
            text = unescape(text)
            text = text.replace("\u3000", " ")
            text = re.sub(r"\s+", " ", text).strip()
            return text

        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL)
        if not rows:
            return {}

        instruments = {}

        for row_html in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.IGNORECASE | re.DOTALL)
            if not cells:
                continue

            # Section titles are often single-cell rows.
            if len(cells) == 1:
                continue

            # Prefer first cell (often code + name), fallback to second cell when needed.
            primary = clean_text(cells[0])
            secondary = clean_text(cells[1]) if len(cells) > 1 else ""
            candidate = primary if primary else secondary
            if not candidate:
                continue

            # Typical formats:
            # - "006208 富邦台50"
            # - "00981A 主動統一台股增長"
            # - "2330 台積電"
            # - sometimes no space between code/name
            match = re.match(r"^([0-9]{4,6}[A-Z]?)\s*(.+)$", candidate)
            if not match:
                continue

            code = match.group(1).strip().upper()
            name = match.group(2).strip(" -")
            if not name:
                name = secondary
            if not _is_supported_symbol(code):
                continue
            if not name:
                continue

            instruments[code] = {
                "code": code,
                "name": name,
                "english_name": "",
            }

        return instruments

    except Exception as e:
        logger.warning(f"Failed to fetch instrument list from TWSE ISIN: {e}")
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

    logger.info("Fetching listed instruments from TWSE ISIN page...")
    isin_stocks = fetch_twse_isin_instruments()
    if isin_stocks:
        logger.info("Fetched %s ISIN records", len(isin_stocks))
        for code, record in isin_stocks.items():
            # TWSE file should contain listed instruments for local validation.
            twse_stocks[code] = record
            combined_stocks[code] = record
    else:
        logger.warning("No ISIN records fetched from TWSE ISIN page")

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
