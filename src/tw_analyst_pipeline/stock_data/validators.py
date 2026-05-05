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
        # `short_names` carries the colloquial Chinese short form when the CSV
        # provides it in the 3rd column (e.g. `5371,中強光電股份有限公司,中光電`).
        self.short_names: Dict[str, str] = {}
        self.aliases: Dict[str, str] = {}
        self._fugle_cache: Dict[str, bool] = {}
        self._pinyin_cache: Dict[str, tuple] = {}

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
                        # The 3rd column (header `english_name`) frequently
                        # holds the colloquial Chinese short form for TPEX
                        # rows (e.g. "中光電"), keep it as a separate index.
                        short = self._get_row_value(
                            row,
                            "english_name",
                            "short_name",
                            "abbreviation",
                            "abbr",
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
                                cleaned_name = str(name).strip()
                                # Fix known mojibake in upstream CSV (e.g. 宏 → U+FFFD)
                                if "\ufffd" in cleaned_name and code == "2353":
                                    cleaned_name = "宏碁"
                                self.stock_names[code] = cleaned_name

                            if short:
                                cleaned_short = str(short).strip()
                                # Only keep entries with Chinese characters; ignore
                                # English transliterations such as "TSMC".
                                if re.search(r"[\u4e00-\u9fff]", cleaned_short):
                                    self.short_names[code] = cleaned_short

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

    def _alias_file_paths(self) -> List[Path]:
        """Resolve config/stock_aliases.json from CWD and repo root."""
        candidates = [
            Path("config/stock_aliases.json"),
            Path(__file__).resolve().parents[3] / "config" / "stock_aliases.json",
        ]
        out: List[Path] = []
        seen = set()
        for p in candidates:
            try:
                r = p.resolve()
            except Exception:
                r = p
            if r in seen:
                continue
            seen.add(r)
            out.append(r)
        return out

    def _load_aliases(self):
        """Load stock aliases from JSON file."""
        alias_file = None
        for candidate in self._alias_file_paths():
            if candidate.exists():
                alias_file = candidate
                break

        if not alias_file:
            self.logger.warning("Stock aliases file not found under config/stock_aliases.json")
            return

        try:
            with open(alias_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.aliases = data.get("aliases", {})

            self.logger.info("Loaded %s stock aliases from %s", len(self.aliases), alias_file)

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

    @staticmethod
    def _relax_pinyin_syllable(syl: str) -> str:
        """Collapse Mandarin sounds that are commonly confused in speech.

        Rules applied:
            - Initials zh/ch/sh -> z/c/s
            - Initial r/n -> l (frequent confusion in Taiwan Mandarin)
            - Final -ng -> -n
        Tones are already removed because callers use Style.NORMAL.
        """
        s = (syl or "").lower()
        if not s:
            return s
        if s.startswith("zh"):
            s = "z" + s[2:]
        elif s.startswith("ch"):
            s = "c" + s[2:]
        elif s.startswith("sh"):
            s = "s" + s[2:]
        if s.startswith("r"):
            s = "l" + s[1:]
        elif s.startswith("n"):
            s = "l" + s[1:]
        if s.endswith("ng"):
            s = s[:-2] + "n"
        return s

    def _name_pinyin_relaxed(self, name: str) -> tuple:
        """Return relaxed pinyin tuple of `name` (cached).

        Returns an empty tuple when pypinyin is unavailable or the name has
        no Han characters.
        """
        if not name:
            return ()
        cached = self._pinyin_cache.get(name)
        if cached is not None:
            return cached
        try:
            import pypinyin
        except ImportError:
            self._pinyin_cache[name] = ()
            return ()
        # Only convert characters that pypinyin can handle. Non-Han chars
        # come back as themselves; we keep them so length stays comparable.
        syllables = []
        for parts in pypinyin.pinyin(name, style=pypinyin.Style.NORMAL, errors="ignore"):
            if not parts:
                continue
            syllables.append(self._relax_pinyin_syllable(parts[0]))
        out = tuple(syllables)
        self._pinyin_cache[name] = out
        return out

    def _pinyin_has_overlap(self, a: str, b: str) -> bool:
        """True iff `a` and `b` share at least one syllable under relaxed pinyin."""
        if not a or not b:
            return False
        pa = set(self._name_pinyin_relaxed(a))
        pb = set(self._name_pinyin_relaxed(b))
        if not pa or not pb:
            return False
        return bool(pa & pb)

    @staticmethod
    def _is_derivative_listing(code: str, name: str) -> bool:
        """Heuristic: warrants / structured products often contain 購/售/權 in long names."""
        name = name or ""
        if re.search(r"認購|認售|權證|牛證|熊證", name):
            return True
        if re.search(r"售\d{2}|購\d{2}", name):
            return True
        c = str(code or "").strip().upper()
        # Many Taiwan warrants use 7-char codes like 030264
        if re.match(r"^0[3-9]\d{5}[A-Z]?$", c):
            return True
        return False

    def _alias_exact_code(self, mention: str) -> Optional[str]:
        """Exact match only — must run before fuzzy resolution."""
        if not mention:
            return None
        ml = str(mention).strip().lower()
        for alias, code in self.aliases.items():
            if str(alias).strip().lower() == ml:
                return str(code).strip().upper()
        return None

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

        alias_hit = self._alias_exact_code(mention)
        if alias_hit:
            return alias_hit

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

        # Exact name match (case insensitive), skip derivative rows
        for code, name in self.stock_names.items():
            if self._is_derivative_listing(code, name):
                continue
            if name.lower() == mention_lower:
                return code

        # Exact short-name match (CSV's 3rd column). This catches
        # colloquial-but-official forms like "中光電" -> 5371 directly.
        for code, short in self.short_names.items():
            if not short:
                continue
            if self._is_derivative_listing(code, short):
                continue
            if short.lower() == mention_lower:
                return code

        # Fuzzy match alias (substring) — after exact alias already handled
        for alias, code in self.aliases.items():
            if mention_lower in alias.lower() or alias.lower() in mention_lower:
                return code

        # Fuzzy match stock names — exclude warrants; prefer shorter / closer names
        candidates: List[tuple] = []
        for code, name in self.stock_names.items():
            if self._is_derivative_listing(code, name):
                continue
            nl = name.lower()
            if mention_lower in nl or nl in mention_lower:
                candidates.append((code, name))
        if not candidates:
            return None

        def _score(item: tuple) -> tuple:
            code, name = item
            nl = name.lower()
            exact = nl == mention_lower
            starts = nl.startswith(mention_lower) or mention_lower.startswith(nl)
            return (0 if exact else 1, 0 if starts else 1, len(name), code)

        candidates.sort(key=_score)
        return candidates[0][0]

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
            # Trust any plain 4-digit numeric code even if our local CSV
            # universe is missing it (e.g. very recently listed companies).
            if re.match(r"^\d{4}$", code):
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

    def _find_homophone_code(self, name: str, include_aliases: bool = True) -> Optional[str]:
        """Match `name` against master/alias names by relaxed Mandarin pinyin.

        Order of preference:
          1. CSV short-names (3rd column) — colloquial Chinese forms.
          2. CSV full names (`stock_names`).
          3. Curated aliases from `stock_aliases.json` (only when
             `include_aliases=True`).

        Equal pinyin length plus tuple equality is required so that "穩懋"
        does not match "穩懋半導體股份有限公司" by accident.
        """
        if not name:
            return None
        target = self._name_pinyin_relaxed(name)
        if not target:
            return None

        for code, short in self.short_names.items():
            if not short or self._is_derivative_listing(code, short):
                continue
            cand = self._name_pinyin_relaxed(short)
            if cand and cand == target:
                return code

        for code, full in self.stock_names.items():
            if self._is_derivative_listing(code, full):
                continue
            cand = self._name_pinyin_relaxed(full)
            if cand and cand == target:
                return code

        if include_aliases:
            for alias, code in self.aliases.items():
                cand = self._name_pinyin_relaxed(alias)
                if cand and cand == target:
                    code_norm = str(code).strip().upper()
                    if code_norm.isdigit() and len(code_norm) < 4:
                        code_norm = code_norm.zfill(4)
                    if self.validate_stock_code(code_norm):
                        return code_norm

        return None

    def resolve_signals(self, signals: List, include_dropped: bool = False):
        """
        Validate and resolve signal stock codes.

        Args:
            signals: List of StockSignal objects

        Returns:
            Filtered list of valid signals, or (valid_signals, dropped_items)
            when include_dropped=True.
        """

        valid_signals = []
        dropped_count = 0
        dropped_items = []
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
            drop_reason = ""

            # Resolution priority (revised):
            #   1) Config alias on company name (curated, highest trust).
            #   2) Name-resolved code via resolve_stock_code (exact alias,
            #      exact name, exact short-name, fuzzy substring).
            #   3) Trust LLM's clean code when it validates: master CSV is
            #      authoritative for the name, so we override LLM's
            #      potentially-wrong stock_name with `code_expected_name`
            #      whenever available. Any plain 4-digit code passes
            #      validate_stock_code() so previously-unknown listings
            #      survive too.
            #   4) Master-name homophone (relaxed pinyin against CSV
            #      short_names + full names) — used when LLM had no
            #      usable code to anchor on.
            #   5) Alias-only homophone (curated aliases) — last resort.
            #   6) Code resolved from raw code text (legacy fallback).
            empty_name = (not orig_name) or orig_name.upper() in {"UNKNOWN", "N/A", "XXXX"}
            shares_char = bool(
                code_expected_name
                and orig_name
                and (set(orig_name) & set(code_expected_name))
            )

            alias_direct = self._alias_exact_code(orig_name)
            if alias_direct and self.validate_stock_code(alias_direct):
                resolved_code = alias_direct
                resolved_name = (
                    self.stock_names.get(alias_direct)
                    or self.short_names.get(alias_direct)
                    or orig_name
                )
                if clean_code and clean_code != alias_direct:
                    self.logger.info(
                        f"Alias overrode LLM code for '{orig_name}': {orig_code} -> {alias_direct}"
                    )

            if not resolved_code and resolved_from_name and self.validate_stock_code(resolved_from_name):
                if resolved_from_name != clean_code and clean_code:
                    self.logger.info(
                        f"Correcting LLM hallucinated code for '{orig_name}': {orig_code} -> {resolved_from_name}"
                    )
                resolved_code = resolved_from_name
                resolved_name = (
                    self.stock_names.get(resolved_code)
                    or self.short_names.get(resolved_code)
                    or orig_name
                )

            # Trust LLM's clean 4-digit-style code only when the LLM-supplied
            # name is at least loosely consistent with the master name we have
            # for that code. "Loosely consistent" =
            #   - no name supplied, OR
            #   - master CSV doesn't have a record for the code (e.g. very new
            #     listing), OR
            #   - shares ≥1 Han character with master expected name, OR
            #   - shares ≥1 syllable (relaxed pinyin) with master expected name.
            #
            # Without this check we would route LLM hallucinations like
            # "管宇" → 2388 (威盛) instead of letting master homophone find
            # the real 廣宇 (2381).
            if not resolved_code and clean_code and self.validate_stock_code(clean_code):
                pinyin_overlap = self._pinyin_has_overlap(orig_name, code_expected_name) if code_expected_name else False
                trust_llm_code = (
                    empty_name
                    or not code_expected_name
                    or shares_char
                    or pinyin_overlap
                )
                if trust_llm_code:
                    resolved_code = clean_code
                    resolved_name = (
                        code_expected_name
                        or self.short_names.get(clean_code)
                        or orig_name
                    )
                    if (
                        code_expected_name
                        and orig_name
                        and not shares_char
                        and orig_name != code_expected_name
                    ):
                        self.logger.info(
                            f"Trusting LLM code; replacing hallucinated name "
                            f"'{orig_name}' with master '{code_expected_name}' for {clean_code} "
                            f"(pinyin_overlap={pinyin_overlap})"
                        )

            # Master-name homophone — try when we still have no anchor code.
            # This catches both:
            #   (a) LLM code hallucinations where the name is the truthful
            #       signal (e.g. 管宇 → 2381 廣宇), and
            #   (b) Speech-recognition typos where the LLM also fails to
            #       supply a code (e.g. 文貌 → 3105 穩懋).
            if not resolved_code:
                homophone_master = self._find_homophone_code(orig_name, include_aliases=False)
                if homophone_master and self.validate_stock_code(homophone_master):
                    resolved_code = homophone_master
                    resolved_name = (
                        self.stock_names.get(resolved_code)
                        or self.short_names.get(resolved_code)
                        or orig_name
                    )
                    self.logger.info(
                        f"Correcting by master homophone: {orig_name} -> {resolved_name} ({resolved_code})"
                    )

            if not resolved_code:
                homophone_alias = self._find_homophone_code(orig_name, include_aliases=True)
                if homophone_alias and self.validate_stock_code(homophone_alias):
                    resolved_code = homophone_alias
                    resolved_name = (
                        self.stock_names.get(resolved_code)
                        or self.short_names.get(resolved_code)
                        or orig_name
                    )
                    self.logger.info(
                        f"Correcting by alias homophone: {orig_name} -> {resolved_name} ({resolved_code})"
                    )

            if (
                not resolved_code
                and resolved_from_code
                and self.validate_stock_code(resolved_from_code)
                and resolved_from_code != clean_code
            ):
                # Only meaningful when raw code text contained extras and resolved_from_code
                # differs from clean_code; still gate by the same name-consistency.
                rfc_expected = self.stock_names.get(resolved_from_code)
                rfc_shares = bool(
                    rfc_expected and orig_name and (set(orig_name) & set(rfc_expected))
                )
                if empty_name or rfc_shares:
                    resolved_code = resolved_from_code
                    resolved_name = rfc_expected or orig_name

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
                if (
                    clean_code
                    and self.validate_stock_code(clean_code)
                    and code_expected_name
                    and orig_name
                    and not (set(orig_name) & set(code_expected_name))
                    and not self._pinyin_has_overlap(orig_name, code_expected_name)
                ):
                    drop_reason = (
                        f"code_name_mismatch:{clean_code}({code_expected_name}) "
                        f"!= name({orig_name})"
                    )
                elif clean_code and not self.validate_stock_code(clean_code):
                    drop_reason = f"invalid_code:{clean_code}"
                elif resolved_from_name and not self.validate_stock_code(resolved_from_name):
                    drop_reason = f"name_resolved_to_invalid_code:{resolved_from_name}"
                else:
                    drop_reason = "unresolvable_name_and_code"
                self.logger.warning(
                    f"Invalid or unresolvable stock: code='{signal.stock_code}' name='{signal.stock_name}'"
                )
                signal.validated = False
                signal.validation_source = (
                    self.settings.stock_validation_provider or "local"
                )
                dropped_count += 1
                if include_dropped:
                    dropped_items.append(
                        {
                            "original_stock_code": orig_code,
                            "original_stock_name": orig_name,
                            "resolved_from_name": resolved_from_name,
                            "resolved_from_code": resolved_from_code,
                            "reason": drop_reason,
                            "validation_source": self.settings.stock_validation_provider or "local",
                            "action": getattr(getattr(signal, "action", None), "value", getattr(signal, "action", None)),
                            "normalized_label": getattr(signal, "normalized_label", None),
                            "implied_label": getattr(signal, "implied_label", None),
                        }
                    )

        valid_signals = list(deduped.values())

        if dropped_count:
            self.logger.info("Dropped %s invalid signals during validation", dropped_count)

        if include_dropped:
            return valid_signals, dropped_items
        return valid_signals
