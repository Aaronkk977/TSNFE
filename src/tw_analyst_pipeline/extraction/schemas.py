"""
Data models for stock signal extraction using Pydantic v2
Provides strict validation and JSON schema generation
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class TradeAction(str, Enum):
    """Stock trading action enum."""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    UNKNOWN = "unknown"


def normalize_label(label: Optional[str]) -> str:
    """Normalize label to Chinese label set: 買進/中立/賣出."""
    if not label:
        return "中立"

    normalized = str(label).strip().lower().replace("_", " ")
    buy_aliases = {
        "buy", "strong buy", "bullish", "加碼", "買進", "看多", "long"
    }
    sell_aliases = {
        "sell", "strong sell", "bearish", "減碼", "賣出", "看空", "short"
    }
    hold_aliases = {
        "hold", "neutral", "中立", "觀望", "持有", "wait"
    }

    if normalized in buy_aliases:
        return "買進"
    if normalized in sell_aliases:
        return "賣出"
    if normalized in hold_aliases:
        return "中立"
    return "中立"


class StockSignal(BaseModel):
    """Single stock signal extracted from analyst video."""

    stock_code: str = Field(..., pattern=r"^\d{4}$", description="4-digit Taiwan stock code")
    stock_name: str = Field(..., description="Stock name or company name")
    action: TradeAction = Field(..., description="Trading action: buy, sell, or hold")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    reasoning: str = Field(..., max_length=500, description="Analyst's reasoning in 1-2 sentences")
    mentioned_price: Optional[float] = Field(None, description="Target price mentioned if any")
    technical_indicators: Optional[List[str]] = Field(
        None, description="Technical indicators mentioned"
    )
    sentiment_score: Optional[float] = Field(
        None, ge=0.0, le=10.0, description="Sentiment score 0-10"
    )
    urgency: Optional[float] = Field(
        None, ge=0.0, le=10.0, description="Urgency score 0-10"
    )
    implied_label: Optional[str] = Field(None, description="Raw label from LLM")
    normalized_label: Optional[str] = Field(
        None,
        description="Normalized Chinese label: 買進/中立/賣出",
    )
    validated: bool = Field(default=False, description="Validated by stock data API")
    validation_source: Optional[str] = Field(
        None,
        description="Validation source provider",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "stock_code": "2330",
                    "stock_name": "台積電",
                    "action": "buy",
                    "confidence": 0.8,
                    "reasoning": "法說會展望佳，產能滿載",
                    "mentioned_price": 1500.0,
                    "technical_indicators": ["突破"],
                }
            ]
        }
    }


class VideoAnalysis(BaseModel):
    """Complete analysis result for a single video."""

    video_id: str = Field(..., description="YouTube video ID")
    analyst_name: Optional[str] = Field(None, description="Analyst name if identifiable")
    signals: List[StockSignal] = Field(default_factory=list, description="Extracted signals")
    market_outlook: Optional[str] = Field(None, description="Analyst's overall market view")
    processed_at: datetime = Field(default_factory=datetime.utcnow, description="Processing timestamp")
    processing_duration_seconds: Optional[float] = Field(None, description="Pipeline processing time")
    transcript_length_chars: Optional[int] = Field(None, description="Transcript character count")
    confidence_score: Optional[float] = Field(
        None, description="Overall confidence 0.0-1.0"
    )
    sentiment_score: Optional[float] = Field(None, ge=0.0, le=10.0)
    urgency: Optional[float] = Field(None, ge=0.0, le=10.0)
    implied_label: Optional[str] = Field(None, description="Raw overall label")
    normalized_label: Optional[str] = Field(
        None,
        description="Normalized overall label: 買進/中立/賣出",
    )
    video_view_count: Optional[int] = Field(None, ge=0)
    video_published_at: Optional[str] = Field(None)
    recommendation_feature: Optional["RecommendationFeature"] = Field(default=None)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "video_id": "abc12345xyz",
                    "analyst_name": "股市分析師",
                    "signals": [
                        {
                            "stock_code": "2330",
                            "stock_name": "台積電",
                            "action": "buy",
                            "confidence": 0.8,
                            "reasoning": "法說會展望佳",
                            "mentioned_price": 1500.0,
                            "technical_indicators": None,
                        }
                    ],
                    "market_outlook": "看多台股未來走勢",
                    "processed_at": "2024-01-15T10:30:00",
                    "processing_duration_seconds": 120.5,
                    "transcript_length_chars": 15000,
                    "confidence_score": 0.75,
                }
            ]
        }
    }


class TranscriptResult(BaseModel):
    """Transcription result from Whisper."""

    video_id: str = Field(..., description="YouTube video ID")
    text: str = Field(..., description="Full transcript text")
    segments: List[dict] = Field(default_factory=list, description="Segments with timestamps")
    language: str = Field(default="zh", description="Detected/specified language")
    duration_seconds: Optional[float] = Field(None, description="Audio duration")
    processing_time_seconds: Optional[float] = Field(None, description="Transcription time")

    class Config:
        arbitrary_types_allowed = True


class RecommendationStock(BaseModel):
    """Stock item for recommendation feature list."""

    stock_code: str = Field(..., pattern=r"^\d{4}$")
    stock_name: str = Field(...)
    label: str = Field(..., description="買進/中立/賣出")


class RecommendationFeature(BaseModel):
    """Per-video recommendation feature list required by downstream analytics."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    view_count: int = Field(default=0, ge=0)
    recommended_stocks: List[RecommendationStock] = Field(default_factory=list)
    label: str = Field(default="中立", description="影片整體標籤：買進/中立/賣出")


VideoAnalysis.model_rebuild()


class ProcessingError(BaseModel):
    """Error information during processing."""

    video_id: str = Field(..., description="YouTube video ID")
    stage: str = Field(..., description="Pipeline stage where error occurred")
    error_type: str = Field(..., description="Exception type")
    error_message: str = Field(..., description="Error message")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    is_recoverable: bool = Field(default=False, description="Whether error can be retried")


class CostMetrics(BaseModel):
    """API cost tracking metrics."""

    video_id: str = Field(..., description="YouTube video ID")
    input_tokens: int = Field(default=0, description="Input tokens to LLM")
    output_tokens: int = Field(default=0, description="Output tokens from LLM")
    estimated_usd: float = Field(default=0.0, description="Estimated API cost in USD")
    processing_time_seconds: float = Field(default=0.0)
    signals_extracted: int = Field(default=0, description="Number of signals extracted")

    @property
    def cost_per_signal(self) -> float:
        """Calculate cost per extracted signal."""
        if self.signals_extracted == 0:
            return self.estimated_usd
        return self.estimated_usd / self.signals_extracted
