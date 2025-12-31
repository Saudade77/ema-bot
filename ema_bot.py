import os
import sys
import json
import time
import hmac
import hashlib
from datetime import datetime
from typing import Dict, List, Optional
import requests
import pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# === Telegram 通知配置 ===
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

def send_telegram_message(message: str):
    """发送 Telegram 消息通知"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        resp = requests.post(url, data=data, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"⚠️ Telegram 发送失败: {e}")
        return False

# 订单配置文件路径
ORDERS_FILE = Path(__file__).parent / "orders.json"

# 支持的EMA周期
SUPPORTED_EMA = [21, 55, 100, 200]

# 支持的时间周期
INTERVAL_MAP = {
    '15m': '15m',
    '15min': '15m',
    '1h': '1h',
    '4h': '4h',
    '1d': '1d',
    '1D': '1d',
    '1w': '1w',
    '1W': '1w',
    '1M': '1M',
}


class OrderManager:
    """订单配置管理"""
    
    @staticmethod
    def load_orders() -> List[dict]:
        if ORDERS_FILE.exists():
            with open(ORDERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    
    @staticmethod
    def save_orders(orders: List[dict]):
        with open(ORDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(orders, f, indent=2, ensure_ascii=False)
    
    @staticmethod
    def add_order(symbol: str, interval: str, ema: int, side: str, quantity: float) -> dict:
        """添加新订单追踪"""
        orders = OrderManager.load_orders()
        
        symbol = symbol.upper()
        if not symbol.endswith('USDT'):
            symbol = symbol + 'USDT'
        
        interval = INTERVAL_MAP.get(interval.lower(), interval)
        
        if ema not in SUPPORTED_EMA:
            raise ValueError(f"EMA必须是 {SUPPORTED_EMA} 之一")
        
        side = side.upper()
        if side not in ['BUY', 'SELL']:
            raise ValueError("side必须是 BUY 或 SELL")
        
        order_id = f"{symbol}_{interval}_EMA{ema}_{side}"
        
        for o in orders:
            if o['id'] == order_id:
                raise ValueError(f"订单已存在: {order_id}")
        
        new_order = {
            'id': order_id,
            'symbol': symbol,
            'interval': interval,
            'ema': ema,
            'side': side,
            'quantity': quantity,
            'binance_order_id': None,
            'status': 'active',
            'created_at': datetime.now().isoformat()
        }
        
        orders.append(new_order)
        OrderManager.save_orders(orders)
        return new_order
    
    @staticmethod
    def remove_order(order_id: str) -> bool:
        """移除订单追踪"""
        orders = OrderManager.load_orders()
        new_orders = [o for o in orders if o['id'] != order_id]
        
        if len(new_orders) < len(orders):
            OrderManager.save_orders(new_orders)
            return True
        return False
    
    @staticmethod
    def list_orders() -> List[dict]:
        return OrderManager.load_orders()
    
    @staticmethod
    def update_binance_order_id(order_id: str, binance_order_id: int):
        """更新币安订单ID"""
        orders = OrderManager.load_orders()
        for o in orders:
            if o['id'] == order_id:
                o['binance_order_id'] = binance_order_id
                break
        OrderManager.save_orders(orders)


class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv('API_KEY')
        self.api_secret = os.getenv('API_SECRET')
        
        self.base_url = "https://fapi.binance.com"
        
        self.session = requests.Session()
        self.session.headers.update({'X-MBX-APIKEY': self.api_key})

        self.time_offset = 0
        self._sync_time()
    
    def _sync_time(self):
        """同步合约服务器时间"""
        try:
            url = f"{self.base_url}/fapi/v1/time"
            resp = self.session.get(url, timeout=10)
            server_time = resp.json()['serverTime']
            local_time = int(time.time() * 1000)
            self.time_offset = server_time - local_time
            print(f"⏱️ 合约服务器时间偏移: {self.time_offset}ms")
        except Exception as e:
            print(f"⚠️ 时间同步失败: {e}")
            self.time_offset = 0
    
    def _sign(self, params: dict) -> dict:
        """签名"""
        params['timestamp'] = int(time.time() * 1000) + self.time_offset
        params['recvWindow'] = 10000
        query_string = '&'.join(f"{k}={v}" for k, v in params.items())
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        params['signature'] = signature
        return params

    def get_current_price(self, symbol: str) -> float:
        """获取合约当前价格"""
        url = f"{self.base_url}/fapi/v1/ticker/price"
        params = {'symbol': symbol}
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return float(resp.json()['price'])

    def calculate_ema(self, symbol: str, period: int, interval: str) -> float:
        """计算合约 EMA"""
        url = f"{self.base_url}/fapi/v1/klines"
        limit = period + 10
        params = {'symbol': symbol, 'interval': interval, 'limit': limit}
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        klines = resp.json()
        
        closes = [float(k[4]) for k in klines]
        if len(closes) < period:
            return 0.0
            
        df = pd.DataFrame({'close': closes})
        ema = df['close'].ewm(span=period, adjust=False).mean()
        return ema.iloc[-1]

    def get_open_orders(self, symbol: str) -> list:
        """获取合约挂单"""
        url = f"{self.base_url}/fapi/v1/openOrders"
        params = self._sign({'symbol': symbol})
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_order_status(self, symbol: str, order_id: int) -> dict:
        """查询合约订单状态"""
        try:
            url = f"{self.base_url}/fapi/v1/order"
            params = {
                'symbol': symbol,
                'orderId': order_id
            }
            params = self._sign(params)
            resp = self.session.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"⚠️ 查询订单状态失败: {e}")
            return None

    def get_account_balance(self) -> dict:
        """获取合约账户余额"""
        url = f"{self.base_url}/fapi/v2/balance"
        params = self._sign({})
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        
        balances = {}
        for asset in data:
            if asset['asset'] == 'USDT':
                balances['USDT'] = float(asset['availableBalance'])
                break
        return balances

    def create_order(self, symbol: str, side: str, price: float, quantity: float):
        """下合约限价单"""
        url = f"{self.base_url}/fapi/v1/order"
        params = {
            'symbol': symbol,
            'side': side.upper(),
            'type': 'LIMIT',
            'timeInForce': 'GTC',
            'quantity': quantity,
            'price': price
        }
        params = self._sign(params)
        resp = self.session.post(url, data=params)
        resp.raise_for_status()
        return resp.json()

    def cancel_order(self, symbol: str, order_id: int):
        """取消合约订单"""
        url = f"{self.base_url}/fapi/v1/order"
        params = {
            'symbol': symbol,
            'orderId': order_id
        }
        params = self._sign(params)
        resp = self.session.delete(url, params=params)
        resp.raise_for_status()
        return resp.json()


class EMATrailingBot:
    """EMA追踪机器人主程序"""
    
    def __init__(self):
        self.client = BinanceClient()
        self.price_threshold = 0.001  # 0.1%价差触发更新
    
    def process_order(self, order_config: dict) -> str:
        """处理单个订单"""
        symbol = order_config['symbol']
        interval = order_config['interval']
        ema_period = order_config['ema']
        side = order_config['side']
        quantity = order_config['quantity']
        binance_order_id = order_config.get('binance_order_id')
        order_id = order_config['id']
        
        # 计算当前EMA
        ema_price = self.client.calculate_ema(symbol, ema_period, interval)
        
        # 获取当前价格
        current_price = self.client.get_current_price(symbol)
        
        # 检查币安订单状态
        open_orders = self.client.get_open_orders(symbol)
        our_order = None
        
        if binance_order_id:
            for o in open_orders:
                if o['orderId'] == binance_order_id:
                    our_order = o
                    break
        
        if our_order:
            # 订单存在，检查是否需要更新
            order_price = float(our_order['price'])
            price_diff = abs(order_price - ema_price) / ema_price
            
            if price_diff > self.price_threshold:
                # 更新订单
                self.client.cancel_order(symbol, binance_order_id)
                new_order = self.client.create_order(symbol, side, ema_price, quantity)
                OrderManager.update_binance_order_id(order_id, new_order['orderId'])
                
                diff_percent = ((ema_price - order_price) / order_price) * 100
                direction = "↑" if diff_percent > 0 else "↓"
                
                message = (
                    f"🔄 *订单已更新*\n\n"
                    f"📌 ID: `{order_id}`\n"
                    f"💱 交易对: {symbol}\n"
                    f"📊 周期: {interval} | EMA{ema_period}\n"
                    f"🎯 方向: {side}\n"
                    f"📦 数量: {quantity}\n\n"
                    f"💰 当前价格: `{current_price:,.2f}`\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"❌ 旧订单价: `{order_price:,.2f}`\n"
                    f"✅ 新订单价: `{ema_price:,.2f}`\n"
                    f"📈 变动: {direction} {abs(diff_percent):.2f}%\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                send_telegram_message(message)
                
                return f"📝 更新 {order_price:.2f} → {ema_price:.2f}"
            else:
                return f"✓ EMA={ema_price:.2f} 订单={order_price:.2f}"
        
        else:
            # 订单不存在
            if binance_order_id is not None:
                # 之前有订单ID，现在没了 -> 判断是成交还是被取消
                order_status = self.client.get_order_status(symbol, binance_order_id)
                
                if order_status and order_status.get('status') == 'FILLED':
                    # 订单已成交
                    avg_price = float(order_status.get('avgPrice', 0))
                    message = (
                        f"🎉 *订单已成交!*\n\n"
                        f"📌 ID: `{order_id}`\n"
                        f"💱 交易对: {symbol}\n"
                        f"🎯 方向: {side}\n"
                        f"📦 数量: {quantity}\n"
                        f"💵 成交价: `{avg_price:,.2f}`\n"
                        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    send_telegram_message(message)
                    OrderManager.remove_order(order_id)
                    return "🎉 已成交，停止追踪"
                
                elif order_status and order_status.get('status') == 'CANCELED':
                    # 订单被手动取消
                    message = (
                        f"🚫 *订单已被手动取消*\n\n"
                        f"📌 ID: `{order_id}`\n"
                        f"💱 交易对: {symbol}\n"
                        f"📊 周期: {interval} | EMA{ema_period}\n"
                        f"🎯 方向: {side}\n\n"
                        f"已自动停止追踪此订单\n"
                        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    send_telegram_message(message)
                    OrderManager.remove_order(order_id)
                    return "🚫 已取消，停止追踪"
                
                else:
                    # 其他情况
                    status = order_status.get('status', '未知') if order_status else '未知'
                    message = (
                        f"⚠️ *订单已失效*\n\n"
                        f"📌 ID: `{order_id}`\n"
                        f"💱 交易对: {symbol}\n"
                        f"状态: {status}\n\n"
                        f"已自动停止追踪此订单\n"
                        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    send_telegram_message(message)
                    OrderManager.remove_order(order_id)
                    return "⚠️ 已失效，停止追踪"
            
            # 首次下单
            new_order = self.client.create_order(symbol, side, ema_price, quantity)
            OrderManager.update_binance_order_id(order_id, new_order['orderId'])
            
            message = (
                f"📌 *新订单已创建*\n\n"
                f"📌 ID: `{order_id}`\n"
                f"💱 交易对: {symbol}\n"
                f"📊 周期: {interval} | EMA{ema_period}\n"
                f"🎯 方向: {side}\n"
                f"📦 数量: {quantity}\n"
                f"💵 挂单价: `{ema_price:,.2f}`\n"
                f"💰 当前价: `{current_price:,.2f}`\n"
                f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            send_telegram_message(message)
            
            return f"📌 新建订单 @ {ema_price:.2f}"
    
    def run(self, check_interval: int = 60):
        """主循环"""
        print("=" * 60)
        print("🚀 EMA多订单追踪机器人启动")
        print("=" * 60)
        print(f"   检查间隔: {check_interval}秒")
        print(f"   更新阈值: {self.price_threshold * 100}%")
        print(f"   Telegram通知: {'✅ 已启用' if TELEGRAM_TOKEN else '❌ 未配置'}")
        print("=" * 60)
        
        if TELEGRAM_TOKEN:
            send_telegram_message(
                f"🚀 *EMA追踪机器人已启动*\n\n"
                f"⏱️ 检查间隔: {check_interval}秒\n"
                f"📊 更新阈值: {self.price_threshold * 100}%\n"
                f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        
        try:
            balances = self.client.get_account_balance()
            print("💰 账户余额:")
            for asset, amount in balances.items():
                if amount > 0.0001:
                    print(f"   {asset}: {amount}")
        except Exception as e:
            print(f"⚠️ 无法获取余额: {e}")
        
        print("\n按 Ctrl+C 停止机器人")
        print("-" * 60)
        
        while True:
            try:
                orders = OrderManager.load_orders()
                active_orders = [o for o in orders if o.get('status') == 'active']
                
                if not active_orders:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 暂无追踪订单，等待添加...")
                    time.sleep(check_interval)
                    continue
                
                current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"\n[{current_time}] 处理 {len(active_orders)} 个订单")
                print("-" * 40)
                
                for order in active_orders:
                    try:
                        result = self.process_order(order)
                        print(f"  {order['id']}: {result}")
                    except Exception as e:
                        print(f"  {order['id']}: ❌ 错误 - {e}")
                        send_telegram_message(
                            f"⚠️ *订单处理错误*\n\n"
                            f"ID: `{order['id']}`\n"
                            f"错误: {str(e)[:100]}"
                        )
                
                print("-" * 40)
                
            except KeyboardInterrupt:
                print("\n\n⏹️ 用户停止机器人")
                if TELEGRAM_TOKEN:
                    send_telegram_message("⏹️ *EMA追踪机器人已停止*")
                break
            
            except Exception as e:
                print(f"❌ 主循环错误: {e}")
                send_telegram_message(f"❌ *机器人错误*\n\n{str(e)[:200]}")
            
            time.sleep(check_interval)


def print_help():
    """打印帮助信息"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║              EMA追踪限价单机器人 - 使用说明                    ║
