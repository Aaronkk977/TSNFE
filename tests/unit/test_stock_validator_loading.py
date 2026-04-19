from tw_analyst_pipeline.stock_data.validators import StockValidator
from tw_analyst_pipeline.utils.config import Settings


def test_local_validator_loads_six_digit_etf_code(tmp_path):
    stock_dir = tmp_path / "data" / "stock_codes"
    stock_dir.mkdir(parents=True)

    csv_content = "code,name,english_name\n006208,富邦台50,Fubon Taiwan 50\n2330,台積電,TSMC\n"
    (stock_dir / "all_stocks.csv").write_text(csv_content, encoding="utf-8")

    settings = Settings(data_dir=str(tmp_path / "data"), stock_validation_provider="local")
    validator = StockValidator(settings)

    assert "006208" in validator.valid_codes
    assert validator.resolve_stock_code("006208") == "006208"
    assert validator.validate_stock_code("006208") is True


def test_local_validator_handles_bom_and_flexible_headers(tmp_path):
    stock_dir = tmp_path / "data" / "stock_codes"
    stock_dir.mkdir(parents=True)

    csv_content = "\ufeff Code , Stock_Name \n006208,富邦台50\n"
    (stock_dir / "etf_codes.csv").write_text(csv_content, encoding="utf-8")

    settings = Settings(data_dir=str(tmp_path / "data"), stock_validation_provider="local")
    validator = StockValidator(settings)

    assert "006208" in validator.valid_codes
    assert validator.stock_names.get("006208") == "富邦台50"
