"""
Paper Trading Simulator — Phase 1 seed universe (free tier)
=============================================================
NIFTY 50 constituents + key indices with their Yahoo Finance tickers.

This is a SEED, not the master. The instrument master (Angel One public
ScripMaster, no key required) is the authority and corrects this list on
every sync — if the index drops/renames a constituent, the sync end-dates
the old row and creates the new one instead of silently keeping a stale
seed. Treat index changes as data, never as code changes.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class UniverseEntry:
    """One seed row: our symbol plus its Yahoo ticker."""

    symbol: str
    yahoo_ticker: str
    name: str
    is_index: bool = False


# NIFTY 50 seed (reviewed Jan 2026; index changes are applied via master sync).
# Yahoo convention for NSE equities: "<SYMBOL>.NS".
_NIFTY_50_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("ADANIENT", "Adani Enterprises Limited"),
    ("ADANIPORTS", "Adani Ports and Special Economic Zone Limited"),
    ("APOLLOHOSP", "Apollo Hospitals Enterprise Limited"),
    ("ASIANPAINT", "Asian Paints Limited"),
    ("AXISBANK", "Axis Bank Limited"),
    ("BAJAJ-AUTO", "Bajaj Auto Limited"),
    ("BAJFINANCE", "Bajaj Finance Limited"),
    ("BAJFINSV", "Bajaj Finserv Limited"),
    ("BEL", "Bharat Electronics Limited"),
    ("BHARTIARTL", "Bharti Airtel Limited"),
    ("BPCL", "Bharat Petroleum Corporation Limited"),
    ("BRITANNIA", "Britannia Industries Limited"),
    ("CIPLA", "Cipla Limited"),
    ("COALINDIA", "Coal India Limited"),
    ("DRREDDY", "Dr. Reddy's Laboratories Limited"),
    ("EICHERMOT", "Eicher Motors Limited"),
    ("GRASIM", "Grasim Industries Limited"),
    ("HCLTECH", "HCL Technologies Limited"),
    ("HDFCBANK", "HDFC Bank Limited"),
    ("HDFCLIFE", "HDFC Life Insurance Company Limited"),
    ("HEROMOTOCO", "Hero MotoCorp Limited"),
    ("HINDALCO", "Hindalco Industries Limited"),
    ("HINDUNILVR", "Hindustan Unilever Limited"),
    ("ICICIBANK", "ICICI Bank Limited"),
    ("INDUSINDBK", "IndusInd Bank Limited"),
    ("INFY", "Infosys Limited"),
    ("ITC", "ITC Limited"),
    ("JIOFIN", "Jio Financial Services Limited"),
    ("JSWSTEEL", "JSW Steel Limited"),
    ("KOTAKBANK", "Kotak Mahindra Bank Limited"),
    ("LT", "Larsen & Toubro Limited"),
    ("M&M", "Mahindra & Mahindra Limited"),
    ("MARUTI", "Maruti Suzuki India Limited"),
    ("NESTLEIND", "Nestle India Limited"),
    ("NTPC", "NTPC Limited"),
    ("ONGC", "Oil & Natural Gas Corporation Limited"),
    ("POWERGRID", "Power Grid Corporation of India Limited"),
    ("RELIANCE", "Reliance Industries Limited"),
    ("SBIN", "State Bank of India"),
    ("SBILIFE", "SBI Life Insurance Company Limited"),
    ("SHRIRAMFIN", "Shriram Finance Limited"),
    ("SUNPHARMA", "Sun Pharmaceutical Industries Limited"),
    ("TATACONSUM", "Tata Consumer Products Limited"),
    ("TATAMOTORS", "Tata Motors Limited"),
    ("TATASTEEL", "Tata Steel Limited"),
    ("TCS", "Tata Consultancy Services Limited"),
    ("TECHM", "Tech Mahindra Limited"),
    ("TITAN", "Titan Company Limited"),
    ("TRENT", "Trent Limited"),
    ("ULTRACEMCO", "UltraTech Cement Limited"),
)

_INDICES: tuple[tuple[str, str, str], ...] = (
    ("NIFTY", "^NSEI", "Nifty 50 Index"),
    ("BANKNIFTY", "^NSEBANK", "Nifty Bank Index"),
    ("INDIAVIX", "^INDIAVIX", "India VIX Index"),
)


def get_seed_universe() -> list[UniverseEntry]:
    """Full Phase 1 seed universe: NIFTY 50 equities + indices."""
    entries = [
        UniverseEntry(symbol=sym, yahoo_ticker=f"{sym}.NS", name=name)
        for sym, name in _NIFTY_50_SYMBOLS
    ]
    entries.extend(
        UniverseEntry(symbol=sym, yahoo_ticker=ticker, name=name, is_index=True)
        for sym, ticker, name in _INDICES
    )
    return entries


def get_seed_symbols() -> list[str]:
    """Just the NSE symbols (for watchlist subscription)."""
    return [e.symbol for e in get_seed_universe()]


def yahoo_ticker_for(symbol: str) -> str:
    """Map an NSE symbol to its Yahoo ticker. Raises KeyError when unknown."""
    for entry in get_seed_universe():
        if entry.symbol == symbol:
            return entry.yahoo_ticker
    raise KeyError(f"No Yahoo ticker mapped for symbol {symbol!r}. Add it to universe.py.")
