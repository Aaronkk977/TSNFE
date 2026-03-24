"""
Configuration management using pydantic-settings
Supports environment variables and YAML config files
"""

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Main application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",  # Allow extra fields
    )

    # ========================================================================
    # YouTube Configuration
    # ========================================================================
    youtube_api_key: Optional[str] = None
    youtube_api_key_backup: Optional[str] = None

    # ========================================================================
    # LLM Configuration
    # ========================================================================
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None

    llm_provider: str = "gemini"  # openai, anthropic, gemini
    llm_model: str = "gemini-2.5-flash"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 9000

    # ========================================================================
    # Speech-to-Text Configuration
    # ========================================================================
    whisper_model: str = "medium"
    whisper_device: str = "cuda"  # cuda or cpu
    whisper_compute_type: str = "float16"  # float32, float16, int8
    transcription_provider: str = "gemini"  # gemini or whisper
    gemini_transcription_model: str = "gemini-2.5-flash"

    # ========================================================================
    # Processing Configuration
    # ========================================================================
    max_concurrent_downloads: int = 3
    transcription_batch_size: int = 5
    top_signals_only: Optional[int] = 10
    max_retries: int = 3
    retry_base_wait: int = 1
    retry_max_wait: int = 60

    # ========================================================================
    # Storage & Paths
    # ========================================================================
    data_dir: str = "./data"
    log_dir: str = "./logs"
    log_level: str = "INFO"
    log_to_file: bool = True
    log_to_console: bool = True

    # ========================================================================
    # Feature Flags
    # ========================================================================
    enable_caching: bool = True
    cleanup_temp_files: bool = True
    validate_stock_codes: bool = True
    enable_cost_tracking: bool = True
    stock_validation_provider: str = "fugle"  # fugle or local
    fugle_api_key: Optional[str] = None
    fugle_base_url: str = "https://api.fugle.tw/marketdata/v1.0"
    fugle_timeout_seconds: int = 10

    # ========================================================================
    # API Rate Limiting
    # ========================================================================
    daily_budget_usd: float = 100.0

    # ========================================================================
    # Proxy Configuration (Optional)
    # ========================================================================
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Create data directories if they don't exist
        self._ensure_directories()

    def _ensure_directories(self):
        """Create necessary directories."""
        directories = [
            Path(self.data_dir),
            Path(self.data_dir) / "raw",
            Path(self.data_dir) / "transcripts",
            Path(self.data_dir) / "signals",
            Path(self.data_dir) / "checkpoints",
            Path(self.data_dir) / "stock_codes",
            Path(self.data_dir) / "errors",
            Path(self.data_dir) / "metadata",
            Path(self.data_dir) / "debug",
            Path(self.log_dir),
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def data_raw_dir(self) -> Path:
        return Path(self.data_dir) / "raw"

    @property
    def data_transcripts_dir(self) -> Path:
        return Path(self.data_dir) / "transcripts"

    @property
    def data_signals_dir(self) -> Path:
        return Path(self.data_dir) / "signals"

    @property
    def data_checkpoints_dir(self) -> Path:
        return Path(self.data_dir) / "checkpoints"

    @property
    def data_stock_codes_dir(self) -> Path:
        return Path(self.data_dir) / "stock_codes"

    @property
    def data_errors_dir(self) -> Path:
        return Path(self.data_dir) / "errors"

    @property
    def data_metadata_dir(self) -> Path:
        return Path(self.data_dir) / "metadata"

    @property
    def data_debug_dir(self) -> Path:
        return Path(self.data_dir) / "debug"

    @property
    def logs_dir(self) -> Path:
        return Path(self.log_dir)


class PipelineConfig:
    """Load and manage YAML configuration."""

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = Path(config_path)
        self.data = self._load_config()

    def _load_config(self) -> dict:
        """Load YAML configuration file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get(self, key: str, default=None):
        """Get config value by dotted path (e.g., 'pipeline.max_retries')"""
        keys = key.split(".")
        value = self.data
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default

    def __getitem__(self, key: str):
        return self.get(key)

    def to_dict(self) -> dict:
        return self.data.copy()


def load_config(
    env_file: str = ".env",
    config_file: str = "config/config.yaml"
) -> tuple[Settings, PipelineConfig]:
    """Load both environment and YAML configurations."""

    # Load environment variables
    if os.path.exists(env_file):
        settings = Settings(_env_file=env_file)
    else:
        print(f"Warning: {env_file} not found, using environment variables only")
        settings = Settings()

    # Load pipeline configuration
    pipeline_config = PipelineConfig(config_file)

    return settings, pipeline_config


# Default instance (lazy-loaded)
_settings: Optional[Settings] = None
_pipeline_config: Optional[PipelineConfig] = None


def get_settings() -> Settings:
    """Get or create settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_pipeline_config() -> PipelineConfig:
    """Get or create pipeline config instance."""
    global _pipeline_config
    if _pipeline_config is None:
        _pipeline_config = PipelineConfig()
    return _pipeline_config
