import os
import sys
import json
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from ema_bot import BinanceClient, OrderManager, SUPPORTED_EMA, INTERVAL_MAP, EMATrailingBot

load_dotenv()

# 日志配置
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 配置
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

if not TELEGRAM_TOKEN:
    print("❌ 错误: 请在 .env 文件中设置 TELEGRAM_TOKEN")
    print("   获取方式: Telegram 搜索 @BotFather，发送 /newbot")
    sys.exit(1)

if not TELEGRAM_CHAT_ID:
    print("❌ 错误: 请在 .env 文件中设置 TELEGRAM_CHAT_ID")
    print("   获取方式: Telegram 搜索 @userinfobot，发送任意消息")
    sys.exit(1)

AUTHORIZED_CHAT_ID = int(TELEGRAM_CHAT_ID)

# 币安客户端
binance_client = BinanceClient()

# 追踪机器人实例
trailing_bot = None
bot_running = False


def is_authorized(chat_id: int) -> bool:
    """验证用户权限"""
    return chat_id == AUTHORIZED_CHAT_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始命令"""
    if not is_authorized(update.effective_chat.id):
        await update.message.reply_text("⛔ 未授权访问")
        return
    
    welcome_text = """
🤖 *EMA 合约追踪机器人*

*核心功能：*
绑定你已经在币安合约(Futures)下的限价单，机器人会自动修改订单价格，使其始终保持在指定的 EMA 均线上。

*📊 查询指令*
/price [币种]  - 查询当前合约价格 (例如: /price BTC)
/ema [币种] [周期] [EMA值] - 计算EMA价格 (例如: /ema BTC 4h 21)
/balance - 查看合约账户可用 USDT 余额

*🔗 绑定/追踪指令 (核心)*
/bind [币种] [周期] [EMA值]
👉 *作用*：让机器人接管你已经在币安下的限价单。
👉 *示例*：/bind ZEC 4h 21
(注意：请先在币安APP手动下一个限价单，再运行此命令)

*⚙️ 管理指令*
/list - 查看正在追踪的所有任务
/remove [订单ID] - 停止追踪 (例如: /remove ZECUSDT\\_4h\\_EMA21\\_BUY)
/status - 查看机器人运行状态

*⚠️ 注意事项*
1. 请确保你的 API 开启了合约交易权限。
2. 机器人只追踪挂单，不会自动开新仓。
3. 追踪期间请勿在 APP 手动修改该订单，可能会导致冲突。

💡 发送 /start\\_bot 启动自动追踪，/stop\\_bot 停止。
"""
    await update.message.reply_text(welcome_text, parse_mode='Markdown')


async def cmd_ema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看EMA值"""
    if not is_authorized(update.effective_chat.id):
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("用法: /ema BTC 4h")
        return
    
    symbol = context.args[0].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'
    
    interval = INTERVAL_MAP.get(context.args[1].lower(), context.args[1])
    
    try:
        current_price = binance_client.get_current_price(symbol)
        
        lines = [f"📊 *{symbol}* ({interval})", f"当前价格: `{current_price:,.2f}`", ""]
        
        for ema in SUPPORTED_EMA:
            ema_value = binance_client.calculate_ema(symbol, ema, interval)
            diff = ((current_price - ema_value) / ema_value) * 100
            direction = "🟢" if diff > 0 else "🔴"
            lines.append(f"EMA{ema}: `{ema_value:,.2f}` {direction} {abs(diff):.2f}%")
        
        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
    
    except Exception as e:
        await update.message.reply_text(f"❌ 错误: {e}")


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看当前价格"""
    if not is_authorized(update.effective_chat.id):
        return
    
    if len(context.args) < 1:
        await update.message.reply_text("用法: /price BTC")
        return
    
    symbol = context.args[0].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'
    
    try:
        price = binance_client.get_current_price(symbol)
        await update.message.reply_text(f"💰 {symbol}: `{price:,.2f}`", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ 错误: {e}")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看账户余额"""
    if not is_authorized(update.effective_chat.id):
        return
    
    try:
        balances = binance_client.get_account_balance()
        
        if not balances:
            await update.message.reply_text("💰 账户余额为空")
            return
        
        lines = ["💰 *账户余额*", ""]
        for asset, amount in sorted(balances.items()):
            if amount > 0.0001:
                lines.append(f"{asset}: `{amount:,.4f}`")
        
        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
    
    except Exception as e:
        await update.message.reply_text(f"❌ 错误: {e}")


