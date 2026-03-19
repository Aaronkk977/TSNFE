"""
Stock data validation and entity resolution module
Handles stock code validation and nickname mapping
"""

import csv
import json
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests

from ..utils.config import Settings
from ..utils.logging import LoggerMixin


class StockValidator(LoggerMixin):
    """Validate stock codes and resolve stock names/nicknames."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.valid_codes: Set[str] = set()
        self.stock_names: Dict[str, str] = {}
        self.aliases: Dict[str, str] = {}
        self._fugle_cache: Dict[str, bool] = {}

        # Load data
        self._load_valid_codes()
        self._load_aliases()

    def _load_valid_codes(self):
        """Load valid Taiwan stock codes from CSV files."""
        data_dir = Path(self.settings.data_stock_codes_dir)

        # Create sample stock files if they don't exist
        self._create_sample_stock_files(data_dir)

        # Load from CSV files
        for csv_file in data_dir.glob("*.csv"):
            try:
                with open(csv_file, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        code = row.get("code") or row.get("stock_code") or row.get("Code")
                        name = row.get("name") or row.get("stock_name") or row.get("Name")

                        if code:
                            code = str(code).strip().zfill(4)
                            self.valid_codes.add(code)

                            if name:
                                self.stock_names[code] = str(name).strip()

                self.logger.info(f"Loaded {len(self.valid_codes)} valid stock codes")

            except Exception as e:
                self.logger.warning(f"Failed to load stock codes from {csv_file}: {e}")

    def _load_aliases(self):
        """Load stock aliases from JSON file."""
        alias_file = Path("config/stock_aliases.json")

        if not alias_file.exists():
            self.logger.warning("Stock aliases file not found")
            return

        try:
            with open(alias_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.aliases = data.get("aliases", {})

            self.logger.info(f"Loaded {len(self.aliases)} stock aliases")

        except Exception as e:
            self.logger.warning(f"Failed to load aliases: {e}")

    def _create_sample_stock_files(self, data_dir: Path):
        """Create sample stock code CSV files if they don't exist."""
        # Sample Taiwan major stocks
        sample_stocks = [
            ("2330", "台積電", "TSMC"),
            ("2454", "聯發科", "MediaTek"),
            ("2317", "鴻海", "Foxconn"),
            ("2603", "長榮", "EVERGREEN"),
            ("1301", "台塑", "Formosa"),
            ("2412", "中華電", "Chunghwa"),
            ("1026", "台電", "TPC"),
            ("2886", "兆豐金", "Megabank"),
            ("2887", "台新金", "Taishin"),
            ("2890", "永豐金", "Yongfeng"),
            ("2882", "國泰金", "Cathay"),
            ("2891", "中信金", "CTBC"),
            ("0050", "台灣50", "Taiwan Top 50"),
            ("0056", "高股息", "Taiwan High Dividend"),
        ]

        twse_file = data_dir / "twse_stocks.csv"
        if not twse_file.exists():
            try:
                with open(twse_file, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=["code", "name", "english_name"])
                    writer.writeheader()
                    for code, name, eng_name in sample_stocks:
                        writer.writerow({
                            "code": code,
                            "name": name,
                            "english_name": eng_name,
                        })

                self.logger.info(f"Created sample stock file: {twse_file}")

            except Exception as e:
                self.logger.warning(f"Failed to create sample stock file: {e}")

    def resolve_stock_code(self, mention: str) -> Optional[str]:
        """
        Resolve a stock mention (name, nickname, code) to stock code.

        Args:
            mention: Stock name, nickname, or code

        Returns:
            4-digit stock code or None if not found
        """

        if not mention:
            return None

        mention = str(mention).strip()

        # Direct alias match
        if mention in self.aliases:
            return self.aliases[mention]

        # Exact code match (with zero-padding)
        if mention.isdigit():
            code = mention.zfill(4)
            if code in self.valid_codes:
                return code

        # Fuzzy match (substring)
        for alias, code in self.aliases.items():
            if mention.lower() in alias.lower() or alias.lower() in mention.lower():
                return code

        # Search in stock names
        for code, name in self.stock_names.items():
            if mention.lower() in name.lower() or name.lower() in mention.lower():
                return code

        return None

    def validate_stock_code(self, code: str) -> bool:
        """Check if stock code is valid."""
        if not code:
            return False

        code = str(code).strip().zfill(4)
        local_valid = code in self.valid_codes
        provider = (self.settings.stock_validation_provider or "local").lower()

        if provider != "fugle":
            return local_valid

        if not local_valid:
            return False

        if not self.settings.fugle_api_key:
            self.logger.warning("FUGLE API key not set, fallback to local validation")
            return local_valid

        return self._validate_with_fugle(code)

    def _validate_with_fugle(self, code: str) -> bool:
        if code in self._fugle_cache:
            return self._fugle_cache[code]

        endpoint = f"{self.settings.fugle_base_url}/stock/intraday/quote/{code}.TW"
        params = {"apiToken": self.settings.fugle_api_key}
        try:
            response = requests.get(
                endpoint,
                params=params,
                timeout=self.settings.fugle_timeout_seconds,
            )
            if response.status_code != 200:
                self.logger.warning(
                    f"Fugle validation failed for {code}: HTTP {response.status_code}"
                )
                self._fugle_cache[code] = False
                return False

            payload = response.json()
            data = payload.get("data") or {}
            validated = bool(data.get("symbolId") or data.get("name"))
            self._fugle_cache[code] = validated
            return validated

        except Exception as e:
            self.logger.warning(f"Fugle request failed for {code}: {e}")
            self._fugle_cache[code] = False
            return False

    def get_stock_name(self, code: str) -> Optional[str]:
        """Get stock name for a code."""
        code = str(code).strip().zfill(4)
        return self.stock_names.get(code)

    def resolve_signals(self, signals: List) -> List:
        """
        Validate and resolve signal stock codes.

        Args:
            signals: List of StockSignal objects

        Returns:
            Filtered list of valid signals
        """

        valid_signals = []

        for signal in signals:
            # Try to resolve stock code
            if not self.validate_stock_code(signal.stock_code):
                self.logger.warning(
                    f"Invalid stock code: {signal.stock_code} ({signal.stock_name})"
                )
                continue

            signal.validated = True
            signal.validation_source = (
                self.settings.stock_validation_provider or "local"
            )

            valid_signals.append(signal)

        return valid_signals
