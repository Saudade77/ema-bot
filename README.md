
# EMA Trailing Trading Bot

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Binance](https://img.shields.io/badge/Exchange-Binance-yellow.svg)

An automated trading assistant based on EMA (Exponential Moving Average), designed for **Binance USDT-M Futures**.

Automatically monitors market signals and manages limit orders to implement a "follow the EMA" trading strategy.

[中文文档](README_CN.md)

---

## ✨ Features

- 📊 **EMA Auto-Tracking** - Limit order prices automatically follow EMA values
- 🔄 **Smart Order Management** - Auto create, update, and cancel orders
- 📱 **Telegram Control** - Remote management via Telegram Bot
- 🔔 **Real-time Notifications** - Instant alerts on order status changes
- ⚡ **Hedge Mode Support** - Compatible with one-way/hedge position modes
- 🎯 **Multiple Timeframes** - 15m / 1h / 4h / 1d / 1w / 1M

---

## 📁 Project Structure


emaBot/
├── .env                 # Environment configuration
├── ema_bot.py          # Core logic (EMA calculation, order management)
├── telegram_bot.py     # Telegram Bot interface
├── orders.json         # Order configuration (auto-generated)
├── README.md           # English Documentation
└── README_CN.md        # Chinese Documentation

---

## 🚀 Quick Start

### 1. Clone Repository

```bash
git clone https://github.com/yourusername/ema-trading-bot.git
cd ema-trading-bot

2. Install Dependencies
bashDownloadCopy codepip install python-dotenv requests pandas python-telegram-bot
3. Configure Environment Variables
Create a .env file:
envDownloadCopy code# Binance API (Futures trading permission required)
API_KEY=your_api_key
API_SECRET=your_api_secret

# Telegram Bot
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
Binance API:

1. Log in to Binance
2. Go to API Management
3. Create API with "Futures Trading" permission enabled
4. Recommended: Enable IP whitelist

Telegram Bot:

1. Search for @BotFather on Telegram
2. Send /newbot to create a bot and get the Token
3. Search for @userinfobot to get your Chat ID

4. Start the Bot
bashDownloadCopy code# Option 1: Via Telegram Bot (Recommended)
python telegram_bot.py

# Option 2: Command line
python ema_bot.py run

📱 Telegram Commands
CommandDescriptionExample/startShow help menu-/bind [symbol] [interval] [EMA]Bind existing order to tracking/bind BTC 4h 21/listView all tracked orders-/remove [ID]Remove tracked order/remove BTCUSDT_4h_EMA21_BUY/ema [symbol] [interval]Query EMA values/ema ETH 1h/price [symbol]Query current price/price BTC/balanceQuery account balance-/statusBot running status-/start_botStart tracking-/stop_botStop tracking-

💻 Command Line Usage
bashDownloadCopy code# Start tracking (default: check every 60 seconds)
python ema_bot.py run

# Custom check interval (30 seconds)
python ema_bot.py run 30

# View order list
python ema_bot.py list

# Remove order
python ema_bot.py remove BTCUSDT_4h_EMA21_BUY

# Query EMA values
python ema_bot.py ema BTC 4h

📋 Workflow
┌─────────────────────────────────────┐
│  1. Place a limit order on Binance  │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  2. Use /bind command to track it   │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  3. Bot automatically tracks EMA    │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  4. Auto-update when price > 0.3%   │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  5. Auto-remove and notify on fill  │
└─────────────────────────────────────┘


⚙️ Supported Parameters
TypeSupported ValuesEMA Periods21 / 55 / 100 / 200Timeframes15m / 1h / 4h / 1d / 1w / 1MUpdate Threshold0.3% (configurable via price_threshold)

🔧 Configuration
Order Configuration File (orders.json)
jsonDownloadCopy code[
  {
    "id": "BTCUSDT_4h_EMA21_BUY",
    "symbol": "BTCUSDT",
    "interval": "4h",
    "ema": 21,
    "side": "BUY",
    "quantity": 0.001,
    "binance_order_id": 123456789,
    "status": "active",
    "leverage": 10,
    "margin_type": "CROSSED",
    "position_side": "LONG"
  }
]
Field Description
FieldDescriptionidUnique identifier (auto-generated)symbolTrading pairintervalKline intervalemaEMA periodsideDirection (BUY/SELL)quantityOrder quantityleverageLeverage multipliermargin_typeMargin mode (CROSSED/ISOLATED)position_sidePosition direction (LONG/SHORT/BOTH)

🔍 How It Works
                    ┌──────────────┐
                    │  Binance API │
                    └──────┬───────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   ┌──────────┐     ┌──────────┐     ┌──────────┐
   │ Get Klines│     │ Get Orders│     │Place Order│
   └────┬─────┘     └────┬─────┘     └────┬─────┘
        │                │                │
        ▼                ▼                ▼
   ┌──────────┐     ┌──────────┐     ┌──────────┐
   │Calc EMA  │────▶│Compare   │────▶│Update    │
   └──────────┘     │  Price   │     │  Order   │
                    └──────────┘     └──────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │Telegram Alert│
                    └──────────────┘


⚠️ Risk Warning

⚠️ This project is for educational purposes only. Not financial advice.

⚠️ Risk ConsiderationsFutures trading carries high risk and may result in total loss of capitalTest with small amounts first to validate the strategyRecommended: Test on testnet firstKeep your API Key secure and never share itOnly enable "Futures Trading" permission, never enable "Withdrawal"

🐛 FAQ
Make sure you're using the latest code. The calculate_ema method now excludes incomplete candles.
Server time sync issue. The code handles this automatically. If the problem persists, check your system time.
Ensure position_side (LONG/SHORT) is correctly set when binding. The code automatically detects position mode.

📄 License
MIT License

🤝 Contributing
Issues and Pull Requests are welcome!

📮 Contact
For questions, please submit an Issue

