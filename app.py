# app.py (Fixed version - No AWS dependencies)
from flask import Flask, jsonify, request
import socket
import requests
import time
import threading
import yfinance as yf
import random
import os
import logging
from datetime import datetime

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration from environment variables
COUCHDB_URL = os.getenv('COUCHDB_URL', "http://admin:password@couchdb:5984/")
DB_NAME = os.getenv('DB_NAME', "stocks")
ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')

TICKERS = {
    "RELIANCE": "RELIANCE.NS",
    "TCS": "TCS.NS",
    "HDFC": "HDFCBANK.NS"
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
                stock = yf.Ticker(symbol)
                hist = stock.history(period="1d")
                if not hist.empty:
                    price = round(hist['Close'].iloc[-1], 2)
                else:
                    price = 1000.0
            except Exception as e:
                logger.error(f"Error fetching {symbol}: {e}")
                price = 1000.0
                
            doc = {
                "_id": name,
                "ticker": name,
                "price": price,
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

def update_live_prices():
    """Update stock prices with realistic movements"""
    while True:
        time.sleep(2)
        try:
            # Fetch all documents
            resp = requests.get(COUCHDB_URL + DB_NAME + "/_all_docs?include_docs=true")
            docs = [row['doc'] for row in resp.json().get('rows', []) if not row['id'].startswith('_design')]
            
            updated_docs = []
            for doc in docs:
                old_price = doc.get('price', 1000.0)
                
                # Simulate realistic price movement
                jitter = random.uniform(-5.0, 5.0)
                current_price = round(max(old_price + jitter, 0.1), 2)
                
                # Generate trading signal
                if current_price > old_price * 1.01:  # 1% increase
                    signal = "BUY"
                elif current_price < old_price * 0.99:  # 1% decrease
                    signal = "SELL"
                else:
                    signal = "HOLD"
                    
                doc['price'] = current_price
                doc['signal'] = signal
                doc['last_updated'] = datetime.now().isoformat()
                updated_docs.append(doc)
                
            # Bulk update
            if updated_docs:
                response = requests.post(
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
        
    display_name = symbol.replace('.NS', '')
    
    try:
        # Fetch real stock data
        stock = yf.Ticker(symbol)
        hist = stock.history(period="1d")
        
        if hist.empty:
            return jsonify({"error": "Invalid Ticker or Market Closed"}), 400
            
        price = round(hist['Close'].iloc[-1], 2)
        
        # Create document
        new_doc = {
            "_id": display_name,
            "ticker": display_name,
            "price": price,
            "signal": "HOLD",
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
    """Get all stock data with MapReduce total"""
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
        
    except Exception as e:
        logger.error(f"Data fetch error: {e}")
        data = []
        total_value = 0.0
        signals = []
    
    return jsonify({
        "server_id": socket.gethostname(),
        "environment": ENVIRONMENT,
        "timestamp": datetime.now().isoformat(),
        "stock_data": data,
        "mapreduce_total": round(total_value, 2),
        "signal_distribution": signals
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
            "update_sequence": db_stats.get('update_seq', 0)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/')
def index():
    """Main dashboard"""
    html = '''<!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Quantum Trading Terminal</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Rajdhani:wght@500;700&display=swap');
            
            :root {
                --bg-deep: #050b14;
                --panel-bg: rgba(10, 19, 41, 0.8);
                --neon-blue: #00f2fe;
                --neon-green: #00ff87;
                --neon-purple: #b026ff;
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
            
            select {
                background: rgba(0,0,0,0.5);
                border: 1px solid var(--neon-purple);
                color: white;
                padding: 8px 15px;
                border-radius: 4px;
                font-family: monospace;
                outline: none;
                cursor: pointer;
            }
            
            select:focus {
                box-shadow: 0 0 10px rgba(176,38,255,0.4);
            }
            
            select option {
                background: var(--bg-deep);
                color: white;
            }
            
            button {
                background: linear-gradient(90deg, var(--neon-purple), var(--neon-blue));
                border: none;
                color: white;
                padding: 8px 20px;
                border-radius: 4px;
                font-family: 'Orbitron', sans-serif;
                font-weight: bold;
                cursor: pointer;
            }
            
            button:hover {
                box-shadow: 0 0 15px rgba(0,242,254,0.6);
            }
            
            .server-badge {
                background: rgba(0, 242, 254, 0.1);
                border: 1px solid var(--neon-blue);
                padding: 5px 15px;
                border-radius: 20px;
                color: var(--neon-blue);
                font-family: monospace;
                box-shadow: 0 0 10px var(--border-glow);
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
            
            .kpi-title {
                font-size: 1rem;
                color: #94a3b8;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            
            .kpi-value {
                font-size: 2rem;
                font-weight: bold;
                font-family: 'Orbitron', sans-serif;
                margin-top: 5px;
            }
            
            .c-green { color: var(--neon-green); text-shadow: 0 0 8px rgba(0,255,135,0.4); }
            .c-blue { color: var(--neon-blue); text-shadow: 0 0 8px rgba(0,242,254,0.4); }
            .c-purple { color: var(--neon-purple); text-shadow: 0 0 8px rgba(176,38,255,0.4); }
            
            .dashboard-grid {
                display: grid;
                grid-template-columns: 2fr 1fr;
                gap: 20px;
                margin-bottom: 20px;
            }
            
            .dashboard-grid-3 {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 20px;
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
            
            .chart-box {
                height: 250px;
                position: relative;
                width: 100%;
            }
            
            .chart-box.tall { height: 350px; }
            
            .panel-title {
                font-size: 1.2rem;
                color: var(--text-main);
                margin-bottom: 15px;
                text-align: center;
                border-bottom: 1px dashed #1e3a8a;
                padding-bottom: 5px;
            }
            
            .progress-container {
                display: flex;
                flex-direction: column;
                justify-content: flex-start;
                gap: 12px;
                height: 250px;
                overflow-y: auto;
                padding-right: 5px;
            }
            
            .progress-container::-webkit-scrollbar {
                width: 5px;
            }
            
            .progress-container::-webkit-scrollbar-thumb {
                background: var(--neon-blue);
                border-radius: 5px;
            }
            
            .prog-row {
                display: flex;
                align-items: center;
                justify-content: space-between;
                font-size: 1.1rem;
            }
            
            .prog-bar-bg {
                flex-grow: 1;
                height: 8px;
                background: rgba(255,255,255,0.1);
                margin: 0 15px;
                border-radius: 4px;
                overflow: hidden;
            }
            
            .prog-bar-fill {
                height: 100%;
                background: linear-gradient(90deg, var(--neon-blue), var(--neon-purple));
                width: 0%;
                transition: width 0.5s ease;
            }
            
            .prog-pct {
                width: 45px;
                text-align: right;
                color: var(--neon-purple);
                font-family: monospace;
                font-size: 1.2rem;
            }
        </style>
    </head>
    <body>
        <div class="top-bar">
            <h1>QUANTUM TRADING TERMINAL</h1>
            <div class="controls">
                <select id="ticker-input">
                    <option value="ZOMATO.NS">ZOMATO</option>
                    <option value="WIPRO.NS">WIPRO</option>
                    <option value="TATAMOTORS.NS">TATA MOTORS</option>
                    <option value="SBIN.NS">SBI</option>
                    <option value="INFY.NS">INFOSYS</option>
                    <option value="ITC.NS">ITC</option>
                    <option value="BHARTIARTL.NS">AIRTEL</option>
                    <option value="MRF.NS">MRF</option>
                </select>
                <button onclick="addStock()" id="add-btn">ADD ASSET</button>
            </div>
            <div class="server-badge">
                Instance: <span id="server-id">CONNECTING...</span>
            </div>
        </div>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-title">Portfolio Value</div>
                <div class="kpi-value c-green" id="kpi-total">₹0</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Market Volatility</div>
                <div class="kpi-value c-blue" id="kpi-vol">REALTIME</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Active Assets</div>
                <div class="kpi-value c-purple" id="kpi-assets">0</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Live Status</div>
                <div class="kpi-value c-green" id="kpi-status">ACTIVE</div>
            </div>
        </div>

        <div class="dashboard-grid">
            <div class="panel">
                <div class="panel-title">Live Portfolio Trajectory</div>
                <div class="chart-box tall">
                    <canvas id="comboChart"></canvas>
                </div>
            </div>
            <div class="panel">
                <div class="panel-title">Asset Distribution</div>
                <div class="chart-box tall">
                    <canvas id="polarChart"></canvas>
                </div>
            </div>
        </div>

        <div class="dashboard-grid-3">
            <div class="panel">
                <div class="panel-title">Live Prices</div>
                <div class="chart-box">
                    <canvas id="barChart"></canvas>
                </div>
            </div>
            <div class="panel">
                <div class="panel-title">Signal Distribution</div>
                <div class="chart-box">
                    <canvas id="donutChart"></canvas>
                </div>
            </div>
            <div class="panel">
                <div class="panel-title">Portfolio Weighting</div>
                <div class="progress-container" id="progress-box"></div>
            </div>
        </div>

        <script>
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
                btn.innerText = "ADD ASSET";
                if(d.error) alert("Error: " + d.error);
                else {
                    fetchData();
                }
            })
            .catch(e => {
                btn.innerText = "ADD ASSET";
                alert("Network error.");
            });
        }

        const chartOptions = {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 500 },
            plugins: {
                legend: { labels: { color: '#e2e8f0' } }
            }
        };

        // Initialize charts
        const ctxCombo = document.getElementById('comboChart').getContext('2d');
        const comboChart = new Chart(ctxCombo, {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'Average Price',
                    data: [],
                    borderColor: '#00ff87',
                    backgroundColor: 'rgba(0, 255, 135, 0.1)',
                    tension: 0.4,
                    fill: true
                }]
            },
            options: {
                ...chartOptions,
                scales: {
                    x: { grid: { color: 'rgba(255,255,255,0.05)' } },
                    y: { grid: { color: 'rgba(255,255,255,0.05)' } }
                }
            }
        });

        const ctxPolar = document.getElementById('polarChart').getContext('2d');
        const polarChart = new Chart(ctxPolar, {
            type: 'polarArea',
            data: {
                labels: [],
                datasets: [{
                    data: [],
                    backgroundColor: [
                        'rgba(0,242,254,0.6)',
                        'rgba(0,255,135,0.6)',
                        'rgba(176,38,255,0.6)',
                        'rgba(255,153,0,0.6)',
                        'rgba(255,51,102,0.6)'
                    ]
                }]
            },
            options: chartOptions
        });

        const ctxBar = document.getElementById('barChart').getContext('2d');
        const barChart = new Chart(ctxBar, {
            type: 'bar',
            data: {
                labels: [],
                datasets: [{
                    label: 'Price (₹)',
                    data: [],
                    backgroundColor: '#00f2fe'
                }]
            },
            options: {
                ...chartOptions,
                plugins: { legend: { display: false } }
            }
        });

        const ctxDonut = document.getElementById('donutChart').getContext('2d');
        const donutChart = new Chart(ctxDonut, {
            type: 'doughnut',
            data: {
                labels: ['BUY', 'SELL', 'HOLD'],
                datasets: [{
                    data: [0, 0, 0],
                    backgroundColor: ['#00ff87', '#ff3366', '#64748b']
                }]
            },
            options: {
                ...chartOptions,
                cutout: '70%'
            }
        });

        function fetchData() {
            fetch('/api/data')
            .then(r => r.json())
            .then(d => {
                document.getElementById('server-id').innerText = d.server_id;
                document.getElementById('kpi-total').innerText = '₹' + d.mapreduce_total.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
                document.getElementById('kpi-assets').innerText = d.stock_data.length;
                
                let buys = 0, sells = 0, holds = 0;
                let tickers = [], prices = [];

                if(d.stock_data.length > 0) {
                    d.stock_data.forEach(s => {
                        tickers.push(s.ticker);
                        prices.push(s.price);
                        if(s.signal === 'BUY') buys++;
                        else if(s.signal === 'SELL') sells++;
                        else holds++;
                    });
                }

                // Update charts
                comboChart.data.labels.push(new Date().toLocaleTimeString());
                comboChart.data.datasets[0].data.push(d.mapreduce_total);
                if (comboChart.data.labels.length > 15) {
                    comboChart.data.labels.shift();
                    comboChart.data.datasets[0].data.shift();
                }
                comboChart.update();

                polarChart.data.labels = tickers;
                polarChart.data.datasets[0].data = prices;
                polarChart.update();

                barChart.data.labels = tickers;
                barChart.data.datasets[0].data = prices;
                barChart.update();

                donutChart.data.datasets[0].data = [buys, sells, holds];
                donutChart.update();

                // Update progress bars
                const progBox = document.getElementById('progress-box');
                progBox.innerHTML = '';
                d.stock_data.forEach(s => {
                    let weight = d.mapreduce_total > 0 ? Math.round((s.price / d.mapreduce_total) * 100) : 0;
                    progBox.innerHTML += `
                        <div class="prog-row">
                            <span style="width: 70px; color: #94a3b8; font-size: 0.9rem;">${s.ticker}</span>
                            <div class="prog-bar-bg"><div class="prog-bar-fill" style="width: ${weight}%"></div></div>
                            <span class="prog-pct">${weight}%</span>
                        </div>
                    `;
                });
            });
        }

        setInterval(fetchData, 2000);
        fetchData();
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