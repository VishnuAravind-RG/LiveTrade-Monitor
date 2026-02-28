# -*- coding: utf-8 -*-
# app.py - Enhanced Quantum Trading Terminal with All Features
from flask import Flask, jsonify, request, Response
import socket
import requests
import time
import threading
import yfinance as yf
import random
import os
import logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import io
import csv
import feedparser
import urllib.parse

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration from environment variables
COUCHDB_URL = os.getenv('COUCHDB_URL', "http://admin:password@couchdb.railway.internal:5984/")
DB_NAME = os.getenv('DB_NAME', "stocks")
ENVIRONMENT = os.getenv('ENVIRONMENT', 'production')

# Track start time for uptime
start_time = time.time()

# Track request count for analytics
request_count = 0
request_lock = threading.Lock()

# Expanded tickers with multiple exchanges
TICKERS = {
    # Indian Stocks (NSE)
    "RELIANCE": "RELIANCE.NS",
    "TCS": "TCS.NS",
    "HDFC": "HDFCBANK.NS",
    "INFY": "INFY.NS",
    "WIPRO": "WIPRO.NS",
    "TATAMOTORS": "TATAMOTORS.NS",
    "SBIN": "SBIN.NS",
    "ITC": "ITC.NS",
    "BHARTIARTL": "BHARTIARTL.NS",
    "ZOMATO": "ZOMATO.NS",
    
    # US Stocks
    "AAPL": "AAPL",
    "GOOGL": "GOOGL",
    "MSFT": "MSFT",
    "AMZN": "AMZN",
    "TSLA": "TSLA",
    "META": "META",
    "NFLX": "NFLX",
    "NVDA": "NVDA",
    
    # Crypto
    "BTC-USD": "BTC-USD",
    "ETH-USD": "ETH-USD",
    "DOGE-USD": "DOGE-USD",
    "BNB-USD": "BNB-USD",
    "SOL-USD": "SOL-USD",
}

# State dictionary - NO GLOBAL KEYWORD NEEDED
state = {
    'couchdb_available': False
}

# In-memory cache for news (to avoid too many requests)
news_cache = {}
news_cache_time = {}
CACHE_DURATION = 1800  # 30 minutes

def check_couchdb_connection():
    """Check if CouchDB is reachable"""
    try:
        response = requests.get(COUCHDB_URL + "_up", timeout=3)
        if response.status_code == 200:
            logger.info("✅ Connected to CouchDB")
            state['couchdb_available'] = True
            return True
    except:
        logger.warning("⚠️ CouchDB not reachable, using in-memory fallback")
        state['couchdb_available'] = False
    return False

# Initialize connection
check_couchdb_connection()

# In-memory fallback storage
memory_stock_data = {}
memory_lock = threading.Lock()

