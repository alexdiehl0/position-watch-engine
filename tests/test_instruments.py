from position_watch import instruments
from position_watch.analysis.stock import volatility_level


def test_kinds_and_yahoo_symbols_come_from_workspace_config():
    assert instruments.kind("ETFX") == instruments.ETF
    assert instruments.kind("GOLD") == instruments.COMMODITY
    assert instruments.kind("ANYTHING") == instruments.STOCK
    assert instruments.yahoo_symbol("EURS") == "EURS.PA"
    assert instruments.yahoo_symbol("USDS") == "USDS"


def test_volatility_bands():
    assert volatility_level(None) is None
    assert volatility_level(15) == "low"
    assert volatility_level(28) == "moderate"
    assert volatility_level(35) == "high"
