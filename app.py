# app.py - Enhanced version with Real Data, Indicators, Export, Multiple Exchanges
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

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration from environment variables
COUCHDB_URL = os.getenv('COUCHDB_URL', "http://admin:password@couchdb:5984/")
DB_NAME = os.getenv('DB_NAME', "stocks")
ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')

# Track start time for uptime
start_time = time.time()

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
    
    # Crypto
    "BTC-USD": "BTC-USD",
    "ETH-USD": "ETH-USD",
    "DOGE-USD": "DOGE-USD",
}

def setup_db():
    """Initialize CouchDB database"""
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
                    change_percent = round((change / hist['Open'].iloc[-1]) * 100, 2)
                else:
                    price = 1000.0
                    change = 0.0
                    change_percent = 0.0
                    
            except Exception as e:
                logger.error(f"Error fetching {symbol}: {e}")
                price = 1000.0
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
            
        logger.info("Database initialized successfully")
        
    except Exception as e:
        logger.error(f"Database setup error: {e}")

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

def update_live_prices():
    """Update stock prices with REAL data"""
    while True:
        time.sleep(60)  # Update every minute (respects API limits)
        try:
            # Fetch all documents
            resp = requests.get(COUCHDB_URL + DB_NAME + "/_all_docs?include_docs=true")
            docs = [row['doc'] for row in resp.json().get('rows', []) if not row['id'].startswith('_design')]
            
            updated_docs = []
            for doc in docs:
                symbol = doc.get('symbol', doc.get('ticker', ''))
                if not symbol:
                    continue
                
                try:
                    # Fetch REAL price from Yahoo Finance
                    stock = yf.Ticker(symbol)
                    hist = stock.history(period="1d", interval="1m")
                    
                    if not hist.empty:
                        current_price = round(hist['Close'].iloc[-1], 2)
                        prev_close = doc.get('price', current_price)
                        
                        # Calculate change
                        change = round(current_price - prev_close, 2)
                        change_percent = round((change / prev_close) * 100, 2)
                        
                        # Generate signal based on RSI
                        # Get historical prices for indicators
                        hist_data = stock.history(period="1mo")
                        prices = hist_data['Close'].tolist()
                        
                        rsi = calculate_rsi(prices)
                        
                        # Trading signal based on RSI
                        if rsi < 30:
                            signal = "BUY"  # Oversold
                        elif rsi > 70:
                            signal = "SELL"  # Overbought
                        else:
                            signal = "HOLD"
                        
                        # Calculate moving averages
                        moving_avgs = calculate_moving_averages(prices)
                        
                        doc['price'] = current_price
                        doc['change'] = change
                        doc['change_percent'] = change_percent
                        doc['signal'] = signal
                        doc['rsi'] = rsi
                        doc['moving_averages'] = moving_avgs
                        doc['last_updated'] = datetime.now().isoformat()
                        
                    else:
                        # Fallback to simulated data if API fails
                        old_price = doc.get('price', 1000.0)
                        jitter = random.uniform(-5.0, 5.0)
                        current_price = round(max(old_price + jitter, 0.1), 2)
                        
                        change = round(current_price - old_price, 2)
                        change_percent = round((change / old_price) * 100, 2)
                        
                        if current_price > old_price:
                            signal = "BUY"
                        elif current_price < old_price:
                            signal = "SELL"
                        else:
                            signal = "HOLD"
                            
                        doc['price'] = current_price
                        doc['change'] = change
                        doc['change_percent'] = change_percent
                        doc['signal'] = signal
                        doc['rsi'] = 50.0
                        doc['last_updated'] = datetime.now().isoformat()
                        
                except Exception as e:
                    logger.error(f"Error updating {symbol}: {e}")
                    # Keep old data on error
                    pass
                    
                updated_docs.append(doc)
                
            # Bulk update
            if updated_docs:
                requests.post(
                    COUCHDB_URL + DB_NAME + "/_bulk_docs",
                    json={"docs": updated_docs}
                )
                    
        except Exception as e:
            logger.error(f"Price update error: {e}")

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
            return jsonify({"error": "Invalid Ticker or Market Closed"}), 400
            
        price = round(hist['Close'].iloc[-1], 2)
        change = round(hist['Close'].iloc[-1] - hist['Open'].iloc[-1], 2)
        change_percent = round((change / hist['Open'].iloc[-1]) * 100, 2)
        
        # Get historical data for indicators
        hist_data = stock.history(period="1mo")
        prices = hist_data['Close'].tolist()
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
            "last_updated": datetime.now().isoformat(),
            "added_at": datetime.now().isoformat()
        }
        
        # Check if exists
        check_resp = requests.get(COUCHDB_URL + DB_NAME + "/" + display_name)
        if check_resp.status_code == 200:
            new_doc['_rev'] = check_resp.json()['_rev']
            
        # Save to CouchDB
        response = requests.put(
            COUCHDB_URL + DB_NAME + "/" + display_name,
            json=new_doc
        )
        
        if response.status_code in [200, 201]:
            return jsonify({"success": True, "price": price})
        else:
            return jsonify({"error": "Failed to save to database"}), 500
            
    except Exception as e:
        logger.error(f"Add stock error: {e}")
        return jsonify({"error": "Failed to fetch stock data"}), 500