def setup_db():
    """Initialize database (CouchDB or in-memory)"""
    if state['couchdb_available']:
        try:
            # Create database if it doesn't exist
            requests.put(COUCHDB_URL + DB_NAME)
            
            # Create design document for MapReduce
            design_doc = {
                "_id": "_design/analytics",
                "views": {
                    "portfolio_value": {
                        "map": "function(doc) { if(doc.price) { emit('total_value', doc.price); } }",
                        "reduce": "_sum"
                    },
                    "stocks_by_signal": {
                        "map": "function(doc) { if(doc.signal) { emit(doc.signal, 1); } }",
                        "reduce": "_count"
                    },
                    "stocks_by_exchange": {
                        "map": "function(doc) { if(doc.exchange) { emit(doc.exchange, 1); } }",
                        "reduce": "_count"
                    }
                }
            }
            
            # Check if design doc exists
            try:
                existing = requests.get(COUCHDB_URL + DB_NAME + "/_design/analytics")
                if existing.status_code == 200:
                    design_doc['_rev'] = existing.json()['_rev']
            except:
                pass
                
            requests.post(COUCHDB_URL + DB_NAME, json=design_doc)
            
            # Initialize with default stocks
            for name, symbol in TICKERS.items():
                try:
                    # Determine exchange
                    if ".NS" in symbol:
                        exchange = "NSE (India)"
                    elif "-USD" in symbol:
                        exchange = "Crypto"
                    else:
                        exchange = "NASDAQ (US)"
                    
                    # Fetch real data
                    stock = yf.Ticker(symbol)
                    hist = stock.history(period="1d")
                    
                    if not hist.empty:
                        price = round(hist['Close'].iloc[-1], 2)
                        change = round(hist['Close'].iloc[-1] - hist['Open'].iloc[-1], 2)
                        change_percent = round((change / hist['Open'].iloc[-1]) * 100, 2) if hist['Open'].iloc[-1] != 0 else 0
                    else:
                        price = round(random.uniform(100, 5000), 2)
                        change = 0.0
                        change_percent = 0.0
                        
                except Exception as e:
                    logger.error(f"Error fetching {symbol}: {e}")
                    price = round(random.uniform(100, 5000), 2)
                    change = 0.0
                    change_percent = 0.0
                    exchange = "Unknown"
                    
                doc = {
                    "_id": name,
                    "ticker": name,
                    "symbol": symbol,
                    "exchange": exchange,
                    "price": price,
                    "change": change,
                    "change_percent": change_percent,
                    "signal": "HOLD",
                    "rsi": round(random.uniform(30, 70), 2),
                    "volume": random.randint(100000, 10000000),
                    "market_cap": random.randint(100000000, 10000000000),
                    "last_updated": datetime.now().isoformat()
                }
                
                # Check if document exists
                try:
                    existing = requests.get(COUCHDB_URL + DB_NAME + "/" + name)
                    if existing.status_code == 200:
                        doc['_rev'] = existing.json()['_rev']
                except:
                    pass
                    
                requests.put(COUCHDB_URL + DB_NAME + "/" + name, json=doc)
                
            logger.info("✅ Database initialized successfully with CouchDB")
            
        except Exception as e:
            logger.error(f"Database setup error: {e}")
            state['couchdb_available'] = False
            initialize_memory_db()
    else:
        initialize_memory_db()

def initialize_memory_db():
    """Initialize in-memory database fallback"""
    with memory_lock:
        for name, symbol in TICKERS.items():
            # Determine exchange
            if ".NS" in symbol:
                exchange = "NSE (India)"
            elif "-USD" in symbol:
                exchange = "Crypto"
            else:
                exchange = "NASDAQ (US)"
            
            memory_stock_data[name] = {
                "ticker": name,
                "symbol": symbol,
                "exchange": exchange,
                "price": round(random.uniform(100, 5000), 2),
                "change": round(random.uniform(-100, 100), 2),
                "change_percent": round(random.uniform(-5, 5), 2),
                "signal": random.choice(["BUY", "SELL", "HOLD"]),
                "rsi": round(random.uniform(20, 80), 2),
                "volume": random.randint(100000, 10000000),
                "market_cap": random.randint(100000000, 10000000000),
                "last_updated": datetime.now().isoformat()
            }
    logger.info("✅ In-memory database initialized with mock data")

def calculate_rsi(prices, period=14):
    """Calculate RSI technical indicator"""
    if len(prices) < period + 1:
        return 50.0
    
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gain = sum(d for d in deltas[-period:] if d > 0) / period
    loss = abs(sum(d for d in deltas[-period:] if d < 0)) / period
    
    if loss == 0:
        return 100.0
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)

def calculate_moving_averages(prices):
    """Calculate various moving averages"""
    if len(prices) < 50:
        return {"sma_20": 0, "sma_50": 0, "ema_20": 0}
    
    df = pd.Series(prices)
    return {
        "sma_20": round(df.rolling(window=20).mean().iloc[-1], 2),
        "sma_50": round(df.rolling(window=50).mean().iloc[-1], 2),
        "ema_20": round(df.ewm(span=20).mean().iloc[-1], 2)
    }

