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
    def add_order(symbol: str, interval: str, ema: int, side: str, quantity: float, 
                  leverage: int = None, margin_type: str = None) -> dict:
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
            'created_at': datetime.now().isoformat(),
            'leverage': leverage,
            'margin_type': margin_type,
            'notified_error': False  # 是否已通知过错误
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
    def update_order(order_id: str, **kwargs):
        """更新订单信息"""
        orders = OrderManager.load_orders()
        for o in orders:
            if o['id'] == order_id:
                for key, value in kwargs.items():
                    o[key] = value
                break
        OrderManager.save_orders(orders)
    
    @staticmethod
    def update_binance_order_id(order_id: str, binance_order_id: int):
        """更新币安订单ID"""
        OrderManager.update_order(order_id, binance_order_id=binance_order_id)
    
    @staticmethod
    def set_notified(order_id: str, notified: bool):
        """设置是否已通知"""
        OrderManager.update_order(order_id, notified_error=notified)


class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv('API_KEY')
        self.api_secret = os.getenv('API_SECRET')
        
        if not self.api_key or not self.api_secret:
            raise ValueError("API_KEY 或 API_SECRET 未配置")
        
        self.api_key = self.api_key.strip()
        self.api_secret = self.api_secret.strip()
        
        self.base_url = "https://fapi.binance.com"
        
        self.session = requests.Session()
        self.session.headers.update({
            'X-MBX-APIKEY': self.api_key
        })

        self.time_offset = 0
        self._sync_time()
        
        self._exchange_info = None
    
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
    
    def _sign(self, params: dict) -> str:
        """签名并返回完整的 query string"""
        params['timestamp'] = int(time.time() * 1000) + self.time_offset
        params['recvWindow'] = 10000
        query_string = '&'.join(f"{k}={v}" for k, v in params.items())
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return f"{query_string}&signature={signature}"

    def get_symbol_info(self, symbol: str) -> dict:
        """获取交易对精度信息（带缓存）"""
        if not self._exchange_info:
            url = f"{self.base_url}/fapi/v1/exchangeInfo"
            resp = self.session.get(url, timeout=10)
            resp.raise_for_status()
            self._exchange_info = resp.json()
        
        for s in self._exchange_info['symbols']:
            if s['symbol'] == symbol:
                return s
        return None

    def format_price(self, symbol: str, price: float) -> str:
        """根据交易对规则格式化价格"""
        info = self.get_symbol_info(symbol)
        if not info:
            return f"{price:.2f}"
        
        for f in info['filters']:
            if f['filterType'] == 'PRICE_FILTER':
                tick_size = float(f['tickSize'])
                
                if tick_size >= 1:
                    precision = 0
                else:
                    precision = len(str(tick_size).rstrip('0').split('.')[-1])
                
                price = (price // tick_size) * tick_size
                return f"{price:.{precision}f}"
        
        return f"{price:.2f}"

    def format_quantity(self, symbol: str, quantity: float) -> str:
        """根据交易对规则格式化数量"""
        info = self.get_symbol_info(symbol)
        if not info:
            return str(quantity)
        
        for f in info['filters']:
            if f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
                
                if step_size >= 1:
                    precision = 0
                else:
                    precision = len(str(step_size).rstrip('0').split('.')[-1])
                
                quantity = (quantity // step_size) * step_size
                return f"{quantity:.{precision}f}"
        
        return str(quantity)

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
        query_string = self._sign({'symbol': symbol})
        resp = self.session.get(f"{url}?{query_string}")
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
            query_string = self._sign(params)
            resp = self.session.get(f"{url}?{query_string}")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"⚠️ 查询订单状态失败: {e}")
            return None

    def get_account_balance(self) -> dict:
        """获取合约账户余额"""
        url = f"{self.base_url}/fapi/v2/balance"
        query_string = self._sign({})
        resp = self.session.get(f"{url}?{query_string}")
        resp.raise_for_status()
        data = resp.json()
        
        balances = {}
        for asset in data:
            if asset['asset'] == 'USDT':
                balances['USDT'] = float(asset['availableBalance'])
                break
        return balances

    def get_position_mode(self) -> bool:
        """获取持仓模式 (True=双向持仓, False=单向持仓)"""
        url = f"{self.base_url}/fapi/v1/positionSide/dual"
        query_string = self._sign({})
        resp = self.session.get(f"{url}?{query_string}")
        resp.raise_for_status()
        return resp.json().get('dualSidePosition', False)

    def get_leverage(self, symbol: str) -> int:
        """获取交易对当前杠杆倍数"""
        url = f"{self.base_url}/fapi/v2/positionRisk"
        query_string = self._sign({'symbol': symbol})
        resp = self.session.get(f"{url}?{query_string}")
        resp.raise_for_status()
        data = resp.json()
        if data:
            return int(data[0].get('leverage', 20))
        return 20

    def set_leverage(self, symbol: str, leverage: int):
        """设置杠杆倍数"""
        url = f"{self.base_url}/fapi/v1/leverage"
        params = {
            'symbol': symbol,
            'leverage': leverage
        }
        query_string = self._sign(params)
        resp = self.session.post(f"{url}?{query_string}")
        resp.raise_for_status()
        return resp.json()

    def get_margin_type(self, symbol: str) -> str:
        """获取保证金模式 (ISOLATED=逐仓, CROSSED=全仓)"""
        url = f"{self.base_url}/fapi/v2/positionRisk"
        query_string = self._sign({'symbol': symbol})
        resp = self.session.get(f"{url}?{query_string}")
        resp.raise_for_status()
        data = resp.json()
        if data:
            return data[0].get('marginType', 'cross').upper()
        return 'CROSS'

    def set_margin_type(self, symbol: str, margin_type: str):
        """设置保证金模式 (ISOLATED=逐仓, CROSSED=全仓)"""
        url = f"{self.base_url}/fapi/v1/marginType"
        params = {
            'symbol': symbol,
            'marginType': margin_type.upper()
        }
        query_string = self._sign(params)
        resp = self.session.post(f"{url}?{query_string}")
        # 如果已经是该模式，会返回错误，忽略
        if resp.status_code == 200:
            return resp.json()
        return None

    def create_order(self, symbol: str, side: str, price: float, quantity: float,
                     leverage: int = None, margin_type: str = None):
        """下合约限价单（确保杠杆和保证金模式正确）"""
        
        # 如果指定了杠杆，先设置杠杆
        if leverage:
            try:
                current_leverage = self.get_leverage(symbol)
                if current_leverage != leverage:
                    self.set_leverage(symbol, leverage)
                    print(f"   ✅ 杠杆已设置为 {leverage}x")
            except Exception as e:
                print(f"   ⚠️ 设置杠杆失败: {e}")
        
        # 如果指定了保证金模式，先设置
        if margin_type:
            try:
                current_margin = self.get_margin_type(symbol)
                target_margin = 'ISOLATED' if margin_type.upper() in ['ISOLATED', '逐仓'] else 'CROSSED'
                if current_margin != target_margin:
                    self.set_margin_type(symbol, target_margin)
                    print(f"   ✅ 保证金模式已设置为 {target_margin}")
            except Exception as e:
                print(f"   ⚠️ 设置保证金模式失败: {e}")
        
        price_str = self.format_price(symbol, price)
        quantity_str = self.format_quantity(symbol, quantity)
        
        print(f"📝 下单: {symbol} {side} 价格={price_str} 数量={quantity_str}")
        
        url = f"{self.base_url}/fapi/v1/order"
        params = {
            'symbol': symbol,
            'side': side.upper(),
            'type': 'LIMIT',
            'timeInForce': 'GTC',
            'quantity': quantity_str,
            'price': price_str
        }
        query_string = self._sign(params)
        
        resp = self.session.post(f"{url}?{query_string}")
        
        if resp.status_code != 200:
            error_detail = resp.text
            print(f"❌ 下单失败: {resp.status_code} - {error_detail}")
            raise Exception(f"下单失败: {error_detail}")
        
        return resp.json()

    def cancel_order(self, symbol: str, order_id: int):
        """取消合约订单"""
        url = f"{self.base_url}/fapi/v1/order"
        params = {
            'symbol': symbol,
            'orderId': order_id
        }
        query_string = self._sign(params)
        resp = self.session.delete(f"{url}?{query_string}")
        resp.raise_for_status()
        return resp.json()


class EMATrailingBot:
    """EMA追踪机器人主程序"""
    
    def __init__(self):
        self.client = BinanceClient()
        self.price_threshold = 0.003  # 0.3% 避免频繁更新
    
    def process_order(self, order_config: dict) -> str:
        """处理单个订单"""
        symbol = order_config['symbol']
        interval = order_config['interval']
        ema_period = order_config['ema']
        side = order_config['side']
        quantity = order_config['quantity']
        binance_order_id = order_config.get('binance_order_id')
        order_id = order_config['id']
        notified = order_config.get('notified_error', False)
        leverage = order_config.get('leverage')
        margin_type = order_config.get('margin_type')
        
        try:
            ema_price = self.client.calculate_ema(symbol, ema_period, interval)
            current_price = self.client.get_current_price(symbol)
            
            open_orders = self.client.get_open_orders(symbol)
            our_order = None
            
            if binance_order_id:
                for o in open_orders:
                    if o['orderId'] == binance_order_id:
                        our_order = o
                        break
            
            if our_order:
                order_price = float(our_order['price'])
                price_diff = abs(order_price - ema_price) / ema_price
                
                if price_diff > self.price_threshold:
                    print(f"🔄 更新订单 {order_id}: {order_price:.4f} → {ema_price:.4f}")
                    
                    # 1. 取消旧订单
                    try:
                        self.client.cancel_order(symbol, binance_order_id)
                        print(f"   ✅ 旧订单已取消")
                    except Exception as cancel_err:
                        error_str = str(cancel_err)
                        if "Unknown order" in error_str or "-2011" in error_str:
                            old_status = self.client.get_order_status(symbol, binance_order_id)
                            if old_status and old_status.get('status') == 'FILLED':
                                OrderManager.remove_order(order_id)
                                send_telegram_message(
                                    f"🎉 *订单已成交*\n\n"
                                    f"ID: `{order_id}`\n"
                                    f"成交价: {float(old_status.get('avgPrice', 0)):,.4f}"
                                )
                                return "🎉 已成交"
                        return f"⚠️ 取消失败"
                    
                    time.sleep(0.3)
                    
                    # 2. 创建新订单（带杠杆和保证金模式）
                    try:
                        new_order = self.client.create_order(
                            symbol, side, ema_price, quantity,
                            leverage=leverage, margin_type=margin_type
                        )
                        new_order_id = new_order['orderId']
                        
                        OrderManager.update_order(order_id, 
                            binance_order_id=new_order_id, 
                            notified_error=False
                        )
                        
                        diff_percent = ((ema_price - order_price) / order_price) * 100
                        direction = "↑" if diff_percent > 0 else "↓"
                        
                        send_telegram_message(
                            f"🔄 *订单已更新*\n\n"
                            f"ID: `{order_id}`\n"
                            f"旧价格: {order_price:,.2f}\n"
                            f"新价格: {ema_price:,.2f}\n"
                            f"变动: {direction} {abs(diff_percent):.2f}%"
                        )
                        
                        return f"📝 更新 {order_price:.2f}→{ema_price:.2f}"
                    
                    except Exception as create_err:
                        error_msg = str(create_err)
                        print(f"   ❌ 创建失败: {error_msg}")
                        
                        if not notified:
                            send_telegram_message(
                                f"⚠️ *订单更新失败*\n\n"
                                f"ID: `{order_id}`\n"
                                f"原因: {error_msg[:150]}\n"
                                f"请手动检查"
                            )
                            OrderManager.set_notified(order_id, True)
                        
                        OrderManager.update_binance_order_id(order_id, None)
                        return f"❌ 创建失败"
                else:
                    if notified:
                        OrderManager.set_notified(order_id, False)
                    return f"✓ 差异{price_diff*100:.2f}%"
            
            else:
                # 订单不存在
                if binance_order_id is not None:
                    order_status = self.client.get_order_status(symbol, binance_order_id)
                    
                    if order_status and order_status.get('status') == 'FILLED':
                        avg_price = float(order_status.get('avgPrice', 0))
                        send_telegram_message(
                            f"🎉 *订单已成交!*\n\n"
                            f"ID: `{order_id}`\n"
                            f"成交价: `{avg_price:,.4f}`"
                        )
                        OrderManager.remove_order(order_id)
                        return "🎉 已成交"
                    
                    print(f"📌 订单不存在，重新创建 {order_id}")
                
                # 创建新订单
                try:
                    new_order = self.client.create_order(
                        symbol, side, ema_price, quantity,
                        leverage=leverage, margin_type=margin_type
                    )
                    OrderManager.update_order(order_id,
                        binance_order_id=new_order['orderId'],
                        notified_error=False
                    )
                    
                    send_telegram_message(
                        f"📌 *新订单已创建*\n\n"
                        f"ID: `{order_id}`\n"
                        f"挂单价: `{ema_price:,.2f}`\n"
                        f"当前价: `{current_price:,.2f}`"
                    )
                    
                    return f"📌 新建 @ {ema_price:.2f}"
                
                except Exception as create_err:
                    error_msg = str(create_err)
                    print(f"❌ 创建失败: {error_msg}")
                    
                    if not notified:
                        send_telegram_message(
                            f"⚠️ *创建订单失败*\n\n"
                            f"ID: `{order_id}`\n"
                            f"原因: {error_msg[:150]}"
                        )
                        OrderManager.set_notified(order_id, True)
                    
                    return f"❌ 创建失败"
        
        except Exception as e:
            error_msg = str(e)
            print(f"❌ 处理 {order_id} 出错: {error_msg}")
            
            if not notified:
                send_telegram_message(
                    f"⚠️ *处理错误*\n\n"
                    f"ID: `{order_id}`\n"
                    f"错误: {error_msg[:150]}"
                )
                OrderManager.set_notified(order_id, True)
            
            return f"❌ 错误"
    
    def run(self, check_interval: int = 60):
        """主循环"""
        print("=" * 50)
        print("🚀 EMA追踪机器人启动")
        print(f"   检查间隔: {check_interval}秒")
        print("=" * 50)
        
        if TELEGRAM_TOKEN:
            send_telegram_message(f"🚀 *EMA追踪机器人已启动*\n\n每{check_interval}秒检查一次订单")
        
        while True:
            try:
                orders = OrderManager.load_orders()
                active_orders = [o for o in orders if o.get('status') == 'active']
                
                if not active_orders:
                    time.sleep(check_interval)
                    continue
                
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 处理 {len(active_orders)} 个订单")
                
                for order in active_orders:
                    result = self.process_order(order)
                    print(f"  {order['id']}: {result}")
                
            except KeyboardInterrupt:
                print("\n⏹️ 停止")
                if TELEGRAM_TOKEN:
                    send_telegram_message("⏹️ *机器人已停止*")
                break
            except Exception as e:
                print(f"❌ 错误: {e}")
            
            time.sleep(check_interval)


def print_help():
    print("""
╔═══════════════════════════════════════════════════╗
║         EMA追踪限价单机器人 - 使用说明             ║
╠═══════════════════════════════════════════════════╣
║  python ema_bot.py run      # 运行机器人          ║
║  python ema_bot.py list     # 查看订单            ║
║  python ema_bot.py remove <ID>  # 删除订单        ║
║  python ema_bot.py ema <币种> <周期>  # 查看EMA   ║
╚═══════════════════════════════════════════════════╝
""")


def cmd_list(args):
    orders = OrderManager.list_orders()
    if not orders:
        print("暂无订单")
        return
    
    print(f"\n{'ID':<35} {'方向':<5} {'数量':<10} {'杠杆':<6} {'模式':<8}")
    print("=" * 70)
    for o in orders:
        leverage = o.get('leverage', '-')
        margin = o.get('margin_type', '-')
        print(f"{o['id']:<35} {o['side']:<5} {o['quantity']:<10} {leverage}x    {margin}")
    print(f"\n共 {len(orders)} 个订单")


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
                print(f"✅ 已取消币安订单")
            except:
                pass
    
    if OrderManager.remove_order(order_id):
        print(f"✅ 已删除: {order_id}")
    else:
        print(f"❌ 订单不存在")


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
    
    print(f"\n{symbol} ({interval}) 当前: {current_price:.2f}")
    print("-" * 35)
    
    for ema in SUPPORTED_EMA:
        ema_value = client.calculate_ema(symbol, ema, interval)
        diff = ((current_price - ema_value) / ema_value) * 100
        print(f"  EMA{ema:<3}: {ema_value:>10.2f}  ({diff:+.2f}%)")


def main():
    if len(sys.argv) < 2:
        print_help()
        return
    
    cmd = sys.argv[1].lower()
    args = sys.argv[2:]
    
    if cmd == 'run':
        bot = EMATrailingBot()
        interval = int(args[0]) if args else 60
        bot.run(interval)
    elif cmd == 'list':
        cmd_list(args)
    elif cmd == 'remove':
        cmd_remove(args)
    elif cmd == 'ema':
        cmd_ema(args)
    else:
        print_help()


if __name__ == "__main__":
    main()