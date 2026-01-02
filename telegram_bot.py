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

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    print("❌ 请配置 TELEGRAM_TOKEN 和 TELEGRAM_CHAT_ID")
    sys.exit(1)

AUTHORIZED_CHAT_ID = int(TELEGRAM_CHAT_ID)

binance_client = BinanceClient()
trailing_bot = None
bot_running = False


def is_authorized(chat_id: int) -> bool:
    return chat_id == AUTHORIZED_CHAT_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return
    
    await update.message.reply_text(
        "🤖 *EMA追踪机器人*\n\n"
        "/bind [币种] [周期] [EMA] - 绑定订单\n"
        "/list - 查看订单\n"
        "/remove [ID] - 删除\n"
        "/price [币种] - 价格\n"
        "/ema [币种] [周期] - EMA值\n"
        "/status - 状态",
        parse_mode='Markdown'
    )


async def cmd_ema(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        price = binance_client.get_current_price(symbol)
        lines = [f"📊 *{symbol}* ({interval}) = `{price:,.2f}`\n"]
        
        for ema in SUPPORTED_EMA:
            val = binance_client.calculate_ema(symbol, ema, interval)
            diff = ((price - val) / val) * 100
            icon = "🟢" if diff > 0 else "🔴"
            lines.append(f"EMA{ema}: `{val:,.2f}` {icon} {diff:+.2f}%")
        
        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await update.message.reply_text(f"❌ {e}")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return
    
    try:
        balances = binance_client.get_account_balance()
        usdt = balances.get('USDT', 0)
        await update.message.reply_text(f"💰 余额: `{usdt:,.2f}` USDT", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def cmd_bind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """绑定币安已有订单"""
    if not is_authorized(update.effective_chat.id):
        return
    
    if len(context.args) < 3:
        await update.message.reply_text("用法: `/bind 币种 周期 EMA`\n例: `/bind BTC 4h 21`", parse_mode='Markdown')
        return
    
    symbol = context.args[0].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'
    
    interval = INTERVAL_MAP.get(context.args[1].lower(), context.args[1])
    
    try:
        ema = int(context.args[2])
        if ema not in SUPPORTED_EMA:
            await update.message.reply_text(f"❌ EMA须为 {SUPPORTED_EMA}")
            return
    except:
        await update.message.reply_text("❌ EMA须为数字")
        return
    
    try:
        open_orders = binance_client.get_open_orders(symbol)
        
        if not open_orders:
            await update.message.reply_text(f"❌ 未找到 {symbol} 挂单")
            return
        
        if len(open_orders) == 1:
            await bind_order(update, open_orders[0], symbol, interval, ema)
        else:
            keyboard = []
            for o in open_orders:
                icon = "🟢" if o['side'] == 'BUY' else "🔴"
                ps = o.get('positionSide', 'BOTH')
                text = f"{icon} {o['side']} {ps} @ {float(o['price']):,.2f}"
                keyboard.append([InlineKeyboardButton(text, callback_data=f"bind_{o['orderId']}_{symbol}_{interval}_{ema}")])
            
            await update.message.reply_text("选择订单:", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def bind_order(update, binance_order: dict, symbol: str, interval: str, ema: int):
    """执行绑定"""
    side = binance_order['side']
    quantity = float(binance_order['origQty'])
    order_id = binance_order['orderId']
    price = float(binance_order['price'])
    position_side = binance_order.get('positionSide', 'BOTH')  # 获取 positionSide
    
    # 获取杠杆和保证金模式
    try:
        leverage = binance_client.get_leverage(symbol)
        margin_type = binance_client.get_margin_type(symbol)
    except:
        leverage = None
        margin_type = None
    
    tracking_id = f"{symbol}_{interval}_EMA{ema}_{side}"
    
    # 检查已存在
    orders = OrderManager.load_orders()
    exists = False
    for o in orders:
        if o['id'] == tracking_id:
            exists = True
            OrderManager.update_order(tracking_id,
                binance_order_id=order_id,
                quantity=quantity,
                leverage=leverage,
                margin_type=margin_type,
                position_side=position_side,
                notified_error=False
            )
            break
    
    if not exists:
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
            'leverage': leverage,
            'margin_type': margin_type,
            'position_side': position_side,
            'notified_error': False
        }
        orders.append(new_order)
        OrderManager.save_orders(orders)
    
    ema_price = binance_client.calculate_ema(symbol, ema, interval)
    
    msg = (
        f"✅ *绑定成功!*\n\n"
        f"ID: `{tracking_id}`\n"
        f"方向: {side} ({position_side})\n"
        f"价格: {price:,.2f}\n"
        f"数量: {quantity}\n"
        f"杠杆: {leverage}x | {margin_type}\n"
        f"EMA{ema}: `{ema_price:,.2f}`\n\n"
        f"发送 /start\\_bot 启动追踪"
    )
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg, parse_mode='Markdown')


async def bind_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not query.data.startswith("bind_"):
        return
    
    parts = query.data.split("_")
    order_id = int(parts[1])
    symbol = parts[2]
    interval = parts[3]
    ema = int(parts[4])
    
    try:
        open_orders = binance_client.get_open_orders(symbol)
        target = None
        for o in open_orders:
            if o['orderId'] == order_id:
                target = o
                break
        
        if not target:
            await query.edit_message_text("❌ 订单不存在")
            return
        
        await bind_order(update, target, symbol, interval, ema)
    except Exception as e:
        await query.edit_message_text(f"❌ {e}")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return
    
    orders = OrderManager.list_orders()
    
    if not orders:
        await update.message.reply_text("📭 暂无订单")
        return
    
    lines = ["📋 *订单列表*\n"]
    for o in orders:
        icon = "📈" if o['side'] == 'BUY' else "📉"
        ps = o.get('position_side', '-')
        lv = o.get('leverage', '-')
        lines.append(f"{icon} `{o['id']}`")
        lines.append(f"   {o['quantity']} | {lv}x | {ps}")
    
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return
    
    if len(context.args) < 1:
        orders = OrderManager.list_orders()
        if not orders:
            await update.message.reply_text("📭 暂无")
            return
        
        keyboard = [[InlineKeyboardButton(f"❌ {o['id']}", callback_data=f"rm_{o['id']}")] for o in orders]
        await update.message.reply_text("选择删除:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    await do_remove(update, context.args[0])


async def do_remove(update, order_id: str):
    orders = OrderManager.load_orders()
    for o in orders:
        if o['id'] == order_id and o.get('binance_order_id'):
            try:
                binance_client.cancel_order(o['symbol'], o['binance_order_id'])
            except:
                pass
    
    success = OrderManager.remove_order(order_id)
    msg = f"✅ 已删除" if success else f"❌ 不存在"
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(msg)
    else:
        await update.message.reply_text(msg)


async def remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("rm_"):
        await do_remove(update, query.data[3:])


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return
    
    global bot_running
    orders = OrderManager.list_orders()
    active = len([o for o in orders if o.get('status') == 'active'])
    
    status = "🟢 运行中" if bot_running else "🔴 停止"
    await update.message.reply_text(f"{status} | 订单: {active}")


async def run_trailing_bot(context: ContextTypes.DEFAULT_TYPE):
    """后台追踪"""
    global bot_running, trailing_bot
    
    if not bot_running:
        return
    
    if not trailing_bot:
        trailing_bot = EMATrailingBot()
    
    try:
        orders = OrderManager.load_orders()
        active = [o for o in orders if o.get('status') == 'active']
        
        for order in active:
            try:
                result = trailing_bot.process_order(order)
                logger.info(f"{order['id']}: {result}")
            except Exception as e:
                logger.error(f"{order['id']}: {e}")
    except Exception as e:
        logger.error(f"错误: {e}")


async def cmd_start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return
    
    global bot_running
    
    if bot_running:
        await update.message.reply_text("⚠️ 已在运行")
        return
    
    bot_running = True
    context.job_queue.run_repeating(run_trailing_bot, interval=60, first=5, name='trailing')
    await update.message.reply_text("🚀 已启动")


async def cmd_stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return
    
    global bot_running
    
    if not bot_running:
        await update.message.reply_text("⚠️ 未运行")
        return
    
    bot_running = False
    for job in context.job_queue.get_jobs_by_name('trailing'):
        job.schedule_removal()
    
    await update.message.reply_text("⏹️ 已停止")


async def post_init(application: Application):
    global bot_running
    bot_running = True
    
    application.job_queue.run_repeating(run_trailing_bot, interval=60, first=10, name='trailing')
    
    try:
        await application.bot.send_message(
            chat_id=AUTHORIZED_CHAT_ID,
            text="🚀 *EMA追踪机器人已自动启动*\n\n每60秒检查一次订单",
            parse_mode='Markdown'
        )
    except:
        pass


def main():
    print("🚀 启动机器人...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.post_init = post_init
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("ema", cmd_ema))
    application.add_handler(CommandHandler("price", cmd_price))
    application.add_handler(CommandHandler("balance", cmd_balance))
    application.add_handler(CommandHandler("bind", cmd_bind))
    application.add_handler(CommandHandler("list", cmd_list))
    application.add_handler(CommandHandler("remove", cmd_remove))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("start_bot", cmd_start_bot))
    application.add_handler(CommandHandler("stop_bot", cmd_stop_bot))
    
    application.add_handler(CallbackQueryHandler(bind_callback, pattern="^bind_"))
    application.add_handler(CallbackQueryHandler(remove_callback, pattern="^rm_"))
    
    print("✅ 已启动")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()