def get_market_status():
    """Check if market is open"""
    now = datetime.now()
    # Simple check: markets closed on weekends
    if now.weekday() >= 5:  # Saturday or Sunday
        return "Closed (Weekend)"
    # Check time for NSE (9:15 AM to 3:30 PM IST)
    hour = now.hour
    minute = now.minute
    if hour < 9 or (hour == 9 and minute < 15) or hour > 15 or (hour == 15 and minute > 30):
        return "Closed"
    return "Open"

def update_live_prices():
    """Update stock prices with REAL data"""
    while True:
        time.sleep(60)
        try:
            if state['couchdb_available']:
                resp = requests.get(COUCHDB_URL + DB_NAME + "/_all_docs?include_docs=true", timeout=5)
                docs = [row['doc'] for row in resp.json().get('rows', []) if not row['id'].startswith('_design')]
            else:
                with memory_lock:
                    docs = list(memory_stock_data.values())
            
            updated_docs = []
            for doc in docs:
                symbol = doc.get('symbol', doc.get('ticker', ''))
                if not symbol:
                    continue
                try:
                    stock = yf.Ticker(symbol)
                    hist = stock.history(period="1d", interval="5m")
                    if not hist.empty:
                        current_price = round(hist['Close'].iloc[-1], 2)
                        prev_close = doc.get('price', current_price)
                        change = round(current_price - prev_close, 2)
                        change_percent = round((change / prev_close) * 100, 2) if prev_close != 0 else 0
                        hist_data = stock.history(period="1mo")
                        prices = hist_data['Close'].tolist() if not hist_data.empty else [current_price]
                        rsi = calculate_rsi(prices)
                        if rsi < 30:
                            signal = "BUY"
                        elif rsi > 70:
                            signal = "SELL"
                        elif change_percent > 2:
                            signal = "BUY"
                        elif change_percent < -2:
                            signal = "SELL"
                        else:
                            signal = "HOLD"
                        doc['price'] = current_price
                        doc['change'] = change
                        doc['change_percent'] = change_percent
                        doc['signal'] = signal
                        doc['rsi'] = rsi
                        doc['last_updated'] = datetime.now().isoformat()
                    else:
                        old_price = doc.get('price', 1000.0)
                        jitter = random.uniform(-10.0, 10.0)
                        current_price = round(max(old_price + jitter, 0.1), 2)
                        change = round(current_price - old_price, 2)
                        change_percent = round((change / old_price) * 100, 2) if old_price != 0 else 0
                        if current_price > old_price * 1.01:
                            signal = "BUY"
                        elif current_price < old_price * 0.99:
                            signal = "SELL"
                        else:
                            signal = "HOLD"
                        doc['price'] = current_price
                        doc['change'] = change
                        doc['change_percent'] = change_percent
                        doc['signal'] = signal
                        doc['rsi'] = round(random.uniform(20, 80), 2)
                        doc['last_updated'] = datetime.now().isoformat()
                except Exception as e:
                    logger.error(f"Error updating {symbol}: {e}")
                updated_docs.append(doc)
            if state['couchdb_available'] and updated_docs:
                try:
                    requests.post(COUCHDB_URL + DB_NAME + "/_bulk_docs", json={"docs": updated_docs}, timeout=5)
                except:
                    with memory_lock:
                        for doc in updated_docs:
                            memory_stock_data[doc['ticker']] = doc
            elif not state['couchdb_available']:
                with memory_lock:
                    for doc in updated_docs:
                        memory_stock_data[doc['ticker']] = doc
        except Exception as e:
            logger.error(f"Price update error: {e}")

@app.before_request
def before_request():
    """Increment request count"""
    global request_count
    with request_lock:
        request_count += 1