╠══════════════════════════════════════════════════════════════╣
║  运行机器人:                                                  ║
║    python ema_bot.py run                                     ║
║                                                              ║
║  添加订单:                                                    ║
║    python ema_bot.py add <币种> <周期> <EMA> <方向> <数量>     ║
║    例: python ema_bot.py add BTC 4h 21 BUY 0.001             ║
║                                                              ║
║  查看订单:                                                    ║
║    python ema_bot.py list                                    ║
║                                                              ║
║  删除订单:                                                    ║
║    python ema_bot.py remove <订单ID>                         ║
║                                                              ║
║  查看当前EMA值:                                               ║
║    python ema_bot.py ema <币种> <周期>                        ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║  支持的周期: 15m, 1h, 4h, 1d, 1w, 1M                          ║
║  支持的EMA: 21, 55, 100, 200                                 ║
║  方向: BUY(做多入场) / SELL(做空入场)                          ║
╚══════════════════════════════════════════════════════════════╝
""")


def cmd_add(args):
    if len(args) < 5:
        print("用法: python ema_bot.py add <币种> <周期> <EMA> <方向> <数量>")
        return
    
    symbol, interval, ema, side, quantity = args[0], args[1], int(args[2]), args[3], float(args[4])
    
    try:
        order = OrderManager.add_order(symbol, interval, ema, side, quantity)
        print(f"✅ 订单添加成功!")
        print(f"   ID: {order['id']}")
        print(f"   交易对: {order['symbol']}")
        print(f"   周期: {order['interval']}")
        print(f"   EMA: {order['ema']}")
        print(f"   方向: {order['side']}")
        print(f"   数量: {order['quantity']}")
    except Exception as e:
        print(f"❌ 添加失败: {e}")


def cmd_list(args):
    orders = OrderManager.list_orders()
    
    if not orders:
        print("暂无订单")
        return
    
    print(f"\n{'='*70}")
    print(f"{'ID':<35} {'方向':<6} {'数量':<12} {'状态':<8}")
    print(f"{'='*70}")
    
    for o in orders:
        print(f"{o['id']:<35} {o['side']:<6} {o['quantity']:<12} {o['status']:<8}")
    
    print(f"{'='*70}")
    print(f"共 {len(orders)} 个订单\n")


def cmd_remove(args):
    if len(args) < 1:
        print("用法: python ema_bot.py remove <订单ID>")
        return
    
    order_id = args[0]
    
    orders = OrderManager.load_orders()
    for o in orders:
        if o['id'] == order_id and o.get('binance_order_id'):
            try:
                client = BinanceClient()
                client.cancel_order(o['symbol'], o['binance_order_id'])
                print(f"✅ 已取消币安订单 {o['binance_order_id']}")
            except Exception as e:
                print(f"⚠️ 取消币安订单失败: {e}")
    
    if OrderManager.remove_order(order_id):
        print(f"✅ 已删除订单: {order_id}")
    else:
        print(f"❌ 订单不存在: {order_id}")


def cmd_ema(args):
    if len(args) < 2:
        print("用法: python ema_bot.py ema <币种> <周期>")
        return
    
    symbol = args[0].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'
    
    interval = INTERVAL_MAP.get(args[1].lower(), args[1])
    
    client = BinanceClient()
    current_price = client.get_current_price(symbol)
    
    print(f"\n{symbol} ({interval}) 当前价格: {current_price}")
    print("-" * 40)
    
    for ema in SUPPORTED_EMA:
        ema_value = client.calculate_ema(symbol, ema, interval)
        diff = ((current_price - ema_value) / ema_value) * 100
        direction = "↑" if diff > 0 else "↓"
        print(f"  EMA{ema:<3}: {ema_value:>12.2f}  ({direction} {abs(diff):.2f}%)")
    
    print()


def main():
    if len(sys.argv) < 2:
        print_help()
        return
    
    command = sys.argv[1].lower()
    args = sys.argv[2:]
    
    if command == 'run':
        bot = EMATrailingBot()
        check_interval = int(args[0]) if args else 60
        bot.run(check_interval)
    
    elif command == 'add':
        cmd_add(args)
    
    elif command == 'list':
        cmd_list(args)
    
    elif command == 'remove':
        cmd_remove(args)
    
    elif command == 'ema':
        cmd_ema(args)
    
    elif command in ['help', '-h', '--help']:
        print_help()
    
    else:
        print(f"未知命令: {command}")
        print_help()


if __name__ == "__main__":
    main()