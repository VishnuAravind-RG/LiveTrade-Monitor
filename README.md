# ⚡ Quantum Trading Terminal

A real-time algorithmic trading dashboard that tracks stocks from **NSE India**, **NASDAQ US**, and **Cryptocurrency markets** with live prices, technical indicators, and news integration.

---

# 🚀 Live Demo

🔗 https://remarkable-charm-production-eac4.up.railway.app/

---

# 📋 Table of Contents

- Features
- Tech Stack
- Architecture
- Installation
- Usage
- API Endpoints
- Deployment
- Screenshots
- Future Enhancements
- Contributors
- License

---

# ✨ Features

## 📊 Multi-Exchange Support

- 🇮🇳 **NSE India** – RELIANCE, TCS, HDFC, INFY and more  
- 🇺🇸 **NASDAQ US** – AAPL, GOOGL, MSFT, TSLA and more  
- ₿ **Cryptocurrency** – BTC, ETH, DOGE, BNB, SOL  

---

## 📈 Technical Analysis

- **RSI (Relative Strength Index)**  
  - Overbought (>70)  
  - Oversold (<30)

- **Price Movement Indicators**
  - ▲ Green – Price Up
  - ▼ Red – Price Down

- **Trading Signals**
  - BUY
  - SELL
  - HOLD

---

## 🔄 Real-Time Updates

- Auto refresh every **60 seconds**
- Manual refresh button
- Live data fetched from **Yahoo Finance**

---

## 📰 News Integration

- Latest news fetched via **Google News RSS**
- Cached for **30 minutes**
- Click any stock to view relevant news

---

## 📊 Interactive Charts

- **7-day price history** line chart
- **Exchange distribution** donut chart
- **Signal distribution** donut chart

---

## 📥 Data Export

Export stock data to **CSV** with one click.

Fields included:

- Ticker  
- Exchange  
- Price  
- Change %  
- Signal  
- RSI  
- Volume  
- Timestamp  

---

## 🗄️ Database Persistence

- Uses **CouchDB**
- Automatic fallback to **in-memory storage**
- Data persists across restarts

---

## ☁️ Cloud Deployment

- Hosted on **Railway.app**
- **GitHub Actions CI/CD**
- Auto deployment on every push

---

## 🛡️ Resilience Features

- Mock data fallback if APIs fail
- Timeout protection
- Full API error handling
- Market open / closed detection

---

# 🛠 Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Python Flask |
| Frontend | HTML5, CSS3, JavaScript |
| Charts | Chart.js |
| Database | CouchDB |
| APIs | Yahoo Finance, Google News RSS |
| Deployment | Railway.app |
| CI/CD | GitHub Actions |
| Containerization | Docker |
| Load Balancer | Nginx |

---

# 🏗 Architecture

```
+-------------------+
|   Browser Client  |
|    (Frontend)     |
+---------+---------+
          |
          v
+-------------------+
|   Nginx Reverse   |
|     Proxy / LB    |
+---------+---------+
          |
          v
+-------------------+
|     Flask API     |
|      Backend      |
+----+---------+----+
     |         |
     v         v
+--------+  +----------------+
| CouchDB|  | Yahoo Finance  |
|Database|  |      API       |
+--------+  +----------------+
```


---

## Data Flow

1. Frontend requests data via REST APIs  
2. Flask backend fetches from CouchDB or Yahoo Finance  
3. CouchDB stores persistent stock data  
4. Yahoo Finance provides live prices  
5. Google News RSS provides news updates  
6. Background thread updates prices every 60 seconds  

---

# 💻 Installation

## Prerequisites

- Python 3.9+
- Git
- Docker (optional)

---

## Local Setup

### 1. Clone the Repository

```bash
git clone https://github.com/VishnuAravind-RG/LiveTrade-Monitor.git
cd LiveTrade-Monitor
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the Application

```bash
python app.py
```

### 4. Open in Browser

```
http://localhost:5000
```

---

## 🐳 Docker Setup

### Build and Run

```bash
docker-compose up --build
```

### Access the Application

```
http://localhost
```