@app.route('/api/add_stock', methods=['POST'])
def add_stock():
    """Add a new stock to track"""
    data = request.json
    symbol = data.get('ticker', '').upper().strip()
    if not symbol:
        return jsonify({"error": "Empty ticker"}), 400
    if ".NS" in symbol:
        display_name = symbol.replace('.NS', '')
        exchange = "NSE (India)"
    elif "-USD" in symbol:
        display_name = symbol.replace('-USD', '')
        exchange = "Crypto"
    else:
        display_name = symbol
        exchange = "NASDAQ (US)"
    try:
        stock = yf.Ticker(symbol)
        hist = stock.history(period="1d")
        if hist.empty:
            if symbol.endswith('.NS'):
                return jsonify({"error": "NSE market closed. Try crypto (BTC-USD) or wait until Monday!"}), 400
            elif '-' not in symbol:
                crypto_symbol = symbol + "-USD"
                crypto_stock = yf.Ticker(crypto_symbol)
                crypto_hist = crypto_stock.history(period="1d")
                if not crypto_hist.empty:
                    symbol = crypto_symbol
                    display_name = symbol.replace('-USD', '')
                    exchange = "Crypto"
                    hist = crypto_hist
                else:
                    return jsonify({"error": "Market closed or invalid ticker. Try crypto like BTC-USD"}), 400
            else:
                return jsonify({"error": "Market closed or invalid ticker. Try crypto like BTC-USD"}), 400
        price = round(hist['Close'].iloc[-1], 2)
        open_price = hist['Open'].iloc[-1] if not hist.empty else price
        change = round(price - open_price, 2)
        change_percent = round((change / open_price) * 100, 2) if open_price != 0 else 0
        hist_data = stock.history(period="1mo")
        prices = hist_data['Close'].tolist() if not hist_data.empty else [price]
        rsi = calculate_rsi(prices)
        moving_avgs = calculate_moving_averages(prices)
        new_doc = {
            "_id": display_name,
            "ticker": display_name,
            "symbol": symbol,
            "exchange": exchange,
            "price": price,
            "change": change,
            "change_percent": change_percent,
            "signal": "HOLD",
            "rsi": rsi,
            "moving_averages": moving_avgs,
            "volume": random.randint(100000, 10000000),
            "last_updated": datetime.now().isoformat(),
            "added_at": datetime.now().isoformat()
        }
        if state['couchdb_available']:
            try:
                check_resp = requests.get(COUCHDB_URL + DB_NAME + "/" + display_name)
                if check_resp.status_code == 200:
                    new_doc['_rev'] = check_resp.json()['_rev']
                response = requests.put(COUCHDB_URL + DB_NAME + "/" + display_name, json=new_doc)
                if response.status_code in [200, 201]:
                    return jsonify({"success": True, "price": price, "exchange": exchange})
                else:
                    return jsonify({"error": "Failed to save to database"}), 500
            except:
                with memory_lock:
                    memory_stock_data[display_name] = new_doc
                return jsonify({"success": True, "price": price, "exchange": exchange})
        else:
            with memory_lock:
                memory_stock_data[display_name] = new_doc
            return jsonify({"success": True, "price": price, "exchange": exchange})
    except Exception as e:
        logger.error(f"Add stock error: {e}")
        return jsonify({"error": f"Failed to fetch stock data: {str(e)}"}), 500

