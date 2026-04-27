"""
Stock data validation and entity resolution module
Handles stock code validation and nickname mapping
"""

import csv
import json
import re
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
        data_dirs = self._candidate_stock_code_dirs()
        csv_files = []
        seen_files = set()
        for data_dir in data_dirs:
            for csv_file in sorted(data_dir.glob("*.csv")):
                resolved = csv_file.resolve()
                if resolved in seen_files:
                    continue
                seen_files.add(resolved)
                csv_files.append(csv_file)

        if not csv_files:
            self.logger.warning(
                "No stock code CSV found under %s. Stock validation will reject all local-unknown codes.",
                ", ".join(str(p) for p in data_dirs),
            )
            return

        loaded_files = 0
        # Load from CSV files
        for csv_file in csv_files:
            try:
                with open(csv_file, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    file_loaded = 0
                    for row in reader:
                        code = self._get_row_value(
                            row,
                            "code",
                            "stock_code",
                            "ticker",
                            "symbol",
                        )
                        name = self._get_row_value(
                            row,
                            "name",
                            "stock_name",
                            "company_name",
                        )

                        if code:
                            code = str(code).strip().upper()
                            if code.isdigit() and len(code) < 4:
                                code = code.zfill(4)
                            if not re.match(r"^\d{4,6}[A-Z]?$", code):
                                continue

                            self.valid_codes.add(code)
                            file_loaded += 1

                            if name:
                                self.stock_names[code] = str(name).strip()

                    if file_loaded == 0:
                        self.logger.warning(
                            "No valid stock codes parsed from %s; please verify CSV headers include code/stock_code.",
                            csv_file,
                        )
                loaded_files += 1

            except Exception as e:
                self.logger.warning(f"Failed to load stock codes from {csv_file}: {e}")

        self.logger.info(
            "Loaded %s valid stock codes from %s CSV files",
            len(self.valid_codes),
            loaded_files,
        )

        if len(self.valid_codes) < 1000:
            self.logger.warning(
                "Stock universe is unusually small (%s). Please provide full TWSE/TPEX/ETF code lists.",
                len(self.valid_codes),
            )

    def _candidate_stock_code_dirs(self) -> List[Path]:
        configured_dir = Path(self.settings.data_stock_codes_dir)
        candidates: List[Path] = []

        # Keep configured behavior first.
        candidates.append(configured_dir)

        # If configured path is relative, also resolve from current working dir and repo root.
        if not configured_dir.is_absolute():
            candidates.append((Path.cwd() / configured_dir).resolve())
            repo_root = Path(__file__).resolve().parents[3]
            candidates.append((repo_root / configured_dir).resolve())

        existing: List[Path] = []
        seen = set()
        for path in candidates:
            try:
                resolved = path.resolve()
            except Exception:
                resolved = path
            if resolved in seen:
                continue
            seen.add(resolved)
            if resolved.exists() and resolved.is_dir():
                existing.append(resolved)

        # Return all candidates if none exists to keep warning message informative.
        return existing or candidates

    @staticmethod
    def _get_row_value(row: Dict[str, str], *candidate_keys: str) -> Optional[str]:
        if not isinstance(row, dict):
            return None

        normalized = {
            str(k).replace("\ufeff", "").strip().lower(): v
            for k, v in row.items()
            if k is not None
        }
        for key in candidate_keys:
            value = normalized.get(key.lower())
            if value is not None and str(value).strip() != "":
                return str(value).strip()
        return None

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
            4-digit or custom string stock code, or None if not found
        """

        if not mention:
            return None

        mention = str(mention).strip()
        mention_lower = mention.lower()

        # Direct alias match (exact)
        for alias, code in self.aliases.items():
            if alias.lower() == mention_lower:
                return code

        # Exact code match
        if mention.isdigit():
            code = mention.zfill(4)
            if code in self.valid_codes:
                return code
            if mention.upper() in self.valid_codes:
                return mention.upper()
        else:
            if mention.upper() in self.valid_codes:
                return mention.upper()

        # Exact name match (case insensitive)
        for code, name in self.stock_names.items():
            if name.lower() == mention_lower:
                return code

        # Fuzzy match alias (substring)
        for alias, code in self.aliases.items():
            if mention_lower in alias.lower() or alias.lower() in mention_lower:
                return code

        # Fuzzy match stock names (substring)
        for code, name in self.stock_names.items():
            if mention_lower in name.lower() or name.lower() in mention_lower:
                return code

        return None

    def validate_stock_code(self, code: str) -> bool:
        """Check if stock code is valid."""
        if not code:
            return False

        code = str(code).strip().upper()
        if code.isdigit():
            code = code.zfill(4)

        local_valid = code in self.valid_codes

        provider = (self.settings.stock_validation_provider or "local").lower()

        if provider != "fugle":
            # Allow ETF patterns (usually starting with 00) to pass validation
            if code.startswith("00") and re.match(r"^\d{4,6}[A-Z]?$", code):
                return True
            return local_valid

        if not self.settings.fugle_api_key:
            self.logger.warning("FUGLE API key not set, fallback to local validation")
            return local_valid

        if local_valid:
            return True

        if not re.match(r"^\d{4,6}[A-Z]?$", code):
            return False

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
        code = str(code).strip().upper()
        if code.isdigit():
            code = code.zfill(4)
        return self.stock_names.get(code)

    def _find_homophone_code(self, name: str) -> Optional[str]:
        try:
            import pypinyin
            name_pinyin = [p[0] for p in pypinyin.pinyin(name, style=pypinyin.Style.NORMAL)]
            for code, s_name in self.stock_names.items():
                s_name_pinyin = [p[0] for p in pypinyin.pinyin(s_name, style=pypinyin.Style.NORMAL)]
                if len(name_pinyin) == len(s_name_pinyin) and len(name_pinyin) > 0 and name_pinyin == s_name_pinyin:
                    return code
        except ImportError:
            pass
        return None

    def resolve_signals(self, signals: List) -> List:
        """
        Validate and resolve signal stock codes.

        Args:
            signals: List of StockSignal objects

        Returns:
            Filtered list of valid signals
        """

        valid_signals = []
        dropped_count = 0
        from collections import OrderedDict
        deduped = OrderedDict()

        for signal in signals:
            resolved_from_name = self.resolve_stock_code(signal.stock_name)
            resolved_from_code = self.resolve_stock_code(signal.stock_code)
            
            resolved_code = None
            resolved_name = signal.stock_name
            
            orig_name = str(signal.stock_name or "").strip()
            orig_code = str(signal.stock_code or "").strip()
            clean_code = orig_code.zfill(4) if orig_code.isdigit() else orig_code
            code_expected_name = self.stock_names.get(clean_code)

            if resolved_from_name and clean_code and resolved_from_name == clean_code:
                # Both name and code match
                resolved_code = clean_code
                resolved_name = self.stock_names.get(resolved_code, orig_name)
            elif resolved_from_name and self.validate_stock_code(resolved_from_name):
                # Name can be used to find a code, replace the wrong code
                if resolved_from_name != orig_code:
                    self.logger.info(
                        f"Correcting LLM hallucinated code for '{orig_name}': {orig_code} -> {resolved_from_name}"
                    )
                resolved_code = resolved_from_name
                resolved_name = self.stock_names.get(resolved_code, orig_name)
            else:
                homophone_code = self._find_homophone_code(orig_name)
                if homophone_code and self.validate_stock_code(homophone_code):
                    # Name is a homophone with another company
                    resolved_code = homophone_code
                    resolved_name = self.stock_names.get(resolved_code, orig_name)
                    self.logger.info(
                        f"Correcting by homophone: {orig_name} -> {resolved_name} ({resolved_code})"
                    )
                elif code_expected_name and len(set(orig_name) & set(code_expected_name)) >= 1:
                    # Code corresponds to a company that shares at least one char with name
                    resolved_code = clean_code
                    resolved_name = code_expected_name
                    self.logger.info(
                        f"Corrected name '{orig_name}' to '{resolved_name}' matching code {resolved_code} (shares character)"
                    )
                elif resolved_from_code and self.validate_stock_code(resolved_from_code):
                    resolved_code = resolved_from_code

            if resolved_code and self.validate_stock_code(resolved_code):
                signal.stock_code = resolved_code
                signal.stock_name = resolved_name
                signal.validated = True
                signal.validation_source = (
                    self.settings.stock_validation_provider or "local"
                )
                
                # Check for duplicates based on code
                if resolved_code in deduped:
                    existing_sig = deduped[resolved_code]
                    if getattr(existing_sig, "confidence", 0) < getattr(signal, "confidence", 0):
                        new_reason = existing_sig.reasoning + " | " + signal.reasoning if existing_sig.reasoning != signal.reasoning else signal.reasoning
                        signal.reasoning = new_reason
                        deduped[resolved_code] = signal
                    else:
                        new_reason = existing_sig.reasoning + " | " + signal.reasoning if existing_sig.reasoning != signal.reasoning else existing_sig.reasoning
                        existing_sig.reasoning = new_reason
                else:
                    deduped[resolved_code] = signal
            else:
                self.logger.warning(
                    f"Invalid or unresolvable stock: code='{signal.stock_code}' name='{signal.stock_name}'"
                )
                signal.validated = False
                signal.validation_source = (
                    self.settings.stock_validation_provider or "local"
                )
                dropped_count += 1

        valid_signals = list(deduped.values())

        if dropped_count:
            self.logger.info("Dropped %s invalid signals during validation", dropped_count)

        return valid_signals