@app.route('/api/data')
def get_data():
    """Get all stock data with indicators"""
    try:
        # Get all documents
        response = requests.get(COUCHDB_URL + DB_NAME + "/_all_docs?include_docs=true")
        data = [row['doc'] for row in response.json().get('rows', []) if not row['id'].startswith('_design')]
        
        # Get MapReduce total
        mr_response = requests.get(COUCHDB_URL + DB_NAME + "/_design/analytics/_view/portfolio_value")
        mr_rows = mr_response.json().get('rows', [])
        total_value = mr_rows[0]['value'] if mr_rows else 0.0
        
        # Get signal distribution
        signal_response = requests.get(COUCHDB_URL + DB_NAME + "/_design/analytics/_view/stocks_by_signal?group=true")
        signals = signal_response.json().get('rows', [])
        
        # Get exchange distribution
        exchange_response = requests.get(COUCHDB_URL + DB_NAME + "/_design/analytics/_view/stocks_by_exchange?group=true")
        exchanges = exchange_response.json().get('rows', [])
        
    except Exception as e:
        logger.error(f"Data fetch error: {e}")
        data = []
        total_value = 0.0
        signals = []
        exchanges = []
    
    return jsonify({
        "server_id": socket.gethostname(),
        "environment": ENVIRONMENT,
        "timestamp": datetime.now().isoformat(),
        "stock_data": data,
        "mapreduce_total": round(total_value, 2),
        "signal_distribution": signals,
        "exchange_distribution": exchanges,
        "total_stocks": len(data)
    })

@app.route('/api/history/<ticker>')
def get_history(ticker):
    """Get historical data for a ticker"""
    try:
        # Get symbol from database
        resp = requests.get(COUCHDB_URL + DB_NAME + "/" + ticker)
        if resp.status_code != 200:
            return jsonify({"error": "Ticker not found"}), 404
        
        doc = resp.json()
        symbol = doc.get('symbol', ticker)
        
        # Fetch 7 days of historical data
        stock = yf.Ticker(symbol)
        hist = stock.history(period="7d", interval="1h")
        
        if hist.empty:
            return jsonify({"error": "No historical data"}), 404
        
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
        return jsonify({"error": str(e)}), 500

