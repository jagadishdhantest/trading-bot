from flask import Flask, request, jsonify
from dhan_trader import DhanTrader
from telegram_notifier import TelegramNotifier
import os, json, logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__)
trader   = DhanTrader()
notifier = TelegramNotifier()

# ── Daily loss tracker ───────────────────────────────────
daily_pnl        = 0.0
MAX_DAILY_LOSS   = float(os.getenv("MAX_DAILY_LOSS", -2000))   # ₹ — stop all trading if hit
CAPITAL          = float(os.getenv("CAPITAL", 100000))          # Your total capital in ₹
RISK_PCT         = 5.0                                           # 5% per trade
trade_log        = []

def reset_daily_pnl():
    global daily_pnl, trade_log
    today = datetime.now().strftime("%Y-%m-%d")
    if trade_log and trade_log[-1].get("date") != today:
        daily_pnl  = 0.0
        trade_log  = []

# ── Health check ─────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "🟢 Bot is running", "daily_pnl": daily_pnl}), 200

# ── Main webhook endpoint ─────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    global daily_pnl

    reset_daily_pnl()

    # ── Safety: stop trading if daily loss exceeded ───────
    if daily_pnl <= MAX_DAILY_LOSS:
        msg = f"🛑 Daily loss limit hit (₹{daily_pnl:.2f}). No more trades today."
        logging.warning(msg)
        notifier.send(msg)
        return jsonify({"status": "blocked", "reason": "daily loss limit"}), 200

    # ── Parse incoming alert from TradingView ─────────────
    try:
        data = request.get_json(force=True)
        logging.info(f"Incoming alert: {data}")
    except Exception as e:
        return jsonify({"error": f"Invalid JSON: {str(e)}"}), 400

    # Expected payload from TradingView:
    # {
    #   "action"  : "BUY" | "SELL",
    #   "symbol"  : "TRIVENI",
    #   "exchange": "NSE",
    #   "price"   : 564.50,       ← used for limit sell; ignored for market buy
    #   "sl"      : 557.70,
    #   "target1" : 573.00,
    #   "target2" : 581.00,
    #   "secret"  : "YOUR_WEBHOOK_SECRET"
    # }

    # ── Validate webhook secret ───────────────────────────
    if data.get("secret") != os.getenv("WEBHOOK_SECRET", "changeme123"):
        return jsonify({"error": "Unauthorized"}), 403

    action   = data.get("action", "").upper()
    symbol   = data.get("symbol", "")
    exchange = data.get("exchange", "NSE")
    price    = float(data.get("price", 0))
    sl       = float(data.get("sl", 0))
    target1  = float(data.get("target1", 0))
    target2  = float(data.get("target2", 0))

    if action not in ("BUY", "SELL") or not symbol:
        return jsonify({"error": "Invalid action or symbol"}), 400

    # ── Calculate quantity (5% of capital) ───────────────
    trade_amount = CAPITAL * (RISK_PCT / 100)          # ₹5,000 per trade
    quantity     = max(1, int(trade_amount / price))   # shares to buy/sell

    logging.info(f"Trade: {action} {quantity} x {symbol} @ ₹{price}")

    # ── Place order via Dhan ──────────────────────────────
    try:
        result = trader.place_order(
            action   = action,
            symbol   = symbol,
            exchange = exchange,
            quantity = quantity,
            price    = price,
            sl       = sl,
            target1  = target1,
            target2  = target2,
        )
        order_id = result.get("orderId", "N/A")
        status   = result.get("orderStatus", "PLACED")

        # ── Log trade ─────────────────────────────────────
        trade_record = {
            "date"    : datetime.now().strftime("%Y-%m-%d"),
            "time"    : datetime.now().strftime("%H:%M:%S"),
            "action"  : action,
            "symbol"  : symbol,
            "qty"     : quantity,
            "price"   : price,
            "sl"      : sl,
            "target1" : target1,
            "order_id": order_id,
            "status"  : status,
        }
        trade_log.append(trade_record)

        # ── Send Telegram notification ────────────────────
        emoji = "🟢" if action == "BUY" else "🔴"
        msg = (
            f"{emoji} *{action} ORDER PLACED*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📌 Stock   : `{symbol}`\n"
            f"💰 Price   : ₹{price}\n"
            f"📦 Qty     : {quantity} shares\n"
            f"💵 Value   : ₹{quantity * price:,.2f}\n"
            f"🛑 SL      : ₹{sl}\n"
            f"🎯 Target1 : ₹{target1}\n"
            f"🎯 Target2 : ₹{target2}\n"
            f"🆔 OrderID : {order_id}\n"
            f"⏰ Time    : {datetime.now().strftime('%H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📊 Daily PnL: ₹{daily_pnl:.2f}"
        )
        notifier.send(msg)

        return jsonify({"status": "success", "order_id": order_id}), 200

    except Exception as e:
        err_msg = f"❌ Order FAILED: {symbol} {action}\nError: {str(e)}"
        logging.error(err_msg)
        notifier.send(err_msg)
        return jsonify({"error": str(e)}), 500


# ── PnL update endpoint (call from TradingView exit alerts) ──
@app.route("/pnl", methods=["POST"])
def update_pnl():
    global daily_pnl
    data    = request.get_json(force=True)
    pnl     = float(data.get("pnl", 0))
    daily_pnl += pnl
    symbol  = data.get("symbol", "")
    action  = data.get("action", "EXIT")

    emoji = "✅" if pnl >= 0 else "❌"
    msg = (
        f"{emoji} *TRADE CLOSED*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📌 Stock  : `{symbol}`\n"
        f"💰 PnL    : ₹{pnl:+.2f}\n"
        f"📊 Daily  : ₹{daily_pnl:.2f}\n"
        f"⏰ Time   : {datetime.now().strftime('%H:%M:%S')}"
    )
    notifier.send(msg)
    return jsonify({"daily_pnl": daily_pnl}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
