# app.py - Enhanced Quantum Trading Terminal with All Features
from flask import Flask, jsonify, request, Response, send_file
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
            return True
    except:
        logger.warning("⚠️ CouchDB not reachable, using in-memory fallback")
    return False

COUCHDB_AVAILABLE = check_couchdb_connection()

# In-memory fallback storage
memory_stock_data = {}
memory_lock = threading.Lock()

def setup_db():
    """Initialize database (CouchDB or in-memory)"""
    global COUCHDB_AVAILABLE
    if COUCHDB_AVAILABLE:
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
            
            COUCHDB_AVAILABLE = False
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
        time.sleep(60)  # Update every minute
        
        try:
            if COUCHDB_AVAILABLE:
                # Fetch all documents from CouchDB
                resp = requests.get(COUCHDB_URL + DB_NAME + "/_all_docs?include_docs=true", timeout=5)
                docs = [row['doc'] for row in resp.json().get('rows', []) if not row['id'].startswith('_design')]
            else:
                # Use in-memory data
                with memory_lock:
                    docs = list(memory_stock_data.values())
            
            updated_docs = []
            for doc in docs:
                symbol = doc.get('symbol', doc.get('ticker', ''))
                if not symbol:
                    continue
                
                try:
                    # Fetch REAL price from Yahoo Finance
                    stock = yf.Ticker(symbol)
                    hist = stock.history(period="1d", interval="5m")
                    
                    if not hist.empty:
                        current_price = round(hist['Close'].iloc[-1], 2)
                        prev_close = doc.get('price', current_price)
                        
                        # Calculate change
                        change = round(current_price - prev_close, 2)
                        change_percent = round((change / prev_close) * 100, 2) if prev_close != 0 else 0
                        
                        # Get historical prices for indicators
                        hist_data = stock.history(period="1mo")
                        prices = hist_data['Close'].tolist() if not hist_data.empty else [current_price]
                        
                        rsi = calculate_rsi(prices)
                        
                        # Trading signal based on RSI and price movement
                        if rsi < 30:
                            signal = "BUY"  # Oversold
                        elif rsi > 70:
                            signal = "SELL"  # Overbought
                        elif change_percent > 2:
                            signal = "BUY"  # Strong upward movement
                        elif change_percent < -2:
                            signal = "SELL"  # Strong downward movement
                        else:
                            signal = "HOLD"
                        
                        doc['price'] = current_price
                        doc['change'] = change
                        doc['change_percent'] = change_percent
                        doc['signal'] = signal
                        doc['rsi'] = rsi
                        doc['last_updated'] = datetime.now().isoformat()
                        
                    else:
                        # Fallback to simulated data if API fails
                        old_price = doc.get('price', 1000.0)
                        jitter = random.uniform(-10.0, 10.0)
                        current_price = round(max(old_price + jitter, 0.1), 2)
                        
                        change = round(current_price - old_price, 2)
                        change_percent = round((change / old_price) * 100, 2) if old_price != 0 else 0
                        
                        # Generate signal
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
                    # Keep old data on error
                    pass
                    
                updated_docs.append(doc)
            
            # Save updates
            if COUCHDB_AVAILABLE and updated_docs:
                try:
                    requests.post(COUCHDB_URL + DB_NAME + "/_bulk_docs", json={"docs": updated_docs}, timeout=5)
                except:
                    logger.warning("Failed to update CouchDB, falling back to in-memory")
                    with memory_lock:
                        for doc in updated_docs:
                            memory_stock_data[doc['ticker']] = doc
            elif not COUCHDB_AVAILABLE:
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
    
    # Determine display name and exchange
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
        # Fetch real stock data
        stock = yf.Ticker(symbol)
        hist = stock.history(period="1d")
        
        if hist.empty:
            # If market closed, suggest crypto or try with different format
            if symbol.endswith('.NS'):
                return jsonify({"error": "NSE market closed. Try crypto (BTC-USD) or wait until Monday!"}), 400
            elif not '-' in symbol:
                # Try with different format for crypto
                crypto_symbol = symbol + "-USD"
                crypto_stock = yf.Ticker(crypto_symbol)
                crypto_hist = crypto_stock.history(period="1d")
                if not crypto_hist.empty:
                    symbol = crypto_symbol
                    display_name = symbol.replace('-USD', '')
                    exchange = "Crypto"
                    hist = crypto_hist
                else:
                    return jsonify({"error": f"Market closed or invalid ticker. Try crypto like BTC-USD"}), 400
            else:
                return jsonify({"error": "Market closed or invalid ticker. Try crypto like BTC-USD"}), 400
            
        price = round(hist['Close'].iloc[-1], 2)
        open_price = hist['Open'].iloc[-1] if not hist.empty else price
        change = round(price - open_price, 2)
        change_percent = round((change / open_price) * 100, 2) if open_price != 0 else 0
        
        # Get historical data for indicators
        hist_data = stock.history(period="1mo")
        prices = hist_data['Close'].tolist() if not hist_data.empty else [price]
        rsi = calculate_rsi(prices)
        moving_avgs = calculate_moving_averages(prices)
        
        # Create document
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
        
        if COUCHDB_AVAILABLE:
            # Check if exists in CouchDB
            check_resp = requests.get(COUCHDB_URL + DB_NAME + "/" + display_name)
            if check_resp.status_code == 200:
                new_doc['_rev'] = check_resp.json()['_rev']
                
            # Save to CouchDB
            response = requests.put(
                COUCHDB_URL + DB_NAME + "/" + display_name,
                json=new_doc
            )
            
            if response.status_code in [200, 201]:
                return jsonify({"success": True, "price": price, "exchange": exchange})
            else:
                return jsonify({"error": "Failed to save to database"}), 500
        else:
            # Save to in-memory
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
        if COUCHDB_AVAILABLE:
            # Get all documents from CouchDB
            response = requests.get(COUCHDB_URL + DB_NAME + "/_all_docs?include_docs=true", timeout=5)
            data = [row['doc'] for row in response.json().get('rows', []) if not row['id'].startswith('_design')]
            
            # Get MapReduce total
            try:
                mr_response = requests.get(COUCHDB_URL + DB_NAME + "/_design/analytics/_view/portfolio_value", timeout=5)
                mr_rows = mr_response.json().get('rows', [])
                total_value = mr_rows[0]['value'] if mr_rows else 0.0
            except:
                total_value = sum(item.get('price', 0) for item in data)
            
            # Get signal distribution
            signals = {}
            for item in data:
                signal = item.get('signal', 'HOLD')
                signals[signal] = signals.get(signal, 0) + 1
                
            # Get exchange distribution
            exchanges = {}
            for item in data:
                exchange = item.get('exchange', 'Unknown')
                exchanges[exchange] = exchanges.get(exchange, 0) + 1
        else:
            # Use in-memory data
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
        
        # Format signal distribution for response
        signal_distribution = [{"key": k, "value": v} for k, v in signals.items()]
        exchange_distribution = [{"key": k, "value": v} for k, v in exchanges.items()]
        
    except Exception as e:
        logger.error(f"Data fetch error: {e}")
        # Fallback to in-memory if CouchDB fails
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
        "database": "CouchDB" if COUCHDB_AVAILABLE else "In-Memory"
    })

