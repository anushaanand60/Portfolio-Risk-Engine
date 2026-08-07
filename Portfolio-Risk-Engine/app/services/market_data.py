import json
import yfinance as yf
from datetime import datetime, timedelta
import pandas as pd
from app.core.redis import redis_get, redis_set

def get_historical_prices(tickers: list[str], start_date: datetime, end_date: datetime) -> dict:
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = (end_date + timedelta(days=1)).strftime('%Y-%m-%d')
    
    tickers_key = "_".join(sorted(tickers))
    cache_key = f"market_data:{tickers_key}:{start_str}:{end_str}"
    
    try:
        cached_data = redis_get(cache_key)
    except Exception:
        cached_data = None
    if cached_data:
        try:
            parsed = json.loads(cached_data)
            for t, records in parsed.items():
                for r in records:
                    r["date"] = datetime.fromisoformat(r["date"])
            return parsed
        except Exception:
            pass
    
    df = yf.download(tickers, start=start_str, end=end_str, progress=False)
    
    prices = {t: [] for t in tickers}
    if df.empty:
        return prices
        
    if len(tickers) == 1:
        ticker = tickers[0]
        if "Close" in df:
            series = df["Close"].dropna()
            prices[ticker] = [{"date": idx.to_pydatetime(), "price": float(val)} for idx, val in series.items()]
    else:
        for ticker in tickers:
            if "Close" in df and ticker in df["Close"]:
                series = df["Close"][ticker].dropna()
                prices[ticker] = [{"date": idx.to_pydatetime(), "price": float(val)} for idx, val in series.items()]
                
    try:
        cache_data = {}
        for t, records in prices.items():
            cache_data[t] = [{"date": r["date"].isoformat(), "price": r["price"]} for r in records]
        redis_set(cache_key, json.dumps(cache_data), ttl=86400)
    except Exception:
        pass
                
    return prices
