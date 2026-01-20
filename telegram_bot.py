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

from ema_bot import (
    BinanceClient, OrderManager, SUPPORTED_EMA, INTERVAL_MAP, 
    EMATrailingBot, MARKET_TYPES
)

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
        "🤖 *EMA追踪机器人* (支持现货+合约)\n\n"
        "📌 *绑定订单*\n"
        "/bind \\[币种] \\[周期] \\[EMA] - 绑定合约订单\n"
        "/bind\\_spot \\[币种] \\[周期] \\[EMA] - 绑定现货订单\n\n"
        "📊 *查询*\n"
        "/list - 查看所有订单\n"
        "/ema \\[币种] \\[周期] - 合约EMA\n"
        "/ema\\_spot \\[币种] \\[周期] - 现货EMA\n"
        "/price \\[币种] - 合约价格\n"
        "/price\\_spot \\[币种] - 现货价格\n"
        "/balance - 合约余额\n"
        "/balance\\_spot - 现货余额\n\n"
        "⚙️ *控制*\n"
        "/remove \\[ID] - 删除订单\n"
        "/status - 运行状态\n"
        "/start\\_bot - 启动追踪\n"
        "/stop\\_bot - 停止追踪",
        parse_mode='Markdown'
    )


# ==================== EMA 查询 ====================

async def cmd_ema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查询合约EMA"""
    await _cmd_ema_impl(update, context, 'futures')


async def cmd_ema_spot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查询现货EMA"""
    await _cmd_ema_impl(update, context, 'spot')


async def _cmd_ema_impl(update: Update, context: ContextTypes.DEFAULT_TYPE, market_type: str):
    if not is_authorized(update.effective_chat.id):
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("用法: /ema BTC 4h")
        return
    
    symbol = context.args[0].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'
    
    interval = INTERVAL_MAP.get(context.args[1].lower(), context.args[1])
    market_label = "🔵现货" if market_type == 'spot' else "🟡合约"
    
    try:
        price = binance_client.get_current_price(symbol, market_type)
        lines = [f"📊 {market_label} *{symbol}* ({interval}) = `{price:,.2f}`\n"]
        
        for ema in SUPPORTED_EMA:
            val = binance_client.calculate_ema(symbol, ema, interval, market_type)
            diff = ((price - val) / val) * 100
            icon = "🟢" if diff > 0 else "🔴"
            lines.append(f"EMA{ema}: `{val:,.2f}` {icon} {diff:+.2f}%")
        
        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


# ==================== 价格查询 ====================

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查询合约价格"""
    await _cmd_price_impl(update, context, 'futures')


async def cmd_price_spot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查询现货价格"""
    await _cmd_price_impl(update, context, 'spot')


