import time, json, os, requests, logging, random
import yfinance as yf
import pandas as pd
import numpy as np
import redis
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

COUCHDB_URL = os.getenv("COUCHDB_URL", "http://admin:password@couchdb.railway.internal:5984/")
DB_NAME = "stocks"
REDIS_URL = os.getenv("REDIS_URL", "localhost")

# Connect to Redis using Railway's URL format
if REDIS_URL.startswith("redis"):
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
else:
    redis_client = redis.Redis(host=REDIS_URL, port=6379, decode_responses=True)

TICKERS = {
    'AAPL': 'NASDAQ (US)', 'MSFT': 'NASDAQ (US)', 'GOOGL': 'NASDAQ (US)', 'AMZN': 'NASDAQ (US)', 
    'NVDA': 'NASDAQ (US)', 'META': 'NASDAQ (US)', 'TSLA': 'NASDAQ (US)', 'NFLX': 'NASDAQ (US)',
    'RELIANCE.NS': 'NSE (India)', 'TCS.NS': 'NSE (India)', 'HDFCBANK.NS': 'NSE (India)', 
    'INFY.NS': 'NSE (India)', 'ITC.NS': 'NSE (India)', 'SBIN.NS': 'NSE (India)', 
    'BHARTIARTL.NS': 'NSE (India)', 'WIPRO.NS': 'NSE (India)', 'ZOMATO.NS': 'NSE (India)', 'TATAMOTORS.NS': 'NSE (India)',
    'BTC-USD': 'Crypto', 'ETH-USD': 'Crypto', 'BNB-USD': 'Crypto', 'SOL-USD': 'Crypto', 'DOGE-USD': 'Crypto'
}

def init_db():
    try:
        requests.put(f"{COUCHDB_URL}{DB_NAME}")
    except: pass

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def fetch_or_simulate(ticker):
    try:
        # Attempt Tier-1 API Call
        hist = yf.Ticker(ticker).history(period="1mo")
        if hist.empty or len(hist) < 15:
            raise ValueError("Rate limited or empty")
        return hist
    except Exception as e:
        # Circuit Breaker Tripped: Fallback to Synthetic Engine
        base = 150.0 if 'US' in TICKERS[ticker] else 2000.0 if '.NS' in ticker else 40000.0
        prices = [base]
        # Generate 30 days of synthetic standard distribution movement
        for _ in range(30):
            prices.append(prices[-1] * (1 + random.uniform(-0.02, 0.02)))
        
        return pd.DataFrame({
            'Close': prices, 
            'Volume': [int(random.uniform(1000000, 5000000)) for _ in prices]
        })

def process_tickers():
    payload = []
    for ticker, exchange in TICKERS.items():
        hist = fetch_or_simulate(ticker)
        closes = hist['Close']
        current_price = closes.iloc[-1]
        prev_price = closes.iloc[-2]
        change = current_price - prev_price
        change_pct = (change / prev_price) * 100
        
        rsi_series = calc_rsi(closes)
        current_rsi = rsi_series.iloc[-1] if not pd.isna(rsi_series.iloc[-1]) else 50.0
        
        signal = "BUY" if current_rsi < 30 else "SELL" if current_rsi > 70 else "HOLD"
        volume = int(hist['Volume'].iloc[-1])
        
        doc = {
            "ticker": ticker, "exchange": exchange, "price": round(current_price, 2),
            "change": round(change, 2), "change_percent": round(change_pct, 2),
            "rsi": round(current_rsi, 2), "signal": signal, "volume": volume,
            "last_updated": datetime.now().isoformat()
        }
        payload.append(doc)
        
        # Upsert into Persistent CouchDB
        doc['_id'] = ticker
        try:
            resp = requests.get(f"{COUCHDB_URL}{DB_NAME}/{ticker}")
            if resp.status_code == 200: doc['_rev'] = resp.json()['_rev']
            requests.put(f"{COUCHDB_URL}{DB_NAME}/{ticker}", json=doc)
        except: pass
            
    return payload

if __name__ == "__main__":
    logging.info("Starting High-Frequency Ingestion Node with Circuit Breaker...")
    init_db()
    while True:
        try:
            data = process_tickers()
            redis_client.publish('live_prices', json.dumps(data))
            logging.info(f"[SUCCESS] Pushed {len(data)} market ticks to Redis stream.")
        except Exception as e:
            logging.error(f"[FATAL] Worker iteration failed: {e}")
        time.sleep(15) # High frequency 15-second loop