@app.route('/api/indicators/<ticker>')
def get_indicators(ticker):
    """Get technical indicators for a ticker"""
    try:
        # Get symbol from database
        resp = requests.get(COUCHDB_URL + DB_NAME + "/" + ticker)
        if resp.status_code != 200:
            return jsonify({"error": "Ticker not found"}), 404
        
        doc = resp.json()
        symbol = doc.get('symbol', ticker)
        
        # Fetch historical data
        stock = yf.Ticker(symbol)
        hist = stock.history(period="1mo")
        prices = hist['Close'].tolist()
        
        # Calculate indicators
        rsi = calculate_rsi(prices)
        moving_avgs = calculate_moving_averages(prices)
        
        # Calculate MACD
        exp1 = pd.Series(prices).ewm(span=12).mean()
        exp2 = pd.Series(prices).ewm(span=26).mean()
        macd = exp1 - exp2
        signal = macd.ewm(span=9).mean()
        
        return jsonify({
            "ticker": ticker,
            "rsi": rsi,
            "moving_averages": moving_avgs,
            "macd": {
                "macd": round(macd.iloc[-1], 2) if not macd.empty else 0,
                "signal": round(signal.iloc[-1], 2) if not signal.empty else 0
            }
        })
        
    except Exception as e:
        logger.error(f"Indicators error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/news/<ticker>')
def get_news(ticker):
    """Get news for a ticker"""
    try:
        # Fetch news from Google RSS
        news_url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-IN&gl=IN&ceid=IN:en"
        feed = feedparser.parse(news_url)
        
        news_items = []
        for entry in feed.entries[:10]:
            news_items.append({
                'title': entry.title,
                'link': entry.link,
                'published': entry.published,
                'source': entry.source.title if hasattr(entry, 'source') else 'Google News'
            })
        
        return jsonify({
            "ticker": ticker,
            "news": news_items
        })
        
    except Exception as e:
        logger.error(f"News error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/export/csv')
def export_csv():
    """Export stock data to CSV"""
    try:
        # Get all documents
        response = requests.get(COUCHDB_URL + DB_NAME + "/_all_docs?include_docs=true")
        data = [row['doc'] for row in response.json().get('rows', []) if not row['id'].startswith('_design')]
        
        # Create CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['Ticker', 'Exchange', 'Price', 'Change', 'Change %', 'Signal', 'RSI', 'Last Updated'])
        
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
                stock.get('last_updated', '')
            ])
        
        # Create response
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=stocks_export.csv'}
        )
        
    except Exception as e:
        logger.error(f"Export error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/version')
def version():
    """Get version information"""
    try:
        import subprocess
        git_hash = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode('ascii').strip()[:8]
    except:
        git_hash = "unknown"
    
    return jsonify({
        'version': '2.0.0',
        'git_commit': git_hash,
        'deployed_at': datetime.now().isoformat(),
        'environment': ENVIRONMENT,
        'uptime': round(time.time() - start_time, 2)
    })

@app.route('/api/health')
def health_check():
    """Health check endpoint for load balancers"""
    try:
        # Check CouchDB connection
        requests.get(COUCHDB_URL + "_up", timeout=5)
        return jsonify({
            "status": "healthy",
            "server": socket.gethostname(),
            "timestamp": datetime.now().isoformat()
        }), 200
    except:
        return jsonify({"status": "unhealthy"}), 500

@app.route('/api/metrics')
def get_metrics():
    """Get performance metrics"""
    try:
        # Get database info
        db_info = requests.get(COUCHDB_URL + DB_NAME)
        db_stats = db_info.json() if db_info.status_code == 200 else {}
        
        return jsonify({
            "server": socket.gethostname(),
            "database_size": db_stats.get('sizes', {}).get('file', 0),
            "document_count": db_stats.get('doc_count', 0),
            "update_sequence": db_stats.get('update_seq', 0),
            "uptime": round(time.time() - start_time, 2)
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
        <title>Quantum Trading Terminal - Multi-Exchange Edition</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
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
                background-image: radial-gradient(circle at 50% 0%, #0a1936 0%, transparent 70%);
                min-height: 100vh;
            }
            
            .top-bar {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
                border-bottom: 1px solid var(--border-glow);
                padding-bottom: 15px;
                flex-wrap: wrap;
                gap: 15px;
            }
            
            h1 {
                font-family: 'Orbitron', sans-serif;
                color: var(--neon-blue);
                margin: 0;
                text-shadow: 0 0 10px rgba(0,242,254,0.5);
                font-size: 1.8rem;
            }
            
            .badge-container {
                display: flex;
                gap: 10px;
                align-items: center;
                flex-wrap: wrap;
            }
            
            .badge {
                padding: 5px 15px;
                border-radius: 20px;
                font-size: 0.9rem;
                font-weight: bold;
                background: rgba(0,0,0,0.5);
                border: 1px solid;
            }
            
            .badge.nse { border-color: #00ff87; color: #00ff87; }
            .badge.nasdaq { border-color: #00f2fe; color: #00f2fe; }
            .badge.crypto { border-color: #b026ff; color: #b026ff; }
            
            .server-badge {
                background: rgba(0, 242, 254, 0.1);
                border: 1px solid var(--neon-blue);
                padding: 8px 20px;
                border-radius: 20px;
                color: var(--neon-blue);
                font-family: monospace;
                box-shadow: 0 0 10px var(--border-glow);
            }
            
            .controls {
                display: flex;
                gap: 10px;
                align-items: center;
                flex-wrap: wrap;
            }
            
            select, button {
                background: rgba(0,0,0,0.5);
                border: 1px solid var(--neon-purple);
                color: white;
                padding: 10px 20px;
                border-radius: 8px;
                font-family: 'Orbitron', sans-serif;
                cursor: pointer;
                font-size: 0.9rem;
            }
            
            select {
                min-width: 200px;
            }
            
            select option {
                background: var(--bg-deep);
                color: white;
            }
            
            button {
                background: linear-gradient(90deg, var(--neon-purple), var(--neon-blue));
                border: none;
                font-weight: bold;
                transition: all 0.3s ease;
            }
            
            button:hover {
                transform: translateY(-2px);
                box-shadow: 0 0 20px rgba(0,242,254,0.6);
            }
            
            .kpi-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 15px;
                margin-bottom: 20px;
            }
            
            .kpi-card {
                background: var(--panel-bg);
                border: 1px solid #1e3a8a;
                border-radius: 12px;
                padding: 20px;
                text-align: center;
                backdrop-filter: blur(10px);
            }
            
            .kpi-title {
                font-size: 1rem;
                color: #94a3b8;
                text-transform: uppercase;
                letter-spacing: 1px;
                margin-bottom: 10px;
            }
            
            .kpi-value {
                font-size: 2rem;
                font-weight: bold;
                font-family: 'Orbitron', sans-serif;
            }
            
            .c-green { color: var(--neon-green); }
            .c-blue { color: var(--neon-blue); }
            .c-purple { color: var(--neon-purple); }
            .c-red { color: var(--neon-red); }
            
            .dashboard-grid {
                display: grid;
                grid-template-columns: 2fr 1fr;
                gap: 20px;
                margin-bottom: 20px;
            }
            
            .dashboard-grid-4 {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 20px;
                margin-bottom: 20px;
            }
            
            .panel {
                background: var(--panel-bg);
                border: 1px solid #1e3a8a;
                border-radius: 12px;
                padding: 20px;
                box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
                position: relative;
            }
            
            .panel::before {
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 2px;
                background: linear-gradient(90deg, transparent, var(--neon-blue), transparent);
            }
            
            .panel-title {
                font-size: 1.2rem;
                margin-bottom: 20px;
                text-align: center;
                border-bottom: 1px dashed #1e3a8a;
                padding-bottom: 10px;
                color: var(--neon-blue);
            }
            
            .chart-box {
                height: 300px;
                position: relative;
                width: 100%;
            }
            
            .table-container {
                max-height: 400px;
                overflow-y: auto;
                border-radius: 8px;
            }
            
            table {
                width: 100%;
                border-collapse: collapse;
            }
            
            th {
                background: rgba(0, 242, 254, 0.1);
                padding: 12px;
                text-align: left;
                font-weight: bold;
                color: var(--neon-blue);
                position: sticky;
                top: 0;
                backdrop-filter: blur(10px);
            }
            
            td {
                padding: 10px 12px;
                border-bottom: 1px solid rgba(255,255,255,0.1);
            }
            
            tr:hover {
                background: rgba(255,255,255,0.05);
            }
            
            .signal-buy {
                color: var(--neon-green);
                font-weight: bold;
            }
            
            .signal-sell {
                color: var(--neon-red);
                font-weight: bold;
            }
            
            .signal-hold {
                color: #94a3b8;
            }
            
            .positive {
                color: var(--neon-green);
            }
            
            .negative {
                color: var(--neon-red);
            }
            
            .export-btn {
                background: linear-gradient(90deg, #00ff87, #00f2fe);
                color: black;
                border: none;
                padding: 8px 15px;
                border-radius: 5px;
                cursor: pointer;
                font-weight: bold;
            }
            
            .news-container {
                max-height: 300px;
                overflow-y: auto;
            }
            
            .news-item {
                padding: 10px;
                border-bottom: 1px solid rgba(255,255,255,0.1);
            }
            
            .news-title {
                color: var(--neon-blue);
                text-decoration: none;
                font-size: 0.9rem;
            }
            
            .news-title:hover {
                text-decoration: underline;
            }
            
            .news-source {
                font-size: 0.8rem;
                color: #94a3b8;
                margin-top: 5px;
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
                <div class="badge-container" style="margin-top: 10px;">
                    <span class="badge nse">🇮🇳 NSE India</span>
                    <span class="badge nasdaq">🇺🇸 NASDAQ US</span>
                    <span class="badge crypto">₿ Crypto</span>
                </div>
            </div>
            <div class="controls">
                <select id="exchange-filter">
                    <option value="all">All Exchanges</option>
                    <option value="NSE (India)">🇮🇳 NSE India</option>
                    <option value="NASDAQ (US)">🇺🇸 NASDAQ US</option>
                    <option value="Crypto">₿ Crypto</option>
                </select>
                <select id="ticker-input">
                    <option value="RELIANCE.NS">RELIANCE (NSE)</option>
                    <option value="TCS.NS">TCS (NSE)</option>
                    <option value="HDFCBANK.NS">HDFC Bank (NSE)</option>
                    <option value="INFY.NS">INFOSYS (NSE)</option>
                    <option value="WIPRO.NS">WIPRO (NSE)</option>
                    <option value="TATAMOTORS.NS">TATA MOTORS (NSE)</option>
                    <option value="SBIN.NS">SBI (NSE)</option>
                    <option value="AAPL">APPLE (NASDAQ)</option>
                    <option value="GOOGL">GOOGLE (NASDAQ)</option>
                    <option value="MSFT">MICROSOFT (NASDAQ)</option>
                    <option value="TSLA">TESLA (NASDAQ)</option>
                    <option value="BTC-USD">BITCOIN (CRYPTO)</option>
                    <option value="ETH-USD">ETHEREUM (CRYPTO)</option>
                </select>
                <button onclick="addStock()" id="add-btn">➕ ADD ASSET</button>
                <button onclick="exportCSV()" class="export-btn">📥 EXPORT CSV</button>
            </div>
            <div class="server-badge">
                <span id="server-id">CONNECTING...</span> | v<span id="version">2.0</span>
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
                <div class="kpi-value" id="kpi-us">0</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Crypto</div>
                <div class="kpi-value c-green" id="kpi-crypto">0</div>
            </div>
        </div>

        <div class="dashboard-grid">
            <div class="panel">
                <div class="panel-title">📈 Live Portfolio Trajectory (7 Days)</div>
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
                            <th>SMA 20</th>
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
                            fill: true
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false }
                        },
                        scales: {
                            x: { grid: { color: 'rgba(255,255,255,0.05)' } },
                            y: { grid: { color: 'rgba(255,255,255,0.05)' } }
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
                            borderWidth: 0
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { position: 'bottom', labels: { color: '#e2e8f0' } }
                        }
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
                            backgroundColor: ['#00ff87', '#ff3366', '#64748b']
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        cutout: '60%',
                        plugins: {
                            legend: { position: 'bottom', labels: { color: '#e2e8f0' } }
                        }
                    }
                });
            }

            // Add stock function
            function addStock() {
                const ticker = document.getElementById('ticker-input').value;
                const btn = document.getElementById('add-btn');
                
                btn.innerText = "FETCHING...";
                fetch('/api/add_stock', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ticker: ticker })
                })
                .then(r => r.json())
                .then(d => {
                    btn.innerText = "➕ ADD ASSET";
                    if(d.error) alert("Error: " + d.error);
                    else {
                        fetchData();
                        loadNews(ticker.split('.')[0]);
                    }
                })
                .catch(e => {
                    btn.innerText = "➕ ADD ASSET";
                    alert("Network error.");
                });
            }

            // Export CSV
            function exportCSV() {
                window.location.href = '/api/export/csv';
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
                                    <a href="${item.link}" target="_blank" class="news-title">${item.title}</a>
                                    <div class="news-source">${item.source} • ${new Date(item.published).toLocaleString()}</div>
                                </div>
                            `).join('');
                        } else {
                            container.innerHTML = '<div class="news-item">No news available</div>';
                        }
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
                    document.getElementById('kpi-assets').innerText = d.stock_data.length;
                    
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
                        <tr onclick="loadHistory('${s.ticker}'); loadNews('${s.ticker}')" style="cursor: pointer;">
                            <td>${s.ticker}</td>
                            <td class="${s.change > 0 ? 'positive' : s.change < 0 ? 'negative' : ''}">
                                $${s.exchange === 'NASDAQ (US)' ? '' : '₹'}${s.price}
                            </td>
                            <td class="${s.change_percent > 0 ? 'positive' : s.change_percent < 0 ? 'negative' : ''}">
                                ${s.change_percent > 0 ? '+' : ''}${s.change_percent}%
                            </td>
                            <td class="signal-${s.signal.toLowerCase()}">${s.signal}</td>
                        </tr>
                    `).join('');
                    
                    // Update RSI table
                    const rsiBody = document.getElementById('rsi-body');
                    rsiBody.innerHTML = d.stock_data.map(s => `
                        <tr>
                            <td>${s.ticker}</td>
                            <td>${s.rsi || 50}</td>
                            <td class="${s.rsi < 30 ? 'positive' : s.rsi > 70 ? 'negative' : ''}">
                                ${s.rsi < 30 ? 'Oversold' : s.rsi > 70 ? 'Overbought' : 'Neutral'}
                            </td>
                        </tr>
                    `).join('');
                    
                    // Update signal chart
                    const signals = d.stock_data.reduce((acc, s) => {
                        acc[s.signal] = (acc[s.signal] || 0) + 1;
                        return acc;
                    }, {});
                    signalChart.data.datasets[0].data = [
                        signals['BUY'] || 0,
                        signals['SELL'] || 0,
                        signals['HOLD'] || 0
                    ];
                    signalChart.update();
                    
                    // Update detailed table
                    const detailedBody = document.getElementById('detailed-body');
                    detailedBody.innerHTML = d.stock_data.map(s => `
                        <tr>
                            <td>${s.ticker}</td>
                            <td>${s.exchange || 'Unknown'}</td>
                            <td class="${s.change > 0 ? 'positive' : s.change < 0 ? 'negative' : ''}">
                                ${s.exchange === 'NASDAQ (US)' ? '$' : '₹'}${s.price}
                            </td>
                            <td class="${s.change > 0 ? 'positive' : s.change < 0 ? 'negative' : ''}">
                                ${s.change > 0 ? '+' : ''}${s.change || 0}
                            </td>
                            <td class="${s.change_percent > 0 ? 'positive' : s.change_percent < 0 ? 'negative' : ''}">
                                ${s.change_percent > 0 ? '+' : ''}${s.change_percent || 0}%
                            </td>
                            <td class="signal-${s.signal.toLowerCase()}">${s.signal}</td>
                            <td>${s.rsi || 50}</td>
                            <td>${s.moving_averages?.sma_20 || 'N/A'}</td>
                            <td>${new Date(s.last_updated).toLocaleTimeString()}</td>
                        </tr>
                    `).join('');
                });
            }

            // Filter by exchange
            document.getElementById('exchange-filter').addEventListener('change', function(e) {
                const filter = e.target.value;
                const rows = document.querySelectorAll('#prices-body tr, #detailed-body tr');
                
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

            // Load version
            fetch('/api/version')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('version').innerText = data.version;
                });

            // Initialize
            initCharts();
            fetchData();
            setInterval(fetchData, 60000); // Update every minute
            loadHistory('RELIANCE');
            loadNews('RELIANCE');
        </script>
    </body>
    </html>'''
    
    return html

if __name__ == '__main__':
    # Wait for CouchDB to be ready
    time.sleep(5)
    
    # Setup database
    setup_db()
    
    # Start price update thread
    price_thread = threading.Thread(target=update_live_prices, daemon=True)
    price_thread.start()
    
    # Start Flask app
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('DEBUG', 'False').lower() == 'true'
    
    app.run(host='0.0.0.0', port=port, debug=debug)