async def cmd_bind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """绑定币安已有订单"""
    if not is_authorized(update.effective_chat.id):
        return
    
    if len(context.args) < 3:
        await update.message.reply_text(
            "📖 *绑定已有订单*\n\n"
            "用法: `/bind 币种 周期 EMA`\n\n"
            "示例: `/bind BTC 4h 21`\n\n"
            "前提: 你已在币安APP下了该币种的限价单",
            parse_mode='Markdown'
        )
        return
    
    symbol = context.args[0].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'
    
    interval = INTERVAL_MAP.get(context.args[1].lower(), context.args[1])
    
    try:
        ema = int(context.args[2])
        if ema not in SUPPORTED_EMA:
            await update.message.reply_text(f"❌ EMA必须是 {SUPPORTED_EMA} 之一")
            return
    except ValueError:
        await update.message.reply_text("❌ EMA必须是数字")
        return
    
    try:
        open_orders = binance_client.get_open_orders(symbol)
        
        if not open_orders:
            await update.message.reply_text(
                f"❌ 未找到 {symbol} 的挂单\n\n"
                f"请先在币安APP下一个限价单"
            )
            return
        
        if len(open_orders) == 1:
            order = open_orders[0]
            await bind_order_to_tracking(update, context, order, symbol, interval, ema)
        else:
            keyboard = []
            for o in open_orders:
                side_icon = "🟢" if o['side'] == 'BUY' else "🔴"
                btn_text = f"{side_icon} {o['side']} | {float(o['price']):,.2f} | 数量:{o['origQty']}"
                callback_data = f"bindorder_{o['orderId']}_{symbol}_{interval}_{ema}"
                keyboard.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"📋 找到 {len(open_orders)} 个 {symbol} 挂单\n选择要绑定的订单:",
                reply_markup=reply_markup
            )
    
    except Exception as e:
        await update.message.reply_text(f"❌ 查询失败: {e}")


async def bind_order_to_tracking(update, context, binance_order: dict, symbol: str, interval: str, ema: int):
    """将币安订单绑定到追踪系统"""
    side = binance_order['side']
    quantity = float(binance_order['origQty'])
    order_id = binance_order['orderId']
    price = float(binance_order['price'])
    
    tracking_id = f"{symbol}_{interval}_EMA{ema}_{side}"
    
    orders = OrderManager.load_orders()
    for o in orders:
        if o['id'] == tracking_id:
            o['binance_order_id'] = order_id
            o['quantity'] = quantity
            OrderManager.save_orders(orders)
            
            msg = (
                f"🔄 *已更新绑定*\n\n"
                f"ID: `{tracking_id}`\n"
                f"币安订单: `{order_id}`\n"
                f"价格: {price:,.2f}\n"
                f"数量: {quantity}"
            )
            
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(msg, parse_mode='Markdown')
            else:
                await update.message.reply_text(msg, parse_mode='Markdown')
            return
    
    new_order = {
        'id': tracking_id,
        'symbol': symbol,
        'interval': interval,
        'ema': ema,
        'side': side,
        'quantity': quantity,
        'binance_order_id': order_id,
        'status': 'active',
        'created_at': datetime.now().isoformat(),
        'bound': True
    }
    
    orders.append(new_order)
    OrderManager.save_orders(orders)
    
    try:
        ema_price = binance_client.calculate_ema(symbol, ema, interval)
        ema_info = f"当前EMA{ema}: `{ema_price:,.2f}`"
    except:
        ema_info = ""
    
    msg = (
        f"✅ *绑定成功!*\n\n"
        f"ID: `{tracking_id}`\n"
        f"币安订单: `{order_id}`\n"
        f"方向: {side}\n"
        f"当前挂单价: {price:,.2f}\n"
        f"数量: {quantity}\n"
        f"{ema_info}\n\n"
        f"💡 发送 /start\\_bot 启动自动追踪"
    )
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg, parse_mode='Markdown')


