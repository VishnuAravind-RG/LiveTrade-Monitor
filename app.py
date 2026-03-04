# -*- coding: utf-8 -*-
# app.py - Complete Multi-User Quantum Trading Terminal
from flask import Flask, jsonify, request, Response, redirect, url_for, render_template_string
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
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
import pytz

app = Flask(__name__)
app.secret_key = os.urandom(24)  # In production, set a fixed secret key

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'

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

# State dictionary for CouchDB availability
state = {
    'couchdb_available': False
}

# In-memory cache for news
news_cache = {}
news_cache_time = {}
CACHE_DURATION = 1800  # 30 minutes

# -------------------------------------------------------------------
# CouchDB connection check
# -------------------------------------------------------------------
def check_couchdb_connection():
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

check_couchdb_connection()

# In-memory fallback storage
memory_stock_data = {}
memory_lock = threading.Lock()

# -------------------------------------------------------------------
# User model (stored in CouchDB)
# -------------------------------------------------------------------
class User(UserMixin):
    def __init__(self, user_id, username, portfolio=None, password_hash=None):
        self.id = user_id
        self.username = username
        self.portfolio = portfolio or {}
        self.password_hash = password_hash

    @staticmethod
    def get(user_id):
        if state['couchdb_available']:
            try:
                resp = requests.get(COUCHDB_URL + "users/" + user_id, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    return User(data['_id'], data['username'],
                                 data.get('portfolio', {}),
                                 data.get('password_hash'))
            except:
                pass
        # Fallback to in-memory
        with memory_users_lock:
            if user_id in memory_users:
                return memory_users[user_id]
        return None

    def save(self):
        doc = {
            "_id": self.id,
            "username": self.username,
            "portfolio": self.portfolio,
            "password_hash": self.password_hash
        }
        if state['couchdb_available']:
            try:
                # Check if exists to get _rev
                resp = requests.get(COUCHDB_URL + "users/" + self.id, timeout=5)
                if resp.status_code == 200:
                    doc['_rev'] = resp.json()['_rev']
                requests.put(COUCHDB_URL + "users/" + self.id, json=doc, timeout=5)
            except Exception as e:
                logger.error(f"Failed to save user to CouchDB: {e}")
                with memory_users_lock:
                    memory_users[self.id] = self
        else:
            with memory_users_lock:
                memory_users[self.id] = self

# In-memory user store for fallback
memory_users = {}
memory_users_lock = threading.Lock()

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

# -------------------------------------------------------------------
# Database setup (ensure stocks and users databases exist)
# -------------------------------------------------------------------
def setup_db():
    if state['couchdb_available']:
        # Create stocks database if not exists
        try:
            requests.put(COUCHDB_URL + DB_NAME, timeout=5)
        except:
            pass
        # Create users database
        try:
            requests.put(COUCHDB_URL + "users", timeout=5)
        except:
            pass

        # Create design document for stocks (MapReduce)
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
        try:
            existing = requests.get(COUCHDB_URL + DB_NAME + "/_design/analytics", timeout=5)
            if existing.status_code == 200:
                design_doc['_rev'] = existing.json()['_rev']
            requests.post(COUCHDB_URL + DB_NAME, json=design_doc, timeout=5)
        except:
            pass

    # Initialize stock data (either from CouchDB or memory)
    initialize_stock_data()

def initialize_stock_data():
    """Populate initial stock data if not present."""
    # Try to fetch existing stocks
    existing_stocks = {}
    if state['couchdb_available']:
        try:
            resp = requests.get(COUCHDB_URL + DB_NAME + "/_all_docs?include_docs=true", timeout=5)
            rows = resp.json().get('rows', [])
            for row in rows:
                if not row['id'].startswith('_design'):
                    doc = row['doc']
                    existing_stocks[doc['ticker']] = doc
        except:
            pass

    # For each ticker, if not present, create a new doc
    for name, symbol in TICKERS.items():
        if name in existing_stocks:
            continue  # already exists
        # Determine exchange
        if ".NS" in symbol:
            exchange = "NSE (India)"
        elif "-USD" in symbol:
            exchange = "Crypto"
        else:
            exchange = "NASDAQ (US)"
        # Fetch real data or generate mock
        try:
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
        except:
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

        if state['couchdb_available']:
            try:
                # Check if exists (though we already checked)
                existing = requests.get(COUCHDB_URL + DB_NAME + "/" + name, timeout=5)
                if existing.status_code == 200:
                    doc['_rev'] = existing.json()['_rev']
                requests.put(COUCHDB_URL + DB_NAME + "/" + name, json=doc, timeout=5)
            except:
                with memory_lock:
                    memory_stock_data[name] = doc
        else:
            with memory_lock:
                memory_stock_data[name] = doc

    logger.info("✅ Stock data initialized")

# -------------------------------------------------------------------
# Technical indicator functions
# -------------------------------------------------------------------
def calculate_rsi(prices, period=14):
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
    if len(prices) < 50:
        return {"sma_20": 0, "sma_50": 0, "ema_20": 0}
    df = pd.Series(prices)
    return {
        "sma_20": round(df.rolling(window=20).mean().iloc[-1], 2),
        "sma_50": round(df.rolling(window=50).mean().iloc[-1], 2),
        "ema_20": round(df.ewm(span=20).mean().iloc[-1], 2)
    }

def get_market_status():
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    if now.weekday() >= 5:
        return "Closed (Weekend)"
    hour = now.hour
    minute = now.minute
    current_minutes = hour * 60 + minute
    market_open = 9 * 60 + 15
    market_close = 15 * 60 + 30
    if market_open <= current_minutes <= market_close:
        return "Open"
    else:
        return "Closed"

# -------------------------------------------------------------------
# Background price updater
# -------------------------------------------------------------------
def update_live_prices():
    while True:
        time.sleep(60)
        try:
            if state['couchdb_available']:
                try:
                    resp = requests.get(COUCHDB_URL + DB_NAME + "/_all_docs?include_docs=true", timeout=5)
                    docs = [row['doc'] for row in resp.json().get('rows', []) if not row['id'].startswith('_design')]
                except:
                    docs = list(memory_stock_data.values())
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
                        if not hist_data.empty:
                            prices = hist_data['Close'].tolist()
                            rsi = calculate_rsi(prices)
                        else:
                            rsi = round(random.uniform(20, 80), 2)

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
                        # Simulated movement
                        self_update_simulated_data(doc)
                except Exception as e:
                    self_update_simulated_data(doc)

                updated_docs.append(doc)

            # Save updates
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
            time.sleep(5)

def self_update_simulated_data(doc):
    old_price = doc.get('price', 1000.0)
    if 'USD' in doc.get('symbol', ''):
        jitter = random.uniform(-50.0, 50.0)
    else:
        jitter = random.uniform(-5.0, 5.0)
    current_price = round(max(old_price + jitter, 0.1), 2)
    change = round(current_price - old_price, 2)
    change_percent = round((change / old_price) * 100, 2) if old_price != 0 else 0

    if current_price > old_price * 1.02:
        signal = "BUY"
    elif current_price < old_price * 0.98:
        signal = "SELL"
    else:
        signal = "HOLD"

    doc['price'] = current_price
    doc['change'] = change
    doc['change_percent'] = change_percent
    doc['signal'] = signal
    doc['rsi'] = round(random.uniform(20, 80), 2)
    doc['last_updated'] = datetime.now().isoformat()

# -------------------------------------------------------------------
# Request counter
# -------------------------------------------------------------------
@app.before_request
def before_request():
    global request_count
    with request_lock:
        request_count += 1

# -------------------------------------------------------------------
# Authentication routes
# -------------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            return render_template_string(LOGIN_PAGE, error="Username and password required")

        # Find user
        user = None
        if state['couchdb_available']:
            try:
                resp = requests.get(COUCHDB_URL + "users/_all_docs?include_docs=true", timeout=5)
                rows = resp.json().get('rows', [])
                for row in rows:
                    doc = row.get('doc', {})
                    if doc.get('username') == username:
                        if check_password_hash(doc.get('password_hash', ''), password):
                            user = User(doc['_id'], username, doc.get('portfolio', {}), doc.get('password_hash'))
                        break
            except:
                pass
        else:
            with memory_users_lock:
                for uid, u in memory_users.items():
                    if u.username == username and check_password_hash(u.password_hash, password):
                        user = u
                        break

        if user:
            login_user(user)
            return redirect('/dashboard')
        else:
            return render_template_string(LOGIN_PAGE, error="Invalid credentials")
    return render_template_string(LOGIN_PAGE)

@app.route('/register', methods=['GET', 'POST'])
def register_page():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            return render_template_string(REGISTER_PAGE, error="Username and password required")

        # Check if username exists
        exists = False
        if state['couchdb_available']:
            try:
                resp = requests.get(COUCHDB_URL + "users/_all_docs?include_docs=true", timeout=5)
                rows = resp.json().get('rows', [])
                for row in rows:
                    if row.get('doc', {}).get('username') == username:
                        exists = True
                        break
            except:
                pass
        else:
            with memory_users_lock:
                for u in memory_users.values():
                    if u.username == username:
                        exists = True
                        break

        if exists:
            return render_template_string(REGISTER_PAGE, error="Username already taken")

        import uuid
        user_id = str(uuid.uuid4())
        new_user = User(user_id, username, {}, generate_password_hash(password))
        new_user.save()
        login_user(new_user)
        return redirect('/dashboard')
    return render_template_string(REGISTER_PAGE)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/')

# -------------------------------------------------------------------
# Protected API endpoints for trading
# -------------------------------------------------------------------
@app.route('/api/portfolio')
@login_required
def get_portfolio():
    holdings = []
    total_value = 0.0
    for symbol, qty in current_user.portfolio.items():
        price = 0.0
        if state['couchdb_available']:
            try:
                resp = requests.get(COUCHDB_URL + DB_NAME + "/" + symbol, timeout=5)
                if resp.status_code == 200:
                    price = resp.json().get('price', 0)
            except:
                pass
        else:
            with memory_lock:
                if symbol in memory_stock_data:
                    price = memory_stock_data[symbol].get('price', 0)
        value = price * qty
        total_value += value
        holdings.append({
            'symbol': symbol,
            'quantity': qty,
            'price': price,
            'value': value
        })
    return jsonify({'holdings': holdings, 'total_value': total_value})

@app.route('/api/buy', methods=['POST'])
@login_required
def buy_stock():
    data = request.json
    symbol = data.get('symbol', '').upper().strip()
    quantity = int(data.get('quantity', 0))
    if quantity <= 0:
        return jsonify({'error': 'Invalid quantity'}), 400

    # Get current price
    price = 0.0
    if state['couchdb_available']:
        try:
            resp = requests.get(COUCHDB_URL + DB_NAME + "/" + symbol, timeout=5)
            if resp.status_code == 200:
                price = resp.json().get('price', 0)
        except:
            pass
    else:
        with memory_lock:
            if symbol in memory_stock_data:
                price = memory_stock_data[symbol].get('price', 0)

    if price <= 0:
        return jsonify({'error': 'Stock not found'}), 404

    current_user.portfolio[symbol] = current_user.portfolio.get(symbol, 0) + quantity
    current_user.save()
    return jsonify({'success': True, 'symbol': symbol, 'quantity': quantity, 'price': price})

@app.route('/api/sell', methods=['POST'])
@login_required
def sell_stock():
    data = request.json
    symbol = data.get('symbol', '').upper().strip()
    quantity = int(data.get('quantity', 0))
    if quantity <= 0:
        return jsonify({'error': 'Invalid quantity'}), 400
    if current_user.portfolio.get(symbol, 0) < quantity:
        return jsonify({'error': 'Insufficient shares'}), 400

    # Get current price
    price = 0.0
    if state['couchdb_available']:
        try:
            resp = requests.get(COUCHDB_URL + DB_NAME + "/" + symbol, timeout=5)
            if resp.status_code == 200:
                price = resp.json().get('price', 0)
        except:
            pass
    else:
        with memory_lock:
            if symbol in memory_stock_data:
                price = memory_stock_data[symbol].get('price', 0)

    if price <= 0:
        return jsonify({'error': 'Stock not found'}), 404

    current_user.portfolio[symbol] -= quantity
    if current_user.portfolio[symbol] == 0:
        del current_user.portfolio[symbol]
    current_user.save()
    return jsonify({'success': True, 'symbol': symbol, 'quantity': quantity, 'price': price})

# -------------------------------------------------------------------
# Existing stock data endpoints (unchanged, but ensure they work with CouchDB/memory)
# -------------------------------------------------------------------
@app.route('/api/data')
def get_data():
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
    try:
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

        stock = yf.Ticker(symbol)
        hist = stock.history(period="7d", interval="1h")
        if hist.empty:
            dates = [(datetime.now() - timedelta(hours=i)).strftime('%Y-%m-%d %H:%M') for i in range(50, 0, -1)]
            base_price = 1000
            prices = [base_price + random.uniform(-50, 50) for _ in range(50)]
            return jsonify({
                "ticker": ticker,
                "dates": dates,
                "prices": [round(p, 2) for p in prices]
            })
        dates = hist.index.strftime('%Y-%m-%d %H:%M').tolist()
        prices = hist['Close'].round(2).tolist()
        return jsonify({
            "ticker": ticker,
            "dates": dates,
            "prices": prices
        })
    except Exception as e:
        logger.error(f"History error: {e}")
        dates = [(datetime.now() - timedelta(hours=i)).strftime('%Y-%m-%d %H:%M') for i in range(50, 0, -1)]
        prices = [1000 + random.uniform(-50, 50) for _ in range(50)]
        return jsonify({
            "ticker": ticker,
            "dates": dates,
            "prices": [round(p, 2) for p in prices]
        })

@app.route('/api/news/<ticker>')
def get_news(ticker):
    try:
        if ticker in news_cache and (datetime.now() - news_cache_time.get(ticker, datetime.min)).seconds < CACHE_DURATION:
            return jsonify({"ticker": ticker, "news": news_cache[ticker]})

        clean_ticker = ticker.replace('.NS', '').replace('-USD', '')
        news_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(clean_ticker)}+stock+OR+{urllib.parse.quote(clean_ticker)}+share&hl=en-IN&gl=IN&ceid=IN:en"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        feed = feedparser.parse(news_url)
        news_items = []
        for entry in feed.entries[:8]:
            news_items.append({
                'title': entry.title,
                'link': entry.link,
                'published': entry.published,
                'source': entry.source.title if hasattr(entry, 'source') else 'Google News'
            })
        if not news_items:
            news_items = [
                {'title': f'{clean_ticker} shows strong momentum in trading', 'link': '#', 'published': datetime.now().strftime('%a, %d %b %Y %H:%M:%S GMT'), 'source': 'Market Watch'},
                {'title': f'Analysts review {clean_ticker} price target', 'link': '#', 'published': (datetime.now() - timedelta(hours=2)).strftime('%a, %d %b %Y %H:%M:%S GMT'), 'source': 'Bloomberg'},
                {'title': f'{clean_ticker} trading volume spikes', 'link': '#', 'published': (datetime.now() - timedelta(hours=5)).strftime('%a, %d %b %Y %H:%M:%S GMT'), 'source': 'Reuters'}
            ]
        news_cache[ticker] = news_items
        news_cache_time[ticker] = datetime.now()
        return jsonify({"ticker": ticker, "news": news_items})
    except Exception as e:
        logger.error(f"News error: {e}")
        return jsonify({"ticker": ticker, "news": [{'title': f'{ticker} market update', 'link': '#', 'published': datetime.now().strftime('%a, %d %b %Y %H:%M:%S GMT'), 'source': 'Financial Times'}]})