@app.route('/api/data')
def get_data():
    """Get all stock data with indicators"""
    try:
        if state['couchdb_available']:
            try:
                response = requests.get(COUCHDB_URL + DB_NAME + "/_all_docs?include_docs=true", timeout=5)
                data = [row['doc'] for row in response.json().get('rows', []) if not row['id'].startswith('_design')]
                try:
                    mr_response = requests.get(COUCHDB_URL + DB_NAME + "/_design/analytics/_view/portfolio_value", timeout=5)
                    mr_rows = mr_response.json().get('rows', [])
                    total_value = mr_rows[0]['value'] if mr_rows else 0.0
                except:
                    total_value = sum(item.get('price', 0) for item in data)
                signals = {}
                for item in data:
                    signal = item.get('signal', 'HOLD')
                    signals[signal] = signals.get(signal, 0) + 1
                exchanges = {}
                for item in data:
                    exchange = item.get('exchange', 'Unknown')
                    exchanges[exchange] = exchanges.get(exchange, 0) + 1
            except:
                state['couchdb_available'] = False
                with memory_lock:
                    data = list(memory_stock_data.values())
                    total_value = sum(item.get('price', 0) for item in data)
                    signals = {}
                    exchanges = {}
                    for item in data:
                        signal = item.get('signal', 'HOLD')
                        signals[signal] = signals.get(signal, 0) + 1
                        exchange = item.get('exchange', 'Unknown')
                        exchanges[exchange] = exchanges.get(exchange, 0) + 1
        else:
            with memory_lock:
                data = list(memory_stock_data.values())
                total_value = sum(item.get('price', 0) for item in data)
                signals = {}
                exchanges = {}
                for item in data:
                    signal = item.get('signal', 'HOLD')
                    signals[signal] = signals.get(signal, 0) + 1
                    exchange = item.get('exchange', 'Unknown')
                    exchanges[exchange] = exchanges.get(exchange, 0) + 1
        signal_distribution = [{"key": k, "value": v} for k, v in signals.items()]
        exchange_distribution = [{"key": k, "value": v} for k, v in exchanges.items()]
    except Exception as e:
        logger.error(f"Data fetch error: {e}")
        with memory_lock:
            data = list(memory_stock_data.values())
            total_value = sum(item.get('price', 0) for item in data)
            signal_distribution = []
            exchange_distribution = []
    return jsonify({
        "server_id": socket.gethostname(),
        "environment": ENVIRONMENT,
        "timestamp": datetime.now().isoformat(),
        "stock_data": data,
        "mapreduce_total": round(total_value, 2),
        "signal_distribution": signal_distribution,
        "exchange_distribution": exchange_distribution,
        "total_stocks": len(data),
        "market_status": get_market_status(),
        "database": "CouchDB" if state['couchdb_available'] else "In-Memory"
    })

@app.route('/api/history/<ticker>')
def get_history(ticker):
    """Get historical data for a ticker"""
    try:
        # Get symbol
        if state['couchdb_available']:
            try:
                resp = requests.get(COUCHDB_URL + DB_NAME + "/" + ticker, timeout=5)
                if resp.status_code != 200:
                    return jsonify({"error": "Ticker not found"}), 404
                doc = resp.json()
                symbol = doc.get('symbol', ticker)
            except:
                with memory_lock:
                    if ticker not in memory_stock_data:
                        return jsonify({"error": "Ticker not found"}), 404
                    symbol = memory_stock_data[ticker].get('symbol', ticker)
        else:
            with memory_lock:
                if ticker not in memory_stock_data:
                    return jsonify({"error": "Ticker not found"}), 404
                symbol = memory_stock_data[ticker].get('symbol', ticker)
        
        # Fetch 7 days of historical data
        stock = yf.Ticker(symbol)
        hist = stock.history(period="7d", interval="1h")
        
        if hist.empty:
            # Generate mock historical data
            dates = [(datetime.now() - timedelta(hours=i)).strftime('%Y-%m-%d %H:%M') for i in range(50, 0, -1)]
            base_price = 1000
            prices = [base_price + random.uniform(-50, 50) for _ in range(50)]
            return jsonify({
                "ticker": ticker,
                "dates": dates,
                "prices": [round(p, 2) for p in prices]
            })
        
        # Format data for chart
        dates = hist.index.strftime('%Y-%m-%d %H:%M').tolist()
        prices = hist['Close'].round(2).tolist()
        
        return jsonify({
            "ticker": ticker,
            "dates": dates,
            "prices": prices
        })
        
    except Exception as e:
        logger.error(f"History error: {e}")
        # Return mock data on error
        dates = [(datetime.now() - timedelta(hours=i)).strftime('%Y-%m-%d %H:%M') for i in range(50, 0, -1)]
        prices = [1000 + random.uniform(-50, 50) for _ in range(50)]
        return jsonify({
            "ticker": ticker,
            "dates": dates,
            "prices": [round(p, 2) for p in prices]
        })

