import os
import time
import requests
import json
import random
from datetime import datetime
import yfinance as yf
import pandas as pd
import redis

COUCHDB_URL = os.getenv('COUCHDB_URL', "http://admin:password@couchdb:5984/")
DB_NAME = "stocks"
REDIS_HOST = os.getenv('REDIS_URL', 'localhost')

redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)

TICKERS = {
    "RELIANCE": "RELIANCE.NS", "TCS": "TCS.NS", "HDFC": "HDFCBANK.NS", "INFY": "INFY.NS",
    "WIPRO": "WIPRO.NS", "TATAMOTORS": "TATAMOTORS.NS", "SBIN": "SBIN.NS", "ITC": "ITC.NS",
    "BHARTIARTL": "BHARTIARTL.NS", "ZOMATO": "ZOMATO.NS", "AAPL": "AAPL", "GOOGL": "GOOGL",
    "MSFT": "MSFT", "AMZN": "AMZN", "TSLA": "TSLA", "META": "META", "NFLX": "NFLX",
    "NVDA": "NVDA", "BTC-USD": "BTC-USD", "ETH-USD": "ETH-USD", "DOGE-USD": "DOGE-USD",
    "BNB-USD": "BNB-USD", "SOL-USD": "SOL-USD"
}

def setup_db():
    while True:
        try:
            requests.get(COUCHDB_URL)
            break
        except:
            time.sleep(2)
    try:
        requests.put(COUCHDB_URL + DB_NAME)
        requests.put(COUCHDB_URL + "users")
        design_doc = {
            "_id": "_design/analytics",
            "views": {
                "portfolio_value": { "map": "function(doc) { if(doc.price) { emit('total_value', doc.price); } }", "reduce": "_sum" },
                "stocks_by_signal": { "map": "function(doc) { if(doc.signal) { emit(doc.signal, 1); } }", "reduce": "_count" },
                "stocks_by_exchange": { "map": "function(doc) { if(doc.exchange) { emit(doc.exchange, 1); } }", "reduce": "_count" }
            }
        }
        try:
            existing = requests.get(COUCHDB_URL + DB_NAME + "/_design/analytics")
            if existing.status_code == 200:
                design_doc['_rev'] = existing.json()['_rev']
        except:
            pass
        requests.post(COUCHDB_URL + DB_NAME, json=design_doc)
        
        for name, symbol in TICKERS.items():
            if ".NS" in symbol:
                exchange = "NSE (India)"
            elif "-USD" in symbol:
                exchange = "Crypto"
            else:
                exchange = "NASDAQ (US)"
            doc = { "_id": name, "ticker": name, "symbol": symbol, "exchange": exchange, "price": 1000.0, "signal": "HOLD" }
            try:
                existing_doc = requests.get(COUCHDB_URL + DB_NAME + "/" + name)
                if existing_doc.status_code == 200:
                    doc['_rev'] = existing_doc.json()['_rev']
            except:
                pass
            requests.put(COUCHDB_URL + DB_NAME + "/" + name, json=doc)
    except:
        pass

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gain = sum(d for d in deltas[-period:] if d > 0) / period
    loss = abs(sum(d for d in deltas[-period:] if d < 0)) / period
    if loss == 0:
        return 100.0
    rs = gain / loss
    return round(100 - (100 / (1 + rs)), 2)

def ingestion_loop():
    while True:
        try:
            resp = requests.get(COUCHDB_URL + DB_NAME + "/_all_docs?include_docs=true")
            docs = [row['doc'] for row in resp.json().get('rows', []) if not row['id'].startswith('_design') and row['id'] != 'users']
            updated_docs = []
            for doc in docs:
                symbol = doc.get('symbol', doc.get('ticker'))
                old_price = doc.get('price', 1000.0)
                try:
                    stock = yf.Ticker(symbol)
                    hist = stock.history(period="1mo", interval="1d")
                    if not hist.empty:
                        current_price = round(hist['Close'].iloc[-1], 2)
                        prices = hist['Close'].tolist()
                        rsi = calculate_rsi(prices)
                    else:
                        raise Exception("Empty")
                except:
                    jitter = random.uniform(-50.0, 50.0) if 'USD' in symbol else random.uniform(-5.0, 5.0)
                    current_price = round(max(old_price + jitter, 0.1), 2)
                    rsi = round(random.uniform(20, 80), 2)
                
                change = round(current_price - old_price, 2)
                change_percent = round((change / old_price) * 100, 2) if old_price != 0 else 0
                
                if rsi < 30 or change_percent > 2:
                    signal = "BUY"
                elif rsi > 70 or change_percent < -2:
                    signal = "SELL"
                else:
                    signal = "HOLD"
                    
                doc['price'] = current_price
                doc['change'] = change
                doc['change_percent'] = change_percent
                doc['signal'] = signal
                doc['rsi'] = rsi
                doc['last_updated'] = datetime.now().isoformat()
                updated_docs.append(doc)
                
            if updated_docs:
                requests.post(COUCHDB_URL + DB_NAME + "/_bulk_docs", json={"docs": updated_docs})
                redis_client.publish('live_prices', json.dumps(updated_docs))
        except:
            pass
        time.sleep(60)

if __name__ == '__main__':
    setup_db()
    ingestion_loop()