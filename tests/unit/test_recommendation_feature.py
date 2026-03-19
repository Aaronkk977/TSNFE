from tw_analyst_pipeline.extraction.schemas import (
    RecommendationFeature,
    RecommendationStock,
    normalize_label,
)


def test_normalize_label_mapping():
    assert normalize_label("Strong Buy") == "買進"
    assert normalize_label("buy") == "買進"
    assert normalize_label("Neutral") == "中立"
    assert normalize_label("hold") == "中立"
    assert normalize_label("Strong Sell") == "賣出"
    assert normalize_label("sell") == "賣出"
    assert normalize_label("unknown") == "中立"


def test_recommendation_feature_schema():
    feature = RecommendationFeature(
        view_count=12345,
        recommended_stocks=[
            RecommendationStock(stock_code="2330", stock_name="台積電", label="買進"),
            RecommendationStock(stock_code="2454", stock_name="聯發科", label="中立"),
        ],
        label="買進",
    )

    assert feature.view_count == 12345
    assert len(feature.recommended_stocks) == 2
    assert feature.recommended_stocks[0].stock_code == "2330"
    assert feature.label == "買進"