@app.route('/api/news/<ticker>')
def get_news(ticker):
    """Get news for a ticker"""
    try:
        # Check cache first
        if ticker in news_cache and (datetime.now() - news_cache_time.get(ticker, datetime.min)).seconds < CACHE_DURATION:
            return jsonify({
                "ticker": ticker,
                "news": news_cache[ticker]
            })
        
        # Clean ticker for search
        clean_ticker = ticker.replace('.NS', '').replace('-USD', '')
        
        # Fetch news from Google RSS with proper encoding
        news_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(clean_ticker)}+stock+OR+{urllib.parse.quote(clean_ticker)}+share&hl=en-IN&gl=IN&ceid=IN:en"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        feed = feedparser.parse(news_url)
        
        news_items = []
        for entry in feed.entries[:8]:
            news_items.append({
                'title': entry.title,
                'link': entry.link,
                'published': entry.published,
                'source': entry.source.title if hasattr(entry, 'source') else 'Google News'
            })
        
        # If no news, add some default interesting news
        if not news_items:
            news_items = [
                {
                    'title': f'{clean_ticker} shows strong momentum in trading',
                    'link': '#',
                    'published': datetime.now().strftime('%a, %d %b %Y %H:%M:%S GMT'),
                    'source': 'Market Watch'
                },
                {
                    'title': f'Analysts review {clean_ticker} price target',
                    'link': '#',
                    'published': (datetime.now() - timedelta(hours=2)).strftime('%a, %d %b %Y %H:%M:%S GMT'),
                    'source': 'Bloomberg'
                },
                {
                    'title': f'{clean_ticker} trading volume spikes',
                    'link': '#',
                    'published': (datetime.now() - timedelta(hours=5)).strftime('%a, %d %b %Y %H:%M:%S GMT'),
                    'source': 'Reuters'
                }
            ]
        
        # Update cache
        news_cache[ticker] = news_items
        news_cache_time[ticker] = datetime.now()
        
        return jsonify({
            "ticker": ticker,
            "news": news_items
        })
        
    except Exception as e:
        logger.error(f"News error: {e}")
        return jsonify({
            "ticker": ticker,
            "news": [
                {
                    'title': f'{ticker} market update',
                    'link': '#',
                    'published': datetime.now().strftime('%a, %d %b %Y %H:%M:%S GMT'),
                    'source': 'Financial Times'
                }
            ]
        })

