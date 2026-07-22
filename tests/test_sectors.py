"""Category bucketing and grouped report rendering."""

from bot.alerts import scan_report
from bot.sectors import category, sort_key


def test_broad_tech_bucket():
    assert category("NVDA", "Information Technology") == "Tech"
    assert category("GOOGL", "Communication Services") == "Tech"
    assert category("HIMS", "Technology") == "Tech"  # Yahoo's IT label
    # Hand-picked extras override their official sector:
    assert category("AMZN", "Consumer Discretionary") == "Tech"
    assert category("TSLA", "Consumer Discretionary") == "Tech"


def test_non_tech_and_yahoo_normalization():
    assert category("BLK", "Financials") == "Financials"
    assert category("SOFI", "Financial Services") == "Financials"   # Yahoo
    assert category("XYZ", "Consumer Cyclical") == "Consumer Discretionary"
    assert category("ABC", "Healthcare") == "Health Care"
    assert category("MISSING", None) == "Unknown"


def test_ordering_tech_first_unknown_last():
    cats = ["Utilities", "Unknown", "Tech", "Financials"]
    assert sorted(cats, key=sort_key) == ["Tech", "Financials", "Utilities", "Unknown"]


def _snap(m60=True):
    return {"daily_close": 100.0, "monthly_above": {"10": True, "20": True, "60": m60}}


def test_scan_report_groups_by_category_with_emoji_headers():
    buys = [
        ("BLK", _snap(), ["reclaimed 60wk SMA"], []),
        ("NVDA", _snap(), ["reclaimed 10wk SMA"], ["pending Fri Jul 24 close"]),
    ]
    watching = [("ABBV", _snap(), ["reclaimed 10wk SMA"])]
    cats = {"NVDA": "Tech", "BLK": "Financials", "ABBV": "Health Care"}
    report = scan_report(buys, watching, cats)
    lines = report.split("\n")
    # Tech header before Financials inside the BUY section:
    assert lines.index("💻 Tech") < lines.index("🏦 Financials")
    assert "🏥 Health Care" in lines
    assert any("pending Fri Jul 24 close" in l for l in lines)


def test_scan_report_flat_when_no_categories_known():
    buys = [("AAA", _snap(), ["reclaimed 10wk SMA"], [])]
    report = scan_report(buys, [], {})
    assert "Unknown" not in report  # degraded mode: no pointless headers
    assert "AAA" in report