@app.route('/api/history/<ticker>')
def get_history(ticker):
    """Get historical data for a ticker"""
    try:
        # Get symbol
        if COUCHDB_AVAILABLE:
            resp = requests.get(COUCHDB_URL + DB_NAME + "/" + ticker, timeout=5)
            if resp.status_code != 200:
                return jsonify({"error": "Ticker not found"}), 404
            doc = resp.json()
            symbol = doc.get('symbol', ticker)
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
        if COUCHDB_AVAILABLE:
            response = requests.get(COUCHDB_URL + DB_NAME + "/_all_docs?include_docs=true", timeout=5)
            data = [row['doc'] for row in response.json().get('rows', []) if not row['id'].startswith('_design')]
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
        'database': 'CouchDB' if COUCHDB_AVAILABLE else 'In-Memory'
    })

@app.route('/api/health')
def health_check():
    """Health check endpoint for load balancers"""
    try:
        status = "healthy"
        db_status = "connected" if COUCHDB_AVAILABLE else "in-memory"
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
        if COUCHDB_AVAILABLE:
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
            "database_mode": "CouchDB" if COUCHDB_AVAILABLE else "In-Memory"
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
        <title>🚀 Quantum Trading Terminal - Live Market Dashboard</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Rajdhani:wght@500;700&display=swap');
            
            :root {
                --bg-deep: #0a0f1c;
                --panel-bg: rgba(18, 25, 45, 0.85);
                --neon-blue: #00f2fe;
                --neon-green: #00ff87;
                --neon-purple: #b026ff;
                --neon-red: #ff3366;
                --neon-yellow: #ffd966;
                --text-main: #e2e8f0;
                --border-glow: rgba(0, 242, 254, 0.2);
                --card-shadow: 0 8px 32px rgba(0, 242, 254, 0.1);
            }
            
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                background-color: var(--bg-deep);
                color: var(--text-main);
                font-family: 'Rajdhani', sans-serif;
                padding: 20px;
                background-image: 
                    radial-gradient(circle at 10% 20%, rgba(0, 242, 254, 0.05) 0%, transparent 30%),
                    radial-gradient(circle at 90% 80%, rgba(176, 38, 255, 0.05) 0%, transparent 30%);
                min-height: 100vh;
            }
            
            .top-bar {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 25px;
                border-bottom: 1px solid var(--border-glow);
                padding-bottom: 15px;
                flex-wrap: wrap;
                gap: 15px;
                background: rgba(0,0,0,0.2);
                padding: 15px 20px;
                border-radius: 12px;
                backdrop-filter: blur(10px);
            }
            
            h1 {
                font-family: 'Orbitron', sans-serif;
                color: var(--neon-blue);
                margin: 0;
                text-shadow: 0 0 20px rgba(0,242,254,0.7);
                font-size: 2rem;
                letter-spacing: 2px;
            }
            
            .badge-container {
                display: flex;
                gap: 12px;
                align-items: center;
                flex-wrap: wrap;
            }
            
            .badge {
                padding: 6px 18px;
                border-radius: 25px;
                font-size: 0.9rem;
                font-weight: bold;
                background: rgba(0,0,0,0.6);
                border: 1px solid;
                backdrop-filter: blur(5px);
                transition: all 0.3s ease;
            }
            
            .badge:hover {
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(0,242,254,0.3);
            }
            
            .badge.nse { border-color: #00ff87; color: #00ff87; }
            .badge.nasdaq { border-color: #00f2fe; color: #00f2fe; }
            .badge.crypto { border-color: #b026ff; color: #b026ff; }
            
            .server-badge {
                background: rgba(0, 242, 254, 0.15);
                border: 1px solid var(--neon-blue);
                padding: 8px 20px;
                border-radius: 25px;
                color: var(--neon-blue);
                font-family: monospace;
                box-shadow: 0 0 20px var(--border-glow);
                backdrop-filter: blur(5px);
            }
            
            .controls {
                display: flex;
                gap: 12px;
                align-items: center;
                flex-wrap: wrap;
            }
            
            select, button {
                background: rgba(0,0,0,0.6);
                border: 1px solid var(--neon-purple);
                color: white;
                padding: 12px 24px;
                border-radius: 8px;
                font-family: 'Orbitron', sans-serif;
                cursor: pointer;
                font-size: 0.95rem;
                backdrop-filter: blur(5px);
                transition: all 0.3s ease;
            }
            
            select {
                min-width: 220px;
                background: rgba(10, 15, 30, 0.8);
            }
            
            select option {
                background: var(--bg-deep);
                color: white;
                padding: 10px;
            }
            
            button {
                background: linear-gradient(135deg, var(--neon-purple), var(--neon-blue));
                border: none;
                font-weight: bold;
                letter-spacing: 1px;
            }
            
            button:hover {
                transform: translateY(-3px);
                box-shadow: 0 8px 25px rgba(0,242,254,0.6);
            }
            
            .kpi-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 25px;
            }
            
            .kpi-card {
                background: var(--panel-bg);
                border: 1px solid #1e3a8a;
                border-radius: 16px;
                padding: 25px 20px;
                text-align: center;
                backdrop-filter: blur(10px);
                transition: all 0.3s ease;
                box-shadow: var(--card-shadow);
            }
            
            .kpi-card:hover {
                transform: translateY(-5px);
                box-shadow: 0 12px 40px rgba(0,242,254,0.2);
            }
            
            .kpi-title {
                font-size: 1rem;
                color: #94a3b8;
                text-transform: uppercase;
                letter-spacing: 2px;
                margin-bottom: 12px;
            }
            
            .kpi-value {
                font-size: 2.2rem;
                font-weight: bold;
                font-family: 'Orbitron', sans-serif;
            }
            
            .c-green { color: var(--neon-green); }
            .c-blue { color: var(--neon-blue); }
            .c-purple { color: var(--neon-purple); }
            .c-red { color: var(--neon-red); }
            .c-yellow { color: var(--neon-yellow); }
            
            .dashboard-grid {
                display: grid;
                grid-template-columns: 2fr 1fr;
                gap: 25px;
                margin-bottom: 25px;
            }
            
            .dashboard-grid-4 {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 25px;
                margin-bottom: 25px;
            }
            
            .panel {
                background: var(--panel-bg);
                border: 1px solid #1e3a8a;
                border-radius: 16px;
                padding: 20px;
                box-shadow: var(--card-shadow);
                position: relative;
                backdrop-filter: blur(10px);
            }
            
            .panel::before {
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 2px;
                background: linear-gradient(90deg, transparent, var(--neon-blue), var(--neon-purple), transparent);
            }
            
            .panel-title {
                font-size: 1.2rem;
                margin-bottom: 20px;
                text-align: center;
                border-bottom: 1px dashed #1e3a8a;
                padding-bottom: 12px;
                color: var(--neon-blue);
                font-weight: bold;
                letter-spacing: 1px;
            }
            
            .chart-box {
                height: 320px;
                position: relative;
                width: 100%;
            }
            
            .table-container {
                max-height: 400px;
                overflow-y: auto;
                border-radius: 8px;
                scrollbar-width: thin;
                scrollbar-color: var(--neon-blue) #1e3a8a;
            }
            
            .table-container::-webkit-scrollbar {
                width: 6px;
            }
            
            .table-container::-webkit-scrollbar-track {
                background: #1e3a8a;
            }
            
            .table-container::-webkit-scrollbar-thumb {
                background: var(--neon-blue);
                border-radius: 3px;
            }
            
            table {
                width: 100%;
                border-collapse: collapse;
            }
            
            th {
                background: linear-gradient(135deg, rgba(0, 242, 254, 0.2), rgba(176, 38, 255, 0.2));
                padding: 14px 12px;
                text-align: left;
                font-weight: bold;
                color: var(--neon-blue);
                position: sticky;
                top: 0;
                backdrop-filter: blur(10px);
                font-size: 0.95rem;
            }
            
            td {
                padding: 12px 12px;
                border-bottom: 1px solid rgba(255,255,255,0.05);
                font-size: 0.95rem;
            }
            
            tr:hover {
                background: rgba(255,255,255,0.05);
                cursor: pointer;
            }
            
            .signal-buy {
                color: var(--neon-green);
                font-weight: bold;
                text-shadow: 0 0 10px rgba(0,255,135,0.5);
            }
            
            .signal-sell {
                color: var(--neon-red);
                font-weight: bold;
                text-shadow: 0 0 10px rgba(255,51,102,0.5);
            }
            
            .signal-hold {
                color: #94a3b8;
            }
            
            .positive {
                color: var(--neon-green);
                font-weight: bold;
            }
            
            .negative {
                color: var(--neon-red);
                font-weight: bold;
            }
            
            .export-btn {
                background: linear-gradient(135deg, #00ff87, #00f2fe);
                color: #0a0f1c;
                border: none;
                padding: 10px 20px;
                border-radius: 8px;
                cursor: pointer;
                font-weight: bold;
                font-family: 'Orbitron', sans-serif;
                transition: all 0.3s ease;
            }
            
            .export-btn:hover {
                transform: translateY(-3px);
                box-shadow: 0 8px 25px rgba(0,255,135,0.6);
            }
            
            .news-container {
                max-height: 320px;
                overflow-y: auto;
                padding-right: 5px;
            }
            
            .news-item {
                padding: 15px;
                border-bottom: 1px solid rgba(255,255,255,0.1);
                transition: all 0.3s ease;
            }
            
            .news-item:hover {
                background: rgba(255,255,255,0.05);
                transform: translateX(5px);
            }
            
            .news-title {
                color: var(--neon-blue);
                text-decoration: none;
                font-size: 0.95rem;
                font-weight: 500;
                line-height: 1.4;
                display: block;
                margin-bottom: 5px;
            }
            
            .news-title:hover {
                text-decoration: underline;
                color: var(--neon-green);
            }
            
            .news-source {
                font-size: 0.8rem;
                color: #94a3b8;
            }
            
            .market-status {
                display: inline-block;
                padding: 5px 15px;
                border-radius: 20px;
                font-size: 0.9rem;
                font-weight: bold;
                background: rgba(0,0,0,0.5);
                margin-left: 10px;
            }
            
            .status-open {
                color: #00ff87;
                border: 1px solid #00ff87;
            }
            
            .status-closed {
                color: #ff3366;
                border: 1px solid #ff3366;
            }
            
            @media (max-width: 1200px) {
                .dashboard-grid, .dashboard-grid-4 {
                    grid-template-columns: 1fr;
                }
            }
        </style>
    </head>
    <body>
        <div class="top-bar">
            <div>
                <h1>⚡ QUANTUM TRADING TERMINAL</h1>
                <div class="badge-container" style="margin-top: 12px;">
                    <span class="badge nse">🇮🇳 NSE India</span>
                    <span class="badge nasdaq">🇺🇸 NASDAQ US</span>
                    <span class="badge crypto">₿ Crypto 24/7</span>
                    <span class="market-status" id="market-status">Loading...</span>
                </div>
            </div>
            <div class="controls">
                <select id="exchange-filter">
                    <option value="all">🌐 All Exchanges</option>
                    <option value="NSE (India)">🇮🇳 NSE India</option>
                    <option value="NASDAQ (US)">🇺🇸 NASDAQ US</option>
                    <option value="Crypto">₿ Crypto</option>
                </select>
                <select id="ticker-input">
                    <option value="RELIANCE.NS">🇮🇳 RELIANCE (NSE)</option>
                    <option value="TCS.NS">🇮🇳 TCS (NSE)</option>
                    <option value="HDFCBANK.NS">🇮🇳 HDFC Bank (NSE)</option>
                    <option value="INFY.NS">🇮🇳 INFOSYS (NSE)</option>
                    <option value="WIPRO.NS">🇮🇳 WIPRO (NSE)</option>
                    <option value="TATAMOTORS.NS">🇮🇳 TATA MOTORS (NSE)</option>
                    <option value="SBIN.NS">🇮🇳 SBI (NSE)</option>
                    <option value="ITC.NS">🇮🇳 ITC (NSE)</option>
                    <option value="BHARTIARTL.NS">🇮🇳 AIRTEL (NSE)</option>
                    <option value="AAPL">🇺🇸 APPLE (NASDAQ)</option>
                    <option value="GOOGL">🇺🇸 GOOGLE (NASDAQ)</option>
                    <option value="MSFT">🇺🇸 MICROSOFT (NASDAQ)</option>
                    <option value="AMZN">🇺🇸 AMAZON (NASDAQ)</option>
                    <option value="TSLA">🇺🇸 TESLA (NASDAQ)</option>
                    <option value="META">🇺🇸 META (NASDAQ)</option>
                    <option value="NVDA">🇺🇸 NVIDIA (NASDAQ)</option>
                    <option value="BTC-USD">₿ BITCOIN (CRYPTO)</option>
                    <option value="ETH-USD">₿ ETHEREUM (CRYPTO)</option>
                    <option value="DOGE-USD">₿ DOGECOIN (CRYPTO)</option>
                    <option value="BNB-USD">₿ BNB (CRYPTO)</option>
                    <option value="SOL-USD">₿ SOLANA (CRYPTO)</option>
                </select>
                <button onclick="addStock()" id="add-btn">➕ ADD ASSET</button>
                <button onclick="exportCSV()" class="export-btn">📥 EXPORT CSV</button>
                <button onclick="refreshData()" style="padding: 12px 20px;">🔄 REFRESH</button>
            </div>
            <div class="server-badge">
                <span id="server-id">CONNECTING...</span> | v<span id="version">3.0</span>
            </div>
        </div>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-title">Total Portfolio</div>
                <div class="kpi-value c-green" id="kpi-total">₹0</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Active Assets</div>
                <div class="kpi-value c-blue" id="kpi-assets">0</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">NSE Stocks</div>
                <div class="kpi-value c-purple" id="kpi-nse">0</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">US Stocks</div>
                <div class="kpi-value c-yellow" id="kpi-us">0</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Crypto</div>
                <div class="kpi-value c-green" id="kpi-crypto">0</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Market Status</div>
                <div class="kpi-value" id="kpi-market">Checking...</div>
            </div>
        </div>

        <div class="dashboard-grid">
            <div class="panel">
                <div class="panel-title">📈 Live Price History (7 Days)</div>
                <div class="chart-box">
                    <canvas id="historyChart"></canvas>
                </div>
            </div>
            <div class="panel">
                <div class="panel-title">📊 Exchange Distribution</div>
                <div class="chart-box">
                    <canvas id="exchangeChart"></canvas>
                </div>
            </div>
        </div>

        <div class="dashboard-grid-4">
            <div class="panel">
                <div class="panel-title">💰 Live Prices</div>
                <div class="table-container">
                    <table id="prices-table">
                        <thead>
                            <tr>
                                <th>Asset</th>
                                <th>Price</th>
                                <th>24h %</th>
                                <th>Signal</th>
                            </tr>
                        </thead>
                        <tbody id="prices-body"></tbody>
                    </table>
                </div>
            </div>
            <div class="panel">
                <div class="panel-title">📊 RSI Indicators</div>
                <div class="table-container">
                    <table id="rsi-table">
                        <thead>
                            <tr>
                                <th>Asset</th>
                                <th>RSI</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody id="rsi-body"></tbody>
                    </table>
                </div>
            </div>
            <div class="panel">
                <div class="panel-title">🎯 Trading Signals</div>
                <div class="chart-box">
                    <canvas id="signalChart"></canvas>
                </div>
            </div>
            <div class="panel">
                <div class="panel-title">📰 Latest News</div>
                <div class="news-container" id="news-container">
                    <div class="news-item">Loading news...</div>
                </div>
            </div>
        </div>

        <div class="panel">
            <div class="panel-title">📋 Detailed Portfolio</div>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Asset</th>
                            <th>Exchange</th>
                            <th>Price</th>
                            <th>Change</th>
                            <th>Change %</th>
                            <th>Signal</th>
                            <th>RSI</th>
                            <th>Volume</th>
                            <th>Last Updated</th>
                        </tr>
                    </thead>
                    <tbody id="detailed-body"></tbody>
                </table>
            </div>
        </div>

        <script>
            let historyChart, exchangeChart, signalChart;
            let currentTicker = 'RELIANCE';
            let refreshInterval = 60000; // 60 seconds

            // Initialize charts
            function initCharts() {
                // History Chart
                const ctxHistory = document.getElementById('historyChart').getContext('2d');
                historyChart = new Chart(ctxHistory, {
                    type: 'line',
                    data: {
                        labels: [],
                        datasets: [{
                            label: 'Price',
                            data: [],
                            borderColor: '#00ff87',
                            backgroundColor: 'rgba(0,255,135,0.1)',
                            tension: 0.4,
                            fill: true,
                            pointRadius: 2,
                            pointHoverRadius: 6
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false },
                            tooltip: { mode: 'index', intersect: false }
                        },
                        scales: {
                            x: { 
                                grid: { color: 'rgba(255,255,255,0.05)' },
                                ticks: { maxRotation: 45, minRotation: 45 }
                            },
                            y: { 
                                grid: { color: 'rgba(255,255,255,0.05)' },
                                ticks: { callback: function(value) { return '₹' + value; } }
                            }
                        }
                    }
                });

                // Exchange Chart
                const ctxExchange = document.getElementById('exchangeChart').getContext('2d');
                exchangeChart = new Chart(ctxExchange, {
                    type: 'doughnut',
                    data: {
                        labels: ['NSE India', 'NASDAQ US', 'Crypto'],
                        datasets: [{
                            data: [0, 0, 0],
                            backgroundColor: ['#00ff87', '#00f2fe', '#b026ff'],
                            borderWidth: 0,
                            hoverOffset: 10
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { position: 'bottom', labels: { color: '#e2e8f0', font: { size: 12 } } }
                        },
                        cutout: '65%'
                    }
                });

                // Signal Chart
                const ctxSignal = document.getElementById('signalChart').getContext('2d');
                signalChart = new Chart(ctxSignal, {
                    type: 'doughnut',
                    data: {
                        labels: ['BUY', 'SELL', 'HOLD'],
                        datasets: [{
                            data: [0, 0, 0],
                            backgroundColor: ['#00ff87', '#ff3366', '#64748b'],
                            borderWidth: 0,
                            hoverOffset: 10
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        cutout: '65%',
                        plugins: {
                            legend: { position: 'bottom', labels: { color: '#e2e8f0', font: { size: 12 } } }
                        }
                    }
                });
            }

            // Add stock function
            function addStock() {
                const ticker = document.getElementById('ticker-input').value;
                const btn = document.getElementById('add-btn');
                
                btn.innerText = "⏳ FETCHING...";
                btn.disabled = true;
                
                fetch('/api/add_stock', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ticker: ticker })
                })
                .then(r => r.json())
                .then(d => {
                    btn.innerText = "➕ ADD ASSET";
                    btn.disabled = false;
                    if(d.error) {
                        alert("⚠️ " + d.error);
                    } else {
                        fetchData();
                        loadNews(ticker.split('.')[0].replace('-USD', ''));
                        showNotification(`✅ Added ${ticker} successfully!`, 'success');
                    }
                })
                .catch(e => {
                    btn.innerText = "➕ ADD ASSET";
                    btn.disabled = false;
                    alert("Network error: " + e.message);
                });
            }

            // Export CSV
            function exportCSV() {
                window.location.href = '/api/export/csv';
                showNotification('📥 Downloading CSV...', 'info');
            }

            // Refresh data
            function refreshData() {
                fetchData();
                showNotification('🔄 Refreshing data...', 'info');
            }

            // Show notification
            function showNotification(message, type) {
                // Simple console notification for now
                console.log(`[${type}] ${message}`);
            }

            // Load news for ticker
            function loadNews(ticker) {
                fetch(`/api/news/${ticker}`)
                    .then(r => r.json())
                    .then(data => {
                        const container = document.getElementById('news-container');
                        if (data.news && data.news.length > 0) {
                            container.innerHTML = data.news.map(item => `
                                <div class="news-item">
                                    <a href="${item.link}" target="_blank" class="news-title">📰 ${item.title}</a>
                                    <div class="news-source">${item.source} • ${new Date(item.published).toLocaleString()}</div>
                                </div>
                            `).join('');
                        } else {
                            container.innerHTML = '<div class="news-item">No news available</div>';
                        }
                    })
                    .catch(() => {
                        document.getElementById('news-container').innerHTML = '<div class="news-item">News temporarily unavailable</div>';
                    });
            }

            // Load history for ticker
            function loadHistory(ticker) {
                fetch(`/api/history/${ticker}`)
                    .then(r => r.json())
                    .then(data => {
                        if (data.dates && data.prices) {
                            historyChart.data.labels = data.dates.slice(-50);
                            historyChart.data.datasets[0].data = data.prices.slice(-50);
                            historyChart.update();
                        }
                    });
            }

            // Main data fetch
            function fetchData() {
                fetch('/api/data')
                .then(r => r.json())
                .then(d => {
                    // Update server info
                    document.getElementById('server-id').innerText = d.server_id;
                    
                    // Update KPIs
                    document.getElementById('kpi-total').innerText = '₹' + d.mapreduce_total.toLocaleString('en-IN', {minimumFractionDigits: 2});
                    document.getElementById('kpi-assets').innerText = d.total_stocks;
                    
                    // Update market status
                    const marketStatus = d.market_status || 'Unknown';
                    const statusEl = document.getElementById('market-status');
                    statusEl.innerText = marketStatus;
                    statusEl.className = 'market-status ' + (marketStatus.includes('Open') ? 'status-open' : 'status-closed');
                    
                    document.getElementById('kpi-market').innerHTML = marketStatus;
                    
                    // Count by exchange
                    let nse = 0, us = 0, crypto = 0;
                    d.stock_data.forEach(s => {
                        if (s.exchange === 'NSE (India)') nse++;
                        else if (s.exchange === 'NASDAQ (US)') us++;
                        else if (s.exchange === 'Crypto') crypto++;
                    });
                    
                    document.getElementById('kpi-nse').innerText = nse;
                    document.getElementById('kpi-us').innerText = us;
                    document.getElementById('kpi-crypto').innerText = crypto;
                    
                    // Update exchange chart
                    exchangeChart.data.datasets[0].data = [nse, us, crypto];
                    exchangeChart.update();
                    
                    // Update prices table
                    const pricesBody = document.getElementById('prices-body');
                    pricesBody.innerHTML = d.stock_data.map(s => `
                        <tr onclick="loadHistory('${s.ticker}'); loadNews('${s.ticker}')">
                            <td><strong>${s.ticker}</strong></td>
                            <td class="${s.change > 0 ? 'positive' : s.change < 0 ? 'negative' : ''}">
                                ${s.exchange === 'NASDAQ (US)' ? '$' : '₹'}${s.price.toLocaleString()}
                            </td>
                            <td class="${s.change_percent > 0 ? 'positive' : s.change_percent < 0 ? 'negative' : ''}">
                                ${s.change_percent > 0 ? '▲' : s.change_percent < 0 ? '▼' : ''} ${Math.abs(s.change_percent).toFixed(2)}%
                            </td>
                            <td class="signal-${s.signal.toLowerCase()}">${s.signal}</td>
                        </tr>
                    `).join('');
                    
                    // Update RSI table
                    const rsiBody = document.getElementById('rsi-body');
                    rsiBody.innerHTML = d.stock_data.map(s => {
                        const rsi = s.rsi || 50;
                        return `
                        <tr>
                            <td><strong>${s.ticker}</strong></td>
                            <td>${rsi}</td>
                            <td class="${rsi < 30 ? 'positive' : rsi > 70 ? 'negative' : ''}">
                                ${rsi < 30 ? '🟢 Oversold' : rsi > 70 ? '🔴 Overbought' : '⚪ Neutral'}
                            </td>
                        </tr>
                    `}).join('');
                    
                    // Update signal chart
                    const signalMap = {};
                    d.signal_distribution.forEach(s => signalMap[s.key] = s.value);
                    signalChart.data.datasets[0].data = [
                        signalMap['BUY'] || 0,
                        signalMap['SELL'] || 0,
                        signalMap['HOLD'] || 0
                    ];
                    signalChart.update();
                    
                    // Update detailed table
                    const detailedBody = document.getElementById('detailed-body');
                    detailedBody.innerHTML = d.stock_data.map(s => `
                        <tr>
                            <td><strong>${s.ticker}</strong></td>
                            <td>${s.exchange || 'Unknown'}</td>
                            <td class="${s.change > 0 ? 'positive' : s.change < 0 ? 'negative' : ''}">
                                ${s.exchange === 'NASDAQ (US)' ? '$' : '₹'}${s.price.toLocaleString()}
                            </td>
                            <td class="${s.change > 0 ? 'positive' : s.change < 0 ? 'negative' : ''}">
                                ${s.change > 0 ? '+' : ''}${s.change?.toFixed(2) || 0}
                            </td>
                            <td class="${s.change_percent > 0 ? 'positive' : s.change_percent < 0 ? 'negative' : ''}">
                                ${s.change_percent > 0 ? '▲' : s.change_percent < 0 ? '▼' : ''} ${Math.abs(s.change_percent || 0).toFixed(2)}%
                            </td>
                            <td class="signal-${(s.signal || 'HOLD').toLowerCase()}">${s.signal || 'HOLD'}</td>
                            <td>${s.rsi || 50}</td>
                            <td>${(s.volume || 0).toLocaleString()}</td>
                            <td>${new Date(s.last_updated).toLocaleTimeString()}</td>
                        </tr>
                    `).join('');
                })
                .catch(error => {
                    console.error('Fetch error:', error);
                    document.getElementById('prices-body').innerHTML = '<tr><td colspan="4">Error loading data. Retrying...</td></tr>';
                });
            }

            // Filter by exchange
            document.getElementById('exchange-filter').addEventListener('change', function(e) {
                const filter = e.target.value;
                const tables = ['#prices-body tr', '#detailed-body tr', '#rsi-body tr'];
                
                tables.forEach(selector => {
                    const rows = document.querySelectorAll(selector);
                    rows.forEach(row => {
                        if (filter === 'all') {
                            row.style.display = '';
                        } else {
                            const exchange = row.cells[1]?.innerText || '';
                            if (exchange.includes(filter)) {
                                row.style.display = '';
                            } else {
                                row.style.display = 'none';
                            }
                        }
                    });
                });
            });

            // Load version
            fetch('/api/version')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('version').innerText = data.version;
                });

            // Initialize
            initCharts();
            fetchData();
            setInterval(fetchData, refreshInterval);
            loadHistory('RELIANCE');
            loadNews('RELIANCE');
        </script>
    </body>
    </html>'''
    
    return html

if __name__ == '__main__':
    # Wait for services to be ready
    time.sleep(5)
    
    # Setup database
    setup_db()
    
    # Start price update thread
    price_thread = threading.Thread(target=update_live_prices, daemon=True)
    price_thread.start()
    
    # Start Flask app
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('DEBUG', 'False').lower() == 'true'
    
    logger.info(f"🚀 Quantum Trading Terminal starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug)