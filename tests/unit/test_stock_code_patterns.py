from tw_analyst_pipeline.extraction.schemas import RecommendationStock, StockSignal


def test_stock_signal_accepts_six_digit_etf_code():
    signal = StockSignal(
        stock_code="006208",
        stock_name="富邦台50",
        action="buy",
        confidence=0.9,
        reasoning="持續買進市值型ETF，累積財富。",
    )

    assert signal.stock_code == "006208"


def test_recommendation_stock_accepts_six_digit_etf_code():
    stock = RecommendationStock(
        stock_code="006208",
        stock_name="富邦台50",
        label="買進",
    )

    assert stock.stock_code == "006208"