async def bind_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理绑定订单的按钮回调"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if not data.startswith("bindorder_"):
        return
    
    parts = data.split("_")
    order_id = int(parts[1])
    symbol = parts[2]
    interval = parts[3]
    ema = int(parts[4])
    
    try:
        open_orders = binance_client.get_open_orders(symbol)
        target_order = None
        for o in open_orders:
            if o['orderId'] == order_id:
                target_order = o
                break
        
        if not target_order:
            await query.edit_message_text("❌ 订单已不存在，可能已成交或取消")
            return
        
        await bind_order_to_tracking(update, context, target_order, symbol, interval, ema)
    
    except Exception as e:
        await query.edit_message_text(f"❌ 绑定失败: {e}")


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """添加订单 - 交互式菜单"""
    if not is_authorized(update.effective_chat.id):
        return
    
    keyboard = [
        [
            InlineKeyboardButton("BTC", callback_data="add_BTC"),
            InlineKeyboardButton("ETH", callback_data="add_ETH"),
            InlineKeyboardButton("SOL", callback_data="add_SOL"),
        ],
        [
            InlineKeyboardButton("BNB", callback_data="add_BNB"),
            InlineKeyboardButton("XRP", callback_data="add_XRP"),
            InlineKeyboardButton("DOGE", callback_data="add_DOGE"),
        ],
        [InlineKeyboardButton("其他 (手动输入)", callback_data="add_OTHER")],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("选择交易对:", reply_markup=reply_markup)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理按钮回调"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("add_"):
        symbol = data.replace("add_", "")
        
        if symbol == "OTHER":
            await query.edit_message_text("请直接发送: `币种 周期 EMA 方向 数量`\n例如: `AVAX 4h 21 BUY 1`", parse_mode='Markdown')
            return
        
        context.user_data['add_symbol'] = symbol
        
        keyboard = [
            [
                InlineKeyboardButton("15m", callback_data=f"interval_{symbol}_15m"),
                InlineKeyboardButton("1h", callback_data=f"interval_{symbol}_1h"),
                InlineKeyboardButton("4h", callback_data=f"interval_{symbol}_4h"),
            ],
            [
                InlineKeyboardButton("1D", callback_data=f"interval_{symbol}_1d"),
                InlineKeyboardButton("1W", callback_data=f"interval_{symbol}_1w"),
                InlineKeyboardButton("1M", callback_data=f"interval_{symbol}_1M"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"📊 {symbol} - 选择时间周期:", reply_markup=reply_markup)
    
    elif data.startswith("interval_"):
        parts = data.split("_")
        symbol = parts[1]
        interval = parts[2]
        
        context.user_data['add_symbol'] = symbol
        context.user_data['add_interval'] = interval
        
        keyboard = [
            [
                InlineKeyboardButton("EMA21", callback_data=f"ema_{symbol}_{interval}_21"),
                InlineKeyboardButton("EMA55", callback_data=f"ema_{symbol}_{interval}_55"),
            ],
            [
                InlineKeyboardButton("EMA100", callback_data=f"ema_{symbol}_{interval}_100"),
                InlineKeyboardButton("EMA200", callback_data=f"ema_{symbol}_{interval}_200"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"📊 {symbol} ({interval}) - 选择EMA:", reply_markup=reply_markup)
    
    elif data.startswith("ema_"):
        parts = data.split("_")
        symbol = parts[1]
        interval = parts[2]
        ema = parts[3]
        
        context.user_data['add_symbol'] = symbol
        context.user_data['add_interval'] = interval
        context.user_data['add_ema'] = ema
        
        keyboard = [
            [
                InlineKeyboardButton("🟢 做多 (BUY)", callback_data=f"side_{symbol}_{interval}_{ema}_BUY"),
                InlineKeyboardButton("🔴 做空 (SELL)", callback_data=f"side_{symbol}_{interval}_{ema}_SELL"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"📊 {symbol} ({interval}) EMA{ema} - 选择方向:", reply_markup=reply_markup)
    
    elif data.startswith("side_"):
        parts = data.split("_")
        symbol = parts[1]
        interval = parts[2]
        ema = parts[3]
        side = parts[4]
        
        context.user_data['add_symbol'] = symbol
        context.user_data['add_interval'] = interval
        context.user_data['add_ema'] = ema
        context.user_data['add_side'] = side
        context.user_data['awaiting_quantity'] = True
        
        try:
            symbol_full = symbol + 'USDT'
            ema_price = binance_client.calculate_ema(symbol_full, int(ema), interval)
            current_price = binance_client.get_current_price(symbol_full)
            
            await query.edit_message_text(
                f"📊 *{symbol}* ({interval}) EMA{ema} {side}\n\n"
                f"当前价格: `{current_price:,.2f}`\n"
                f"EMA{ema}: `{ema_price:,.2f}`\n\n"
                f"请输入下单数量:",
                parse_mode='Markdown'
            )
        except Exception as e:
            await query.edit_message_text(f"请输入下单数量 (例如: 0.001):")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理文本消息"""
    if not is_authorized(update.effective_chat.id):
        return
    
    text = update.message.text.strip()
    
    if context.user_data.get('awaiting_quantity'):
        try:
            quantity = float(text)
            
            symbol = context.user_data['add_symbol']
            interval = context.user_data['add_interval']
            ema = int(context.user_data['add_ema'])
            side = context.user_data['add_side']
            
            order = OrderManager.add_order(symbol, interval, ema, side, quantity)
            
            await update.message.reply_text(
                f"✅ *订单添加成功!*\n\n"
                f"ID: `{order['id']}`\n"
                f"交易对: {order['symbol']}\n"
                f"周期: {order['interval']}\n"
                f"EMA: {order['ema']}\n"
                f"方向: {order['side']}\n"
                f"数量: {order['quantity']}",
                parse_mode='Markdown'
            )
            
            context.user_data.clear()
            
        except ValueError:
            await update.message.reply_text("❌ 请输入有效的数量")
        except Exception as e:
            await update.message.reply_text(f"❌ 添加失败: {e}")
            context.user_data.clear()
        
        return
    
    parts = text.split()
    if len(parts) == 5:
        try:
            symbol, interval, ema, side, quantity = parts
            ema = int(ema)
            quantity = float(quantity)
            
            order = OrderManager.add_order(symbol, interval, ema, side, quantity)
            
            await update.message.reply_text(
                f"✅ *快捷添加成功!*\n\n"
                f"ID: `{order['id']}`\n"
                f"交易对: {order['symbol']}\n"
                f"周期: {order['interval']}\n"
                f"EMA: {order['ema']}\n"
                f"方向: {order['side']}\n"
                f"数量: {order['quantity']}",
                parse_mode='Markdown'
            )
        except Exception as e:
            await update.message.reply_text(f"❌ 无法解析: {e}\n\n格式: `币种 周期 EMA 方向 数量`", parse_mode='Markdown')
    else:
        await update.message.reply_text("发送 /start 查看帮助")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """列出所有订单"""
    if not is_authorized(update.effective_chat.id):
        return
    
    orders = OrderManager.list_orders()
    
    if not orders:
        await update.message.reply_text("📭 暂无订单")
        return
    
    lines = ["📋 *当前订单*", ""]
    
    for o in orders:
        status_icon = "🟢" if o.get('status') == 'active' else "⏸️"
        side_icon = "📈" if o['side'] == 'BUY' else "📉"
        bound_icon = "🔗" if o.get('bound') else ""
        lines.append(f"{status_icon} `{o['id']}` {bound_icon}")
        lines.append(f"   {side_icon} {o['side']} | 数量: {o['quantity']}")
        if o.get('binance_order_id'):
            lines.append(f"   币安订单: `{o['binance_order_id']}`")
        lines.append("")
    
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """删除订单"""
    if not is_authorized(update.effective_chat.id):
        return
    
    if len(context.args) < 1:
        orders = OrderManager.list_orders()
        if not orders:
            await update.message.reply_text("📭 暂无订单")
            return
        
        keyboard = []
        for o in orders:
            keyboard.append([InlineKeyboardButton(
                f"❌ {o['id']}", 
                callback_data=f"remove_{o['id']}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("选择要删除的订单:", reply_markup=reply_markup)
        return
    
    order_id = context.args[0]
    await do_remove_order(update, order_id)


async def do_remove_order(update, order_id: str):
    """执行删除订单"""
    orders = OrderManager.load_orders()
    for o in orders:
        if o['id'] == order_id and o.get('binance_order_id'):
            try:
                binance_client.cancel_order(o['symbol'], o['binance_order_id'])
            except:
                pass
    
    if OrderManager.remove_order(order_id):
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(f"✅ 已删除: `{order_id}`", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"✅ 已删除: `{order_id}`", parse_mode='Markdown')
    else:
        text = f"❌ 订单不存在: {order_id}"
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)


async def remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理删除按钮回调"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("remove_"):
        order_id = query.data.replace("remove_", "")
        await do_remove_order(update, order_id)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看机器人状态"""
    if not is_authorized(update.effective_chat.id):
        return
    
    global bot_running
    
    orders = OrderManager.list_orders()
    active_orders = [o for o in orders if o.get('status') == 'active']
    bound_orders = [o for o in orders if o.get('bound')]
    
    status = "🟢 运行中" if bot_running else "🔴 已停止"
    
    await update.message.reply_text(
        f"*机器人状态*\n\n"
        f"状态: {status}\n"
        f"活跃订单: {len(active_orders)}\n"
        f"绑定订单: {len(bound_orders)}\n"
        f"总订单数: {len(orders)}",
        parse_mode='Markdown'
    )


async def run_trailing_bot(context: ContextTypes.DEFAULT_TYPE):
    """后台运行追踪任务"""
    global bot_running, trailing_bot
    
    if not bot_running:
        return
    
    trailing_bot = EMATrailingBot()
    
    try:
        orders = OrderManager.load_orders()
        active_orders = [o for o in orders if o.get('status') == 'active']
        
        if not active_orders:
            return
        
        for order in active_orders:
            try:
                result = trailing_bot.process_order(order)
                
                # 🔥 关键修改：记录所有处理结果
                logger.info(f"订单 {order['id']} 处理结果: {result}")
                
                # 如果包含重要信息，发送通知
                important_keywords = ["更新", "新建", "成交", "取消", "失效", "错误", "❌", "⚠️", "成功"]
                if any(keyword in result for keyword in important_keywords):
                    try:
                        await context.bot.send_message(
                            chat_id=AUTHORIZED_CHAT_ID,
                            text=f"📊 *订单处理*\n\nID: `{order['id']}`\n\n{result}",
                            parse_mode='Markdown'
                        )
                    except Exception as send_err:
                        logger.error(f"发送消息失败: {send_err}")
                    
            except Exception as e:
                error_msg = f"处理订单 {order['id']} 出错: {str(e)}"
                logger.error(error_msg)
                
                # 🔥 发送详细错误通知
                try:
                    await context.bot.send_message(
                        chat_id=AUTHORIZED_CHAT_ID,
                        text=f"⚠️ *订单处理异常*\n\n"
                             f"ID: `{order['id']}`\n"
                             f"错误: {str(e)[:300]}",
                        parse_mode='Markdown'
                    )
                except Exception as send_err:
                    logger.error(f"发送错误消息失败: {send_err}")
    
    except Exception as e:
        error_msg = f"追踪任务出错: {str(e)}"
        logger.error(error_msg)
        try:
            await context.bot.send_message(
                chat_id=AUTHORIZED_CHAT_ID,
                text=f"❌ *机器人任务异常*\n\n{str(e)[:300]}",
                parse_mode='Markdown'
            )
        except Exception as send_err:
            logger.error(f"发送错误消息失败: {send_err}")


async def cmd_start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """启动追踪机器人"""
    if not is_authorized(update.effective_chat.id):
        return
    
    global bot_running
    
    if bot_running:
        await update.message.reply_text("⚠️ 机器人已在运行中")
        return
    
    bot_running = True
    
    context.job_queue.run_repeating(
        run_trailing_bot,
        interval=60,
        first=5,
        name='trailing_job'
    )
    
    await update.message.reply_text("🚀 追踪机器人已启动!\n每60秒检查一次订单")


async def cmd_stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """停止追踪机器人"""
    if not is_authorized(update.effective_chat.id):
        return
    
    global bot_running
    
    if not bot_running:
        await update.message.reply_text("⚠️ 机器人未在运行")
        return
    
    bot_running = False
    
    current_jobs = context.job_queue.get_jobs_by_name('trailing_job')
    for job in current_jobs:
        job.schedule_removal()
    
    await update.message.reply_text("⏹️ 追踪机器人已停止")


async def post_init(application: Application):
    """机器人启动后自动执行"""
    global bot_running
    bot_running = True
    
    # 自动启动追踪任务
    application.job_queue.run_repeating(
        run_trailing_bot,
        interval=60,
        first=10,  # 启动后10秒开始
        name='trailing_job'
    )
    
    # 发送启动通知
    try:
        await application.bot.send_message(
            chat_id=AUTHORIZED_CHAT_ID,
            text="🚀 *EMA追踪机器人已自动启动*\n\n每60秒检查一次订单",
            parse_mode='Markdown'
        )
    except Exception as e:
        print(f"发送启动通知失败: {e}")
    
    print("✅ 追踪任务已自动启动")


def main():
    """主函数"""
    print("🚀 启动 Telegram 机器人...")
    print(f"✅ 配置加载成功，Chat ID: {AUTHORIZED_CHAT_ID}")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # 添加启动后自动执行的函数
    application.post_init = post_init
    
    # 命令处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("ema", cmd_ema))
    application.add_handler(CommandHandler("price", cmd_price))
    application.add_handler(CommandHandler("balance", cmd_balance))
    application.add_handler(CommandHandler("bind", cmd_bind))
    application.add_handler(CommandHandler("add", cmd_add))
    application.add_handler(CommandHandler("list", cmd_list))
    application.add_handler(CommandHandler("remove", cmd_remove))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("start_bot", cmd_start_bot))
    application.add_handler(CommandHandler("stop_bot", cmd_stop_bot))
    
    # 按钮回调处理器
    application.add_handler(CallbackQueryHandler(bind_callback, pattern="^bindorder_"))
    application.add_handler(CallbackQueryHandler(remove_callback, pattern="^remove_"))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # 文本消息处理器
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ Telegram 机器人已启动")
    print("📱 请在 Telegram 中与机器人对话")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()