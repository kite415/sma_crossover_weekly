"""Category logic: bucket tickers by sector with Tech prioritized.

"Tech" is deliberately broader than GICS Information Technology (user
preference): it also absorbs Communication Services (GOOGL, META) and a
small hand-picked extras list (AMZN, TSLA live in Consumer Discretionary
officially, but read as tech here). Edit TECH_EXTRAS to taste.
"""

TECH_SECTORS = {
    "Information Technology",   # GICS (Wikipedia)
    "Communication Services",   # GICS + Yahoo share this label
    "Technology",               # Yahoo's label for IT
}
TECH_EXTRAS = {"AMZN", "TSLA"}

# Yahoo (used for watchlist tickers outside the indices) names sectors
# differently than GICS -- normalize to the GICS-ish names we display.
YAHOO_TO_GICS = {
    "Financial Services": "Financials",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Healthcare": "Health Care",
    "Basic Materials": "Materials",
}

EMOJI = {
    "Tech": "💻",
    "Communication Services": "📡",  # only reachable if pulled out of Tech
    "Consumer Discretionary": "🛍️",
    "Consumer Staples": "🛒",
    "Energy": "⚡",
    "Financials": "🏦",
    "Health Care": "🏥",
    "Industrials": "🏭",
    "Materials": "🧪",
    "Real Estate": "🏠",
    "Utilities": "🔌",
    "Unknown": "❓",
}


def category(ticker, sector):
    """Bucket name for a ticker given its (GICS or Yahoo) sector string."""
    if ticker in TECH_EXTRAS:
        return "Tech"
    if not sector:
        return "Unknown"
    sector = YAHOO_TO_GICS.get(sector, sector)
    return "Tech" if sector in TECH_SECTORS else sector


def emoji(cat):
    return EMOJI.get(cat, "🏷️")


def sort_key(cat):
    """Tech first, named sectors alphabetically, Unknown last."""
    if cat == "Tech":
        return (0, "")
    if cat == "Unknown":
        return (2, "")
    return (1, cat)