async def _cmd_price_impl(update: Update, context: ContextTypes.DEFAULT_TYPE, market_type: str):
    if not is_authorized(update.effective_chat.id):
        return
    
    if len(context.args) < 1:
        await update.message.reply_text("用法: /price BTC")
        return
    
    symbol = context.args[0].upper()
    if not symbol.endswith('USDT'):
        symbol += 'USDT'
    
    market_label = "🔵现货" if market_type == 'spot' else "🟡合约"
    
    try:
        price = binance_client.get_current_price(symbol, market_type)
        await update.message.reply_text(f"💰 {market_label} {symbol}: `{price:,.2f}`", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


# ==================== 余额查询 ====================

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查询合约余额"""
    await _cmd_balance_impl(update, context, 'futures')


async def cmd_balance_spot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查询现货余额"""
    await _cmd_balance_impl(update, context, 'spot')


async def _cmd_balance_impl(update: Update, context: ContextTypes.DEFAULT_TYPE, market_type: str):
    if not is_authorized(update.effective_chat.id):
        return
    
    market_label = "🔵现货" if market_type == 'spot' else "🟡合约"
    
    try:
        balances = binance_client.get_account_balance(market_type)
        
        if not balances:
            await update.message.reply_text(f"💰 {market_label}余额: 无")
            return
        
        lines = [f"💰 *{market_label}余额*\n"]
        for asset, amount in balances.items():
            if amount > 0.0001:
                lines.append(f"`{asset}`: {amount:,.4f}")
        
        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


# ==================== 绑定订单 ====================

async def cmd_bind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """绑定合约订单"""
    await _cmd_bind_impl(update, context, 'futures')


async def cmd_bind_spot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """绑定现货订单"""
    await _cmd_bind_impl(update, context, 'spot')


async def _cmd_bind_impl(update: Update, context: ContextTypes.DEFAULT_TYPE, market_type: str):
    """绑定币安已有订单"""
    if not is_authorized(update.effective_chat.id):
        return
    
    market_label = "现货" if market_type == 'spot' else "合约"
    cmd_name = "bind_spot" if market_type == 'spot' else "bind"
    
    if len(context.args) < 3:
        await update.message.reply_text(
            f"用法: `/{cmd_name} 币种 周期 EMA`\n例: `/{cmd_name} BTC 4h 21`", 
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
            await update.message.reply_text(f"❌ EMA须为 {SUPPORTED_EMA}")
            return
    except:
        await update.message.reply_text("❌ EMA须为数字")
        return
    
    try:
        open_orders = binance_client.get_open_orders(symbol, market_type)
        
        if not open_orders:
            await update.message.reply_text(f"❌ 未找到 {symbol} {market_label}挂单")
            return
        
        if len(open_orders) == 1:
            await bind_order(update, open_orders[0], symbol, interval, ema, market_type)
        else:
            keyboard = []
            for o in open_orders:
                icon = "🟢" if o['side'] == 'BUY' else "🔴"
                ps = o.get('positionSide', '')
                ps_text = f" {ps}" if ps and ps != 'BOTH' else ""
                text = f"{icon} {o['side']}{ps_text} @ {float(o['price']):,.2f}"
                callback_data = f"bind_{market_type}_{o['orderId']}_{symbol}_{interval}_{ema}"
                keyboard.append([InlineKeyboardButton(text, callback_data=callback_data)])
            
            await update.message.reply_text(
                f"选择要绑定的{market_label}订单:", 
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def bind_order(update, binance_order: dict, symbol: str, interval: str, ema: int, market_type: str):
    """执行绑定"""
    side = binance_order['side']
    quantity = float(binance_order['origQty'])
    order_id = binance_order['orderId']
    price = float(binance_order['price'])
    
    market_label = "现货" if market_type == 'spot' else "合约"
    market_prefix = "SPOT" if market_type == 'spot' else "FUT"
    
    # 合约特有参数
    position_side = None
    leverage = None
    margin_type = None
    
    if market_type == 'futures':
        position_side = binance_order.get('positionSide', 'BOTH')
        try:
            leverage = binance_client.get_leverage(symbol)
            margin_type = binance_client.get_margin_type(symbol)
        except:
            leverage = None
            margin_type = None
    
    tracking_id = f"{market_prefix}_{symbol}_{interval}_EMA{ema}_{side}"
    
    # 检查已存在
    orders = OrderManager.load_orders()
    exists = False
    for o in orders:
        if o['id'] == tracking_id:
            exists = True
            update_params = {
                'binance_order_id': order_id,
                'quantity': quantity,
                'notified_error': False
            }
            if market_type == 'futures':
                update_params['leverage'] = leverage
                update_params['margin_type'] = margin_type
                update_params['position_side'] = position_side
            OrderManager.update_order(tracking_id, **update_params)
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
            'market_type': market_type,
            'leverage': leverage,
            'margin_type': margin_type,
            'position_side': position_side,
            'notified_error': False
        }
        orders.append(new_order)
        OrderManager.save_orders(orders)
    
    ema_price = binance_client.calculate_ema(symbol, ema, interval, market_type)
    
    # 构建消息
    market_icon = "🔵" if market_type == 'spot' else "🟡"
    msg_lines = [
        f"✅ *{market_icon}{market_label}绑定成功!*\n",
        f"ID: `{tracking_id}`",
        f"方向: {side}",
    ]
    
    if market_type == 'futures' and position_side:
        msg_lines.append(f"持仓: {position_side}")
    
    msg_lines.extend([
        f"价格: {price:,.2f}",
        f"数量: {quantity}",
    ])
    
    if market_type == 'futures':
        msg_lines.append(f"杠杆: {leverage}x | {margin_type}")
    
    msg_lines.extend([
        f"EMA{ema}: `{ema_price:,.2f}`",
        "",
        "发送 /start\\_bot 启动追踪"
    ])
    
    msg = "\n".join(msg_lines)
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg, parse_mode='Markdown')


async def bind_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not query.data.startswith("bind_"):
        return
    
    # bind_{market_type}_{order_id}_{symbol}_{interval}_{ema}
    parts = query.data.split("_")
    market_type = parts[1]
    order_id = int(parts[2])
    symbol = parts[3]
    interval = parts[4]
    ema = int(parts[5])
    
    try:
        open_orders = binance_client.get_open_orders(symbol, market_type)
        target = None
        for o in open_orders:
            if o['orderId'] == order_id:
                target = o
                break
        
        if not target:
            await query.edit_message_text("❌ 订单不存在或已成交")
            return
        
        await bind_order(update, target, symbol, interval, ema, market_type)
    except Exception as e:
        await query.edit_message_text(f"❌ {e}")


# ==================== 订单管理 ====================

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return
    
    orders = OrderManager.list_orders()
    
    if not orders:
        await update.message.reply_text("📭 暂无订单")
        return
    
    lines = ["📋 *订单列表*\n"]
    
    # 分类显示
    spot_orders = [o for o in orders if o.get('market_type') == 'spot']
    futures_orders = [o for o in orders if o.get('market_type', 'futures') == 'futures']
    
    if spot_orders:
        lines.append("🔵 *现货*")
        for o in spot_orders:
            icon = "📈" if o['side'] == 'BUY' else "📉"
            lines.append(f"{icon} `{o['id']}`")
            lines.append(f"   {o['quantity']}")
        lines.append("")
    
    if futures_orders:
        lines.append("🟡 *合约*")
        for o in futures_orders:
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
            await update.message.reply_text("📭 暂无订单")
            return
        
        keyboard = []
        for o in orders:
            market_icon = "🔵" if o.get('market_type') == 'spot' else "🟡"
            keyboard.append([InlineKeyboardButton(
                f"❌ {market_icon} {o['id']}", 
                callback_data=f"rm_{o['id']}"
            )])
        await update.message.reply_text("选择要删除的订单:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    await do_remove(update, context.args[0])


async def do_remove(update, order_id: str):
    orders = OrderManager.load_orders()
    for o in orders:
        if o['id'] == order_id and o.get('binance_order_id'):
            try:
                market_type = o.get('market_type', 'futures')
                binance_client.cancel_order(o['symbol'], o['binance_order_id'], market_type)
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


# ==================== 机器人控制 ====================

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return
    
    global bot_running
    orders = OrderManager.list_orders()
    active = [o for o in orders if o.get('status') == 'active']
    
    spot_count = len([o for o in active if o.get('market_type') == 'spot'])
    fut_count = len(active) - spot_count
    
    status = "🟢 运行中" if bot_running else "🔴 停止"
    await update.message.reply_text(
        f"{status}\n"
        f"🔵 现货: {spot_count}\n"
        f"🟡 合约: {fut_count}"
    )


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
                market_icon = "🔵" if order.get('market_type') == 'spot' else "🟡"
                logger.info(f"{market_icon} {order['id']}: {result}")
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
    await update.message.reply_text("🚀 已启动 (支持现货+合约)")


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
        orders = OrderManager.load_orders()
        active = [o for o in orders if o.get('status') == 'active']
        spot_count = len([o for o in active if o.get('market_type') == 'spot'])
        fut_count = len(active) - spot_count
        
        await application.bot.send_message(
            chat_id=AUTHORIZED_CHAT_ID,
            text=f"🚀 *EMA追踪机器人已自动启动*\n\n"
                 f"支持现货+合约\n"
                 f"🔵 现货: {spot_count}\n"
                 f"🟡 合约: {fut_count}\n\n"
                 f"每60秒检查一次订单",
            parse_mode='Markdown'
        )
    except:
        pass


def main():
    print("🚀 启动机器人 (支持现货+合约)...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.post_init = post_init
    
    # 基础命令
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    
    # EMA 查询
    application.add_handler(CommandHandler("ema", cmd_ema))
    application.add_handler(CommandHandler("ema_spot", cmd_ema_spot))
    
    # 价格查询
    application.add_handler(CommandHandler("price", cmd_price))
    application.add_handler(CommandHandler("price_spot", cmd_price_spot))
    
    # 余额查询
    application.add_handler(CommandHandler("balance", cmd_balance))
    application.add_handler(CommandHandler("balance_spot", cmd_balance_spot))
    
    # 绑定订单
    application.add_handler(CommandHandler("bind", cmd_bind))
    application.add_handler(CommandHandler("bind_spot", cmd_bind_spot))
    
    # 订单管理
    application.add_handler(CommandHandler("list", cmd_list))
    application.add_handler(CommandHandler("remove", cmd_remove))
    
    # 机器人控制
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("start_bot", cmd_start_bot))
    application.add_handler(CommandHandler("stop_bot", cmd_stop_bot))
    
    # 回调处理
    application.add_handler(CallbackQueryHandler(bind_callback, pattern="^bind_"))
    application.add_handler(CallbackQueryHandler(remove_callback, pattern="^rm_"))
    
    print("✅ 已启动")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()