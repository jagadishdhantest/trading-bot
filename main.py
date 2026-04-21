from flask import Flask, request, jsonify
from dhan_trader import DhanTrader
from telegram_notifier import TelegramNotifier
from market_scanner import MarketScanner
import os, logging, threading
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app     = Flask(__name__)
trader  = DhanTrader()
notifier= TelegramNotifier()
scanner = MarketScanner(trader, notifier)

# ── State ────────────────────────────────────────────────
state = {
    "daily_pnl"    : 0.0,
    "trade_count"  : 0,
    "active_trades": {},
    "last_scan"    : None,
    "scan_running" : False,
}

MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", -2000))
MAX_TRADES     = int(os.getenv("MAX_TRADES_PER_DAY", 3))
CAPITAL        = float(os.getenv("CAPITAL", 100000))
RISK_PCT       = 5.0

IST = timezone(timedelta(hours=5, minutes=30))

def ist_now():
    return datetime.now(IST)

def is_market_open():
    now  = ist_now()
    # Skip weekends
    if now.weekday() >= 5:
        return False
    t = now.time()
    from datetime import time
    return time(9, 15) <= t <= time(15, 10)

def reset_daily_state():
    """Reset at start of each trading day"""
    today = ist_now().strftime("%Y-%m-%d")
    if state.get("trade_date") != today:
        state["daily_pnl"]    = 0.0
        state["trade_count"]  = 0
        state["active_trades"]= {}
        state["trade_date"]   = today
        logging.info(f"🔄 Daily state reset for {today}")

# ── Scheduler — runs every 15 minutes ────────────────────
def run_scanner():
    """Background thread that scans market every 15 minutes"""
    import time
    logging.info("🤖 Scanner thread started")

    while True:
        try:
            reset_daily_state()

            if not is_market_open():
                logging.info("💤 Market closed — scanner sleeping")
                time.sleep(60)
                continue

            if state["scan_running"]:
                time.sleep(10)
                continue

            if state["daily_pnl"] <= MAX_DAILY_LOSS:
                logging.warning(f"🛑 Daily loss limit hit: ₹{state['daily_pnl']:.2f}")
                time.sleep(300)
                continue

            if state["trade_count"] >= MAX_TRADES:
                logging.info(f"✅ Max trades ({MAX_TRADES}) done for today")
                time.sleep(300)
                continue

            # ── Run the scan ──────────────────────────────
            state["scan_running"] = True
            state["last_scan"]    = ist_now().isoformat()

            logging.info("🔍 Starting market scan...")
            results = scanner.scan_and_trade(
                state        = state,
                capital      = CAPITAL,
                risk_pct     = RISK_PCT,
                max_trades   = MAX_TRADES,
            )
            logging.info(f"Scan complete: {results}")

        except Exception as e:
            logging.error(f"Scanner error: {e}")
            notifier.send(f"⚠️ Scanner error: {str(e)}")
        finally:
            state["scan_running"] = False

        # Wait 15 minutes before next scan
        time.sleep(900)


# ── Start scanner in background on boot ──────────────────
scanner_thread = threading.Thread(target=run_scanner, daemon=True)
scanner_thread.start()
logging.info("✅ Background scanner started")


# ── Health check endpoint ─────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    now = ist_now()
    return jsonify({
        "status"        : "🟢 Bot is running",
        "time_ist"      : now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "market_open"   : is_market_open(),
        "daily_pnl"     : state["daily_pnl"],
        "trade_count"   : state["trade_count"],
        "max_trades"    : MAX_TRADES,
        "active_trades" : list(state["active_trades"].keys()),
        "last_scan"     : state.get("last_scan"),
        "scan_running"  : state["scan_running"],
    }), 200


# ── Manual trigger endpoint (for testing) ────────────────
@app.route("/scan-now", methods=["POST"])
def scan_now():
    secret = request.json.get("secret") if request.json else ""
    if secret != os.getenv("WEBHOOK_SECRET", "changeme123"):
        return jsonify({"error": "Unauthorized"}), 403

    if state["scan_running"]:
        return jsonify({"status": "scan already running"}), 200

    def run():
        state["scan_running"] = True
        try:
            scanner.scan_and_trade(state=state, capital=CAPITAL,
                                   risk_pct=RISK_PCT, max_trades=MAX_TRADES)
        finally:
            state["scan_running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "scan started"}), 200


# ── Exit trade endpoint ───────────────────────────────────
@app.route("/exit", methods=["POST"])
def exit_trade():
    data   = request.get_json(force=True)
    symbol = data.get("symbol", "").upper()
    pnl    = float(data.get("pnl", 0))

    state["daily_pnl"] += pnl
    if symbol in state["active_trades"]:
        del state["active_trades"][symbol]

    emoji = "✅" if pnl >= 0 else "❌"
    notifier.send(
        f"{emoji} *TRADE CLOSED: {symbol}*\n"
        f"💰 PnL   : ₹{pnl:+.2f}\n"
        f"📊 Daily : ₹{state['daily_pnl']:.2f}"
    )
    return jsonify({"daily_pnl": state["daily_pnl"]}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