@app.route('/api/export/csv')
def export_csv():
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

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Ticker', 'Exchange', 'Price', 'Change', 'Change %', 'Signal', 'RSI', 'Volume', 'Last Updated'])
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
    return jsonify({
        "status": get_market_status(),
        "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "recommendation": "Try crypto on weekends!" if get_market_status() == "Closed" else "Markets are open"
    })

@app.route('/api/version')
def version():
    try:
        import subprocess
        git_hash = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode('ascii').strip()[:8]
    except:
        git_hash = "unknown"
    return jsonify({
        'version': '4.0.0',
        'git_commit': git_hash,
        'deployed_at': datetime.now().isoformat(),
        'environment': ENVIRONMENT,
        'uptime': round(time.time() - start_time, 2),
        'total_requests': request_count,
        'database': 'CouchDB' if state['couchdb_available'] else 'In-Memory'
    })

@app.route('/api/health')
def health_check():
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

# -------------------------------------------------------------------
# Page routes
# -------------------------------------------------------------------
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect('/dashboard')
    return render_template_string(LANDING_HTML)

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template_string(DASHBOARD_HTML, username=current_user.username)

# -------------------------------------------------------------------
# HTML templates (embedded for simplicity)
# -------------------------------------------------------------------
LOGIN_PAGE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Login - Quantum Trading</title>
    <style>
        body { background: #0a0f1c; color: #e2e8f0; font-family: 'Rajdhani', sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin:0; }
        .login-box { background: rgba(18,25,45,0.9); padding: 40px; border-radius: 16px; border: 1px solid #00f2fe; box-shadow: 0 0 20px #00f2fe33; width: 350px; }
        h1 { color: #00f2fe; text-align: center; margin-bottom: 30px; }
        input { width: 100%; padding: 12px; margin: 10px 0; background: #0a0f1c; border: 1px solid #b026ff; color: white; border-radius: 8px; box-sizing: border-box; }
        button { width: 100%; padding: 12px; background: linear-gradient(90deg, #b026ff, #00f2fe); border: none; color: white; font-weight: bold; border-radius: 8px; cursor: pointer; }
        .error { color: #ff3366; text-align: center; margin:10px 0; }
        a { color: #00ff87; text-decoration: none; }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>⚡ Login</h1>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="post">
            <input type="text" name="username" placeholder="Username" required>
            <input type="password" name="password" placeholder="Password" required>
            <button type="submit">Login</button>
        </form>
        <p style="text-align:center; margin-top:20px;">New user? <a href="/register">Register</a></p>
    </div>
</body>
</html>
'''

REGISTER_PAGE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Register - Quantum Trading</title>
    <style>
        body { background: #0a0f1c; color: #e2e8f0; font-family: 'Rajdhani', sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin:0; }
        .login-box { background: rgba(18,25,45,0.9); padding: 40px; border-radius: 16px; border: 1px solid #00f2fe; box-shadow: 0 0 20px #00f2fe33; width: 350px; }
        h1 { color: #00f2fe; text-align: center; margin-bottom: 30px; }
        input { width: 100%; padding: 12px; margin: 10px 0; background: #0a0f1c; border: 1px solid #b026ff; color: white; border-radius: 8px; box-sizing: border-box; }
        button { width: 100%; padding: 12px; background: linear-gradient(90deg, #b026ff, #00f2fe); border: none; color: white; font-weight: bold; border-radius: 8px; cursor: pointer; }
        .error { color: #ff3366; text-align: center; margin:10px 0; }
        a { color: #00ff87; text-decoration: none; }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>⚡ Register</h1>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="post">
            <input type="text" name="username" placeholder="Username" required>
            <input type="password" name="password" placeholder="Password" required>
            <button type="submit">Register</button>
        </form>
        <p style="text-align:center; margin-top:20px;">Already have account? <a href="/login">Login</a></p>
    </div>
</body>
</html>
'''

LANDING_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Quantum Trading Terminal</title>
    <style>
        body { background: #0a0f1c; color: #e2e8f0; font-family: 'Rajdhani', sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin:0; text-align:center; }
        .landing { max-width: 600px; }
        h1 { color: #00f2fe; font-size: 3rem; text-shadow: 0 0 20px #00f2fe; }
        p { font-size: 1.2rem; margin: 30px 0; }
        .btn { background: linear-gradient(90deg, #b026ff, #00f2fe); color: white; padding: 15px 40px; border-radius: 8px; text-decoration: none; font-weight: bold; margin: 0 10px; display: inline-block; }
        .btn-reg { background: #00ff87; color: #0a0f1c; }
    </style>
</head>
<body>
    <div class="landing">
        <h1>⚡ QUANTUM TRADING TERMINAL</h1>
        <p>Your personal algorithmic trading platform</p>
        <div>
            <a href="/login" class="btn">Login</a>
            <a href="/register" class="btn btn-reg">Register</a>
        </div>
    </div>
</body>
</html>
'''

DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quantum Trading - Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
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
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            background-color: var(--bg-deep);
            color: var(--text-main);
            font-family: 'Rajdhani', sans-serif;
            padding: 20px;
            background-image: radial-gradient(circle at 10% 20%, rgba(0,242,254,0.05) 0%, transparent 30%),
                              radial-gradient(circle at 90% 80%, rgba(176,38,255,0.05) 0%, transparent 30%);
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
        .badge.nse { border-color: #00ff87; color: #00ff87; }
        .badge.nasdaq { border-color: #00f2fe; color: #00f2fe; }
        .badge.crypto { border-color: #b026ff; color: #b026ff; }
        .server-badge {
            background: rgba(0,242,254,0.15);
            border: 1px solid var(--neon-blue);
            padding: 8px 20px;
            border-radius: 25px;
            color: var(--neon-blue);
            font-family: monospace;
            box-shadow: 0 0 20px var(--border-glow);
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
        button {
            background: linear-gradient(135deg, var(--neon-purple), var(--neon-blue));
            border: none;
            font-weight: bold;
        }
        button:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(0,242,254,0.6);
        }
        .logout {
            background: var(--neon-red);
            padding: 8px 20px;
            border-radius: 8px;
            text-decoration: none;
            color: white;
            font-weight: bold;
        }
        .portfolio-card {
            background: var(--panel-bg);
            border: 1px solid var(--neon-blue);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: var(--card-shadow);
        }
        .portfolio-card h2 { color: var(--neon-green); margin-bottom: 15px; }
        .holdings-table {
            width: 100%;
            border-collapse: collapse;
        }
        .holdings-table th {
            background: linear-gradient(135deg, rgba(0,242,254,0.2), rgba(176,38,255,0.2));
            padding: 14px 12px;
            text-align: left;
            color: var(--neon-blue);
        }
        .holdings-table td {
            padding: 12px 12px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        .buy-sell {
            display: flex;
            gap: 10px;
            margin-top: 20px;
            flex-wrap: wrap;
        }
        .buy-sell input, .buy-sell select {
            flex: 1;
            min-width: 150px;
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
            box-shadow: var(--card-shadow);
        }
        .kpi-value {
            font-size: 2.2rem;
            font-weight: bold;
            font-family: 'Orbitron', sans-serif;
        }
        .c-green { color: var(--neon-green); }
        .c-blue { color: var(--neon-blue); }
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
        }
        .panel-title {
            font-size: 1.2rem;
            margin-bottom: 20px;
            text-align: center;
            border-bottom: 1px dashed #1e3a8a;
            padding-bottom: 12px;
            color: var(--neon-blue);
        }
        .chart-box { height: 300px; position: relative; width: 100%; }
        .table-container { max-height: 400px; overflow-y: auto; }
        table { width: 100%; border-collapse: collapse; }
        th { background: linear-gradient(135deg, rgba(0,242,254,0.2), rgba(176,38,255,0.2)); padding: 12px; text-align: left; color: var(--neon-blue); position: sticky; top:0; }
        td { padding: 10px; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .signal-buy { color: var(--neon-green); }
        .signal-sell { color: var(--neon-red); }
        .positive { color: var(--neon-green); }
        .negative { color: var(--neon-red); }
        .news-container { max-height: 300px; overflow-y: auto; }
        .news-item { padding: 10px; border-bottom: 1px solid rgba(255,255,255,0.1); }
        .news-title { color: var(--neon-blue); text-decoration: none; }
        .news-source { font-size: 0.8rem; color: #94a3b8; }
        .market-status { display: inline-block; padding: 5px 15px; border-radius: 20px; font-size: 0.9rem; font-weight: bold; background: rgba(0,0,0,0.5); }
        .status-open { color: #00ff87; border: 1px solid #00ff87; }
        .status-closed { color: #ff3366; border: 1px solid #ff3366; }
    </style>
</head>
<body>
    <div class="top-bar">
        <div>
            <h1>⚡ QUANTUM TRADING TERMINAL</h1>
            <div class="badge-container" style="margin-top:12px;">
                <span class="badge nse">🇮🇳 NSE India</span>
                <span class="badge nasdaq">🇺🇸 NASDAQ US</span>
                <span class="badge crypto">₿ Crypto 24/7</span>
                <span class="market-status" id="market-status">Loading...</span>
            </div>
        </div>
        <div style="display:flex; align-items:center; gap:20px;">
            <span style="color:#00ff87;">Welcome, {{ username }}</span>
            <a href="/logout" class="logout">Logout</a>
        </div>
    </div>

    <!-- Portfolio Section -->
    <div class="portfolio-card">
        <h2>📊 Your Portfolio</h2>
        <div style="font-size:2.5rem; color:var(--neon-green); margin-bottom:20px;" id="portfolio-total">₹0</div>
        <table class="holdings-table" id="holdings-table">
            <thead><tr><th>Symbol</th><th>Quantity</th><th>Price</th><th>Value</th></tr></thead>
            <tbody id="holdings-body"></tbody>
        </table>
    </div>

    <!-- Buy/Sell Interface -->
    <div class="portfolio-card">
        <h2>💰 Trade Stocks</h2>
        <div class="buy-sell">
            <select id="trade-symbol">
                <!-- populated via JS -->
            </select>
            <input type="number" id="trade-quantity" placeholder="Quantity" min="1">
            <button onclick="buyStock()">BUY</button>
            <button onclick="sellStock()">SELL</button>
        </div>
    </div>

    <!-- KPI Cards (same as before) -->
    <div class="kpi-grid">
        <div class="kpi-card"><div class="kpi-title">Total Portfolio (Market)</div><div class="kpi-value c-green" id="kpi-total">₹0</div></div>
        <div class="kpi-card"><div class="kpi-title">Active Assets</div><div class="kpi-value c-blue" id="kpi-assets">0</div></div>
        <div class="kpi-card"><div class="kpi-title">NSE Stocks</div><div class="kpi-value c-purple" id="kpi-nse">0</div></div>
        <div class="kpi-card"><div class="kpi-title">US Stocks</div><div class="kpi-value c-yellow" id="kpi-us">0</div></div>
        <div class="kpi-card"><div class="kpi-title">Crypto</div><div class="kpi-value c-green" id="kpi-crypto">0</div></div>
        <div class="kpi-card"><div class="kpi-title">Market Status</div><div class="kpi-value" id="kpi-market">Checking...</div></div>
    </div>

    <!-- Charts -->
    <div class="dashboard-grid">
        <div class="panel"><div class="panel-title">📈 Live Price History (7 Days)</div><div class="chart-box"><canvas id="historyChart"></canvas></div></div>
        <div class="panel"><div class="panel-title">📊 Exchange Distribution</div><div class="chart-box"><canvas id="exchangeChart"></canvas></div></div>
    </div>

    <!-- Tables and News -->
    <div class="dashboard-grid-4">
        <div class="panel"><div class="panel-title">💰 Live Prices</div><div class="table-container"><table id="prices-table"><thead><tr><th>Asset</th><th>Price</th><th>24h %</th><th>Signal</th></tr></thead><tbody id="prices-body"></tbody></table></div></div>
        <div class="panel"><div class="panel-title">📊 RSI Indicators</div><div class="table-container"><table id="rsi-table"><thead><tr><th>Asset</th><th>RSI</th><th>Status</th></tr></thead><tbody id="rsi-body"></tbody></table></div></div>
        <div class="panel"><div class="panel-title">🎯 Trading Signals</div><div class="chart-box"><canvas id="signalChart"></canvas></div></div>
        <div class="panel"><div class="panel-title">📰 Latest News</div><div class="news-container" id="news-container"><div class="news-item">Loading news...</div></div></div>
    </div>

    <!-- Detailed Portfolio Table -->
    <div class="panel"><div class="panel-title">📋 Detailed Market Data</div><div class="table-container"><table><thead><tr><th>Asset</th><th>Exchange</th><th>Price</th><th>Change</th><th>Change %</th><th>Signal</th><th>RSI</th><th>Volume</th><th>Last Updated</th></tr></thead><tbody id="detailed-body"></tbody></table></div></div>

    <script>
        let historyChart, exchangeChart, signalChart;

        function initCharts() {
            const ctxHistory = document.getElementById('historyChart').getContext('2d');
            historyChart = new Chart(ctxHistory, {
                type: 'line',
                data: { labels: [], datasets: [{ label: 'Price', data: [], borderColor: '#00ff87', backgroundColor: 'rgba(0,255,135,0.1)', tension: 0.4, fill: true }] },
                options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
            });
            const ctxExchange = document.getElementById('exchangeChart').getContext('2d');
            exchangeChart = new Chart(ctxExchange, {
                type: 'doughnut',
                data: { labels: ['NSE India', 'NASDAQ US', 'Crypto'], datasets: [{ data: [0,0,0], backgroundColor: ['#00ff87','#00f2fe','#b026ff'] }] },
                options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { color: '#e2e8f0' } } } }
            });
            const ctxSignal = document.getElementById('signalChart').getContext('2d');
            signalChart = new Chart(ctxSignal, {
                type: 'doughnut',
                data: { labels: ['BUY','SELL','HOLD'], datasets: [{ data: [0,0,0], backgroundColor: ['#00ff87','#ff3366','#64748b'] }] },
                options: { responsive: true, maintainAspectRatio: false, cutout: '65%', plugins: { legend: { position: 'bottom', labels: { color: '#e2e8f0' } } } }
            });
        }

        // Portfolio functions
        function loadPortfolio() {
            fetch('/api/portfolio')
                .then(r => r.json())
                .then(data => {
                    let total = data.total_value || 0;
                    document.getElementById('portfolio-total').innerText = '₹' + total.toFixed(2);
                    let html = '';
                    data.holdings.forEach(h => {
                        html += `<tr><td>${h.symbol}</td><td>${h.quantity}</td><td>₹${h.price.toFixed(2)}</td><td>₹${h.value.toFixed(2)}</td></tr>`;
                    });
                    document.getElementById('holdings-body').innerHTML = html;
                });
        }

        function buyStock() {
            const symbol = document.getElementById('trade-symbol').value;
            const quantity = document.getElementById('trade-quantity').value;
            if (!quantity || quantity <= 0) { alert('Enter valid quantity'); return; }
            fetch('/api/buy', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({symbol, quantity: parseInt(quantity)})
            })
            .then(r => r.json())
            .then(d => {
                if(d.success) {
                    alert(`Bought ${quantity} of ${symbol} at ₹${d.price}`);
                    loadPortfolio();
                } else alert('Error: ' + d.error);
            });
        }

        function sellStock() {
            const symbol = document.getElementById('trade-symbol').value;
            const quantity = document.getElementById('trade-quantity').value;
            if (!quantity || quantity <= 0) { alert('Enter valid quantity'); return; }
            fetch('/api/sell', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({symbol, quantity: parseInt(quantity)})
            })
            .then(r => r.json())
            .then(d => {
                if(d.success) {
                    alert(`Sold ${quantity} of ${symbol} at ₹${d.price}`);
                    loadPortfolio();
                } else alert('Error: ' + d.error);
            });
        }

        // Market data functions (same as before)
        function loadSymbols() {
            fetch('/api/data')
                .then(r => r.json())
                .then(d => {
                    let select = document.getElementById('trade-symbol');
                    select.innerHTML = '';
                    d.stock_data.forEach(s => {
                        let opt = document.createElement('option');
                        opt.value = s.ticker;
                        opt.text = `${s.ticker} - ${s.exchange==='NASDAQ (US)'?'$':'₹'}${s.price}`;
                        select.appendChild(opt);
                    });
                });
        }

        function loadNews(ticker) {
            fetch(`/api/news/${ticker}`).then(r=>r.json()).then(data=>{
                const container = document.getElementById('news-container');
                if(data.news && data.news.length)
                    container.innerHTML = data.news.map(item=>`<div class="news-item"><a href="${item.link}" target="_blank" class="news-title">📰 ${item.title}</a><div class="news-source">${item.source} • ${new Date(item.published).toLocaleString()}</div></div>`).join('');
                else container.innerHTML = '<div class="news-item">No news available</div>';
            }).catch(()=>document.getElementById('news-container').innerHTML='<div class="news-item">News temporarily unavailable</div>');
        }

        function loadHistory(ticker) {
            fetch(`/api/history/${ticker}`).then(r=>r.json()).then(data=>{
                if(data.dates && data.prices) {
                    historyChart.data.labels = data.dates.slice(-50);
                    historyChart.data.datasets[0].data = data.prices.slice(-50);
                    historyChart.update();
                }
            });
        }

        function fetchData() {
            fetch('/api/data')
                .then(r => r.json())
                .then(d => {
                    document.getElementById('kpi-total').innerText = '₹' + d.mapreduce_total.toLocaleString('en-IN', {minimumFractionDigits:2});
                    document.getElementById('kpi-assets').innerText = d.total_stocks;
                    const marketStatus = d.market_status || 'Unknown';
                    const statusEl = document.getElementById('market-status');
                    statusEl.innerText = marketStatus;
                    statusEl.className = 'market-status ' + (marketStatus.includes('Open')?'status-open':'status-closed');
                    document.getElementById('kpi-market').innerHTML = marketStatus;
                    let nse=0, us=0, crypto=0;
                    d.stock_data.forEach(s=>{
                        if(s.exchange==='NSE (India)') nse++;
                        else if(s.exchange==='NASDAQ (US)') us++;
                        else if(s.exchange==='Crypto') crypto++;
                    });
                    document.getElementById('kpi-nse').innerText = nse;
                    document.getElementById('kpi-us').innerText = us;
                    document.getElementById('kpi-crypto').innerText = crypto;
                    exchangeChart.data.datasets[0].data = [nse, us, crypto];
                    exchangeChart.update();

                    document.getElementById('prices-body').innerHTML = d.stock_data.map(s=>`<tr onclick="loadHistory('${s.ticker}'); loadNews('${s.ticker}')"><td><strong>${s.ticker}</strong></td><td class="${s.change>0?'positive':s.change<0?'negative':''}">${s.exchange==='NASDAQ (US)'?'$':'₹'}${s.price.toLocaleString()}</td><td class="${s.change_percent>0?'positive':s.change_percent<0?'negative':''}">${s.change_percent>0?'▲':s.change_percent<0?'▼':''} ${Math.abs(s.change_percent).toFixed(2)}%</td><td class="signal-${s.signal.toLowerCase()}">${s.signal}</td></tr>`).join('');
                    document.getElementById('rsi-body').innerHTML = d.stock_data.map(s=>{ const rsi=s.rsi||50; return `<tr><td><strong>${s.ticker}</strong></td><td>${rsi}</td><td class="${rsi<30?'positive':rsi>70?'negative':''}">${rsi<30?'🟢 Oversold':rsi>70?'🔴 Overbought':'⚪ Neutral'}</td></tr>`; }).join('');
                    const signalMap = {};
                    d.signal_distribution.forEach(s=>signalMap[s.key]=s.value);
                    signalChart.data.datasets[0].data = [signalMap['BUY']||0, signalMap['SELL']||0, signalMap['HOLD']||0];
                    signalChart.update();
                    document.getElementById('detailed-body').innerHTML = d.stock_data.map(s=>`<tr><td><strong>${s.ticker}</strong></td><td>${s.exchange||'Unknown'}</td><td class="${s.change>0?'positive':s.change<0?'negative':''}">${s.exchange==='NASDAQ (US)'?'$':'₹'}${s.price.toLocaleString()}</td><td class="${s.change>0?'positive':s.change<0?'negative':''}">${s.change>0?'+':''}${s.change?.toFixed(2)||0}</td><td class="${s.change_percent>0?'positive':s.change_percent<0?'negative':''}">${s.change_percent>0?'▲':s.change_percent<0?'▼':''} ${Math.abs(s.change_percent||0).toFixed(2)}%</td><td class="signal-${(s.signal||'HOLD').toLowerCase()}">${s.signal||'HOLD'}</td><td>${s.rsi||50}</td><td>${(s.volume||0).toLocaleString()}</td><td>${new Date(s.last_updated).toLocaleTimeString()}</td></tr>`).join('');
                });
        }

        document.getElementById('exchange-filter')?.addEventListener('change', function(e){
            const filter = e.target.value;
            ['#prices-body tr', '#detailed-body tr', '#rsi-body tr'].forEach(selector=>{
                document.querySelectorAll(selector).forEach(row=>{
                    if(filter==='all') row.style.display='';
                    else {
                        const exchange = row.cells[1]?.innerText||'';
                        row.style.display = exchange.includes(filter) ? '' : 'none';
                    }
                });
            });
        });

        // Initialize
        initCharts();
        fetchData();
        loadPortfolio();
        loadSymbols();
        setInterval(() => {
            fetchData();
            loadPortfolio();
        }, 60000);
        loadHistory('RELIANCE');
        loadNews('RELIANCE');
    </script>
</body>
</html>
'''

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
if __name__ == '__main__':
    time.sleep(5)
    setup_db()
    threading.Thread(target=update_live_prices, daemon=True).start()
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)