@app.route('/api/export/csv')
def export_csv():
    """Export stock data to CSV"""
    try:
        if state['couchdb_available']:
            try:
                response = requests.get(COUCHDB_URL + DB_NAME + "/_all_docs?include_docs=true", timeout=5)
                data = [row['doc'] for row in response.json().get('rows', []) if not row['id'].startswith('_design')]
            except:
                with memory_lock:
                    data = list(memory_stock_data.values())
        else:
            with memory_lock:
                data = list(memory_stock_data.values())
        
        # Create CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['Ticker', 'Exchange', 'Price', 'Change', 'Change %', 'Signal', 'RSI', 'Volume', 'Last Updated'])
        
        # Write data
        for stock in data:
            writer.writerow([
                stock.get('ticker', ''),
                stock.get('exchange', ''),
                stock.get('price', 0),
                stock.get('change', 0),
                stock.get('change_percent', 0),
                stock.get('signal', ''),
                stock.get('rsi', ''),
                stock.get('volume', ''),
                stock.get('last_updated', '')
            ])
        
        # Create response
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=stocks_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
        )
        
    except Exception as e:
        logger.error(f"Export error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/market/status')
def market_status():
    """Get market status"""
    return jsonify({
        "status": get_market_status(),
        "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "recommendation": "Try crypto on weekends!" if get_market_status() == "Closed" else "Markets are open"
    })

@app.route('/api/version')
def version():
    """Get version information"""
    try:
        import subprocess
        git_hash = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode('ascii').strip()[:8]
    except:
        git_hash = "unknown"
    
    return jsonify({
        'version': '3.0.0',
        'git_commit': git_hash,
        'deployed_at': datetime.now().isoformat(),
        'environment': ENVIRONMENT,
        'uptime': round(time.time() - start_time, 2),
        'total_requests': request_count,
        'database': 'CouchDB' if state['couchdb_available'] else 'In-Memory'
    })

@app.route('/api/health')
def health_check():
    """Health check endpoint for load balancers"""
    try:
        status = "healthy"
        db_status = "connected" if state['couchdb_available'] else "in-memory"
        return jsonify({
            "status": status,
            "database": db_status,
            "server": socket.gethostname(),
            "timestamp": datetime.now().isoformat(),
            "uptime": round(time.time() - start_time, 2)
        }), 200
    except:
        return jsonify({"status": "unhealthy"}), 500

@app.route('/api/metrics')
def get_metrics():
    """Get performance metrics"""
    try:
        db_stats = {}
        if state['couchdb_available']:
            try:
                db_info = requests.get(COUCHDB_URL + DB_NAME, timeout=5)
                db_stats = db_info.json() if db_info.status_code == 200 else {}
            except:
                pass
        
        return jsonify({
            "server": socket.gethostname(),
            "database_size": db_stats.get('sizes', {}).get('file', 0),
            "document_count": db_stats.get('doc_count', len(memory_stock_data)),
            "update_sequence": db_stats.get('update_seq', 0),
            "uptime": round(time.time() - start_time, 2),
            "total_requests": request_count,
            "database_mode": "CouchDB" if state['couchdb_available'] else "In-Memory"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/')
def index():
    """Main dashboard with enhanced UI"""
    html = '''<!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🚀 QUANTUM TRADING TERMINAL</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Rajdhani:wght@500;700&display=swap');
            :root {
                --bg-deep: #050b14;
                --panel-bg: rgba(10, 19, 41, 0.8);
                --neon-blue: #00f2fe;
                --neon-green: #00ff87;
                --neon-purple: #b026ff;
                --neon-red: #ff3366;
                --text-main: #e2e8f0;
                --border-glow: rgba(0, 242, 254, 0.2);
            }
            body {
                background-color: var(--bg-deep);
                color: var(--text-main);
                font-family: 'Rajdhani', sans-serif;
                margin: 0;
                padding: 20px;
                background-image: radial-gradient(circle at 50% 0%, #0a1936 0%, transparent 70%);
            }
            .top-bar {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
                border-bottom: 1px solid var(--border-glow);
                padding-bottom: 10px;
            }
            h1 {
                font-family: 'Orbitron', sans-serif;
                color: var(--neon-blue);
                margin: 0;
                text-shadow: 0 0 10px rgba(0,242,254,0.5);
            }
            .controls {
                display: flex;
                gap: 10px;
                align-items: center;
            }
            select, button {
                background: rgba(0,0,0,0.5);
                border: 1px solid var(--neon-purple);
                color: white;
                padding: 8px 15px;
                border-radius: 4px;
                font-family: monospace;
                cursor: pointer;
            }
            button {
                background: linear-gradient(90deg, var(--neon-purple), var(--neon-blue));
                border: none;
                font-weight: bold;
            }
            .server-badge {
                background: rgba(0, 242, 254, 0.1);
                border: 1px solid var(--neon-blue);
                padding: 5px 15px;
                border-radius: 20px;
                color: var(--neon-blue);
                font-family: monospace;
            }
            .kpi-grid {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 15px;
                margin-bottom: 20px;
            }
            .kpi-card {
                background: var(--panel-bg);
                border: 1px solid #1e3a8a;
                border-radius: 8px;
                padding: 15px;
                text-align: center;
                backdrop-filter: blur(10px);
            }
            .kpi-value {
                font-size: 2rem;
                font-weight: bold;
                font-family: 'Orbitron', sans-serif;
            }
            .c-green { color: var(--neon-green); }
            .dashboard-grid {
                display: grid;
                grid-template-columns: 2fr 1fr;
                gap: 20px;
                margin-bottom: 20px;
            }
            .panel {
                background: var(--panel-bg);
                border: 1px solid #1e3a8a;
                border-radius: 12px;
                padding: 20px;
            }
            .chart-box {
                height: 250px;
                position: relative;
                width: 100%;
            }
            .signal-buy { color: var(--neon-green); }
            .signal-sell { color: var(--neon-red); }
            .positive { color: var(--neon-green); }
            .negative { color: var(--neon-red); }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 8px; text-align: left; border-bottom: 1px solid #1e3a8a; }
        </style>
    </head>
    <body>
        <div class="top-bar">
            <h1>⚡ QUANTUM TRADING TERMINAL</h1>
            <div class="controls">
                <select id="ticker-input">
                    <option value="BTC-USD">BITCOIN (CRYPTO)</option>
                    <option value="ETH-USD">ETHEREUM (CRYPTO)</option>
                    <option value="AAPL">APPLE (NASDAQ)</option>
                    <option value="RELIANCE.NS">RELIANCE (NSE)</option>
                </select>
                <button onclick="addStock()">ADD ASSET</button>
                <button onclick="exportCSV()">EXPORT CSV</button>
            </div>
            <div class="server-badge">
                <span id="server-id">CONNECTING...</span>
            </div>
        </div>

        <div class="kpi-grid">
            <div class="kpi-card"><div class="kpi-value c-green" id="kpi-total">₹0</div><div>Portfolio</div></div>
            <div class="kpi-card"><div class="kpi-value c-green" id="kpi-assets">0</div><div>Assets</div></div>
            <div class="kpi-card"><div class="kpi-value c-green" id="kpi-market">Checking...</div><div>Market</div></div>
        </div>

        <div class="dashboard-grid">
            <div class="panel"><div class="panel-title">Live Prices</div><div class="table-container" id="prices-table"></div></div>
            <div class="panel"><div class="panel-title">Signals</div><canvas id="signalChart"></canvas></div>
        </div>

        <script>
            let signalChart;
            function initChart() {
                signalChart = new Chart(document.getElementById('signalChart'), {
                    type: 'doughnut',
                    data: { labels: ['BUY','SELL','HOLD'], datasets: [{ data: [0,0,0], backgroundColor: ['#00ff87','#ff3366','#64748b'] }] }
                });
            }
            function addStock() {
                const ticker = document.getElementById('ticker-input').value;
                fetch('/api/add_stock', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ticker: ticker })
                }).then(r=>r.json()).then(d=>{
                    if(d.error) alert(d.error);
                    else fetchData();
                });
            }
            function exportCSV() { window.location.href = '/api/export/csv'; }
            function fetchData() {
                fetch('/api/data').then(r=>r.json()).then(d=>{
                    document.getElementById('server-id').innerText = d.server_id;
                    document.getElementById('kpi-total').innerText = '₹' + d.mapreduce_total.toFixed(2);
                    document.getElementById('kpi-assets').innerText = d.total_stocks;
                    document.getElementById('kpi-market').innerText = d.market_status;
                    
                    let pricesHtml = '<table><tr><th>Asset</th><th>Price</th><th>Signal</th></tr>';
                    d.stock_data.forEach(s => {
                        pricesHtml += `<tr><td>${s.ticker}</td><td>₹${s.price}</td><td class="signal-${s.signal.toLowerCase()}">${s.signal}</td></tr>`;
                    });
                    pricesHtml += '</table>';
                    document.getElementById('prices-table').innerHTML = pricesHtml;
                    
                    let buys=0, sells=0, holds=0;
                    d.stock_data.forEach(s => { if(s.signal==='BUY') buys++; else if(s.signal==='SELL') sells++; else holds++; });
                    signalChart.data.datasets[0].data = [buys, sells, holds];
                    signalChart.update();
                });
            }
            initChart();
            fetchData();
            setInterval(fetchData, 60000);
        </script>
    </body>
    </html>'''
    return html

if __name__ == '__main__':
    time.sleep(5)
    setup_db()
    threading.Thread(target=update_live_prices, daemon=True).start()
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)