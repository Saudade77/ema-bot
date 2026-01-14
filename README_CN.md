markdownDownloadCopy code# EMA 追踪交易机器人

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Binance](https://img.shields.io/badge/Exchange-Binance-yellow.svg)

基于 EMA (指数移动平均线) 的自动化交易助手，专为 **Binance U本位合约 (USDT-M Futures)** 设计。

自动监控市场信号并管理限价单，实现"跟EMA走"的交易策略。

---

## ✨ 功能特性

- 📊 **EMA 自动追踪** - 限价单价格自动跟随 EMA 值更新
- 🔄 **智能订单管理** - 自动创建、更新、取消订单
- 📱 **Telegram 控制** - 通过 Telegram Bot 远程管理
- 🔔 **实时通知** - 订单状态变化即时推送
- ⚡ **双向持仓支持** - 兼容单向/双向持仓模式
- 🎯 **多周期支持** - 15m / 1h / 4h / 1d / 1w / 1M

---

## 📁 项目结构

emaBot/
├── .env                 # 环境变量配置
├── ema_bot.py          # 核心逻辑 (EMA计算、订单管理)
├── telegram_bot.py     # Telegram Bot 交互界面
├── orders.json         # 订单配置文件 (自动生成)
├── README.md           # English Documentation
└── README_CN.md        # 中文文档

---

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/yourusername/ema-trading-bot.git
cd ema-trading-bot

2. 安装依赖
bashDownloadCopy codepip install python-dotenv requests pandas python-telegram-bot
3. 配置环境变量
创建 .env 文件：
envDownloadCopy code# Binance API (需要合约交易权限)
API_KEY=你的API_KEY
API_SECRET=你的API_SECRET

# Telegram Bot
TELEGRAM_TOKEN=你的Bot_Token
TELEGRAM_CHAT_ID=你的Chat_ID
Binance API：

1. 登录 Binance
2. 进入 API 管理页面
3. 创建 API，开启「合约交易」权限
4. 建议开启 IP 白名单

Telegram Bot：

1. 在 Telegram 搜索 @BotFather
2. 发送 /newbot 创建机器人，获取 Token
3. 搜索 @userinfobot 获取你的 Chat ID

4. 启动机器人
bashDownloadCopy code# 方式一：通过 Telegram Bot 控制 (推荐)
python telegram_bot.py

# 方式二：命令行直接运行
python ema_bot.py run

📱 Telegram 命令
命令说明示例/start显示帮助菜单-/bind [币种] [周期] [EMA]绑定已有挂单到追踪/bind BTC 4h 21/list查看所有追踪订单-/remove [ID]删除追踪订单/remove BTCUSDT_4h_EMA21_BUY/ema [币种] [周期]查询 EMA 值/ema ETH 1h/price [币种]查询当前价格/price BTC/balance查询账户余额-/status机器人运行状态-/start_bot启动追踪-/stop_bot停止追踪-

💻 命令行使用
bashDownloadCopy code# 启动追踪 (默认60秒检查一次)
python ema_bot.py run

# 自定义检查间隔 (30秒)
python ema_bot.py run 30

# 查看订单列表
python ema_bot.py list

# 删除订单
python ema_bot.py remove BTCUSDT_4h_EMA21_BUY

# 查询 EMA 值
python ema_bot.py ema BTC 4h

📋 使用流程
┌─────────────────────────────────────┐
│  1. 在币安合约下一个限价单            │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  2. 使用 /bind 命令绑定该订单         │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  3. 机器人自动追踪 EMA 价格           │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  4. 价格偏离超过 0.3% 时自动更新订单   │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│  5. 订单成交后自动移除并通知          │
└─────────────────────────────────────┘


⚙️ 支持的参数
类型支持值EMA 周期21 / 55 / 100 / 200时间周期15m / 1h / 4h / 1d / 1w / 1M更新阈值0.3% (可在代码中修改 price_threshold)

🔧 配置说明
订单配置文件 (orders.json)
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
字段说明
字段说明id唯一标识符 (自动生成)symbol交易对intervalK线周期emaEMA周期side方向 (BUY/SELL)quantity数量leverage杠杆倍数margin_type保证金模式 (CROSSED/ISOLATED)position_side持仓方向 (LONG/SHORT/BOTH)

🔍 工作原理
                    ┌──────────────┐
                    │  Binance API │
                    └──────┬───────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   ┌──────────┐     ┌──────────┐     ┌──────────┐
   │ 获取K线   │     │ 获取订单  │     │ 下单/改单 │
   └────┬─────┘     └────┬─────┘     └────┬─────┘
        │                │                │
        ▼                ▼                ▼
   ┌──────────┐     ┌──────────┐     ┌──────────┐
   │ 计算EMA   │────▶│ 比较价格  │────▶│ 更新订单  │
   └──────────┘     └──────────┘     └──────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │ Telegram通知  │
                    └──────────────┘


⚠️ 风险提示

⚠️ 本项目仅供学习交流，不构成投资建议

⚠️ 风险事项合约交易具有高风险，可能导致本金全部损失请使用小资金测试，确认策略有效后再投入建议先在测试网验证请妥善保管 API Key，不要泄露给他人API 权限仅开启「合约交易」，不要开启「提现」

🐛 常见问题
确保使用最新代码，calculate_ema 方法已排除未完成的K线。
服务器时间不同步，代码已自动处理，如仍有问题请检查系统时间。
确保绑定时正确设置 position_side（LONG/SHORT），代码会自动检测持仓模式。

📄 License
MIT License

🤝 贡献
欢迎提交 Issue 和 Pull Request！

📮 联系方式
如有问题，请提交 Issue

