import logging
import requests
import os
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

# ── Watchlist — top liquid NSE stocks ─────────────────────
# Bot will scan these every 15 mins and pick the best signal
WATCHLIST = [
    {"symbol": "RELIANCE",       "security_id": "2885"},
    {"symbol": "TCS",            "security_id": "11536"},
    {"symbol": "INFY",           "security_id": "1594"},
    {"symbol": "HDFCBANK",       "security_id": "1333"},
    {"symbol": "ICICIBANK",      "security_id": "4963"},
    {"symbol": "SBIN",           "security_id": "3045"},
    {"symbol": "AXISBANK",       "security_id": "5900"},
    {"symbol": "TATAMOTORS",     "security_id": "3456"},
    {"symbol": "BAJFINANCE",     "security_id": "317"},
    {"symbol": "WIPRO",          "security_id": "3787"},
    {"symbol": "SUNPHARMA",      "security_id": "3351"},
    {"symbol": "MARUTI",         "security_id": "10999"},
    {"symbol": "TITAN",          "security_id": "3506"},
    {"symbol": "ULTRACEMCO",     "security_id": "11532"},
    {"symbol": "NTPC",           "security_id": "11630"},
    {"symbol": "POWERGRID",      "security_id": "14977"},
    {"symbol": "ADANIENT",       "security_id": "25"},
    {"symbol": "ADANIPORTS",     "security_id": "15083"},
    {"symbol": "KOTAKBANK",      "security_id": "1922"},
    {"symbol": "LT",             "security_id": "11483"},
    {"symbol": "HINDALCO",       "security_id": "1363"},
    {"symbol": "JSWSTEEL",       "security_id": "11723"},
    {"symbol": "TATASTEEL",      "security_id": "3499"},
    {"symbol": "BHARTIARTL",     "security_id": "10604"},
    {"symbol": "HCLTECH",        "security_id": "1232"},
    {"symbol": "TRIVENI",        "security_id": "4506"},
    {"symbol": "TRANSFORMERSINDIA", "security_id": "10604"},
    {"symbol": "GROWW",          "security_id": "11915"},
]

class MarketScanner:
    def __init__(self, trader, notifier):
        self.trader   = trader
        self.notifier = notifier
        self.base_url = "https://api.dhan.co"

    # ── Main scan function ────────────────────────────────
    def scan_and_trade(self, state, capital, risk_pct, max_trades):
        signals    = []
        scan_time  = datetime.now(IST).strftime("%H:%M IST")

        logging.info(f"🔍 Scanning {len(WATCHLIST)} stocks at {scan_time}")

        for stock in WATCHLIST:
            symbol      = stock["symbol"]
            security_id = stock["security_id"]

            # Skip already active trades
            if symbol in state["active_trades"]:
                continue

            try:
                candles = self._fetch_candles(security_id, interval="15")
                if not candles or len(candles) < 30:
                    continue

                signal = self._analyze(symbol, security_id, candles)
                if signal:
                    signals.append(signal)
                    logging.info(f"✅ Signal: {symbol} | {signal['direction']} | Score: {signal['score']}")

            except Exception as e:
                logging.error(f"Error scanning {symbol}: {e}")
                continue

        if not signals:
            logging.info("No signals found this scan")
            return {"signals": 0}

        # Sort by score and pick best
        signals.sort(key=lambda x: x["score"], reverse=True)
        best = signals[0]

        # Minimum quality threshold
        if best["score"] < 60:
            logging.info(f"Best signal score {best['score']} below threshold 60 — no trade")
            self.notifier.send(
                f"🔍 *Scan at {scan_time}*\n"
                f"Found {len(signals)} signals but none scored above 60\n"
                f"Best: {best['symbol']} at {best['score']}/100"
            )
            return {"signals": len(signals), "traded": False}

        # ── Place the trade ───────────────────────────────
        symbol   = best["symbol"]
        price    = best["price"]
        quantity = max(1, int((capital * risk_pct / 100) / price))

        # Send full score report to Telegram
        report = "\n".join([f"  • {s['symbol']}: {s['score']}/100 ({s['direction']})" for s in signals[:5]])
        self.notifier.send(
            f"🧠 *CLAUDE SCANNER — {scan_time}*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📊 Stocks scanned : {len(WATCHLIST)}\n"
            f"📡 Signals found  : {len(signals)}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🏆 *BEST PICK: {symbol}*\n"
            f"📈 Direction : {best['direction']}\n"
            f"⭐ Score     : {best['score']}/100\n"
            f"💰 Price     : ₹{price}\n"
            f"📦 Qty       : {quantity} shares\n"
            f"🛑 SL        : ₹{best['sl']}\n"
            f"🎯 Target 1  : ₹{best['target1']}\n"
            f"🎯 Target 2  : ₹{best['target2']}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📋 Top signals:\n{report}"
        )

        try:
            result   = self.trader.place_order(
                action   = best["direction"],
                symbol   = symbol,
                exchange = "NSE",
                quantity = quantity,
                price    = price,
                sl       = best["sl"],
                target1  = best["target1"],
                target2  = best["target2"],
            )
            order_id = result.get("orderId", "N/A")

            state["active_trades"][symbol] = {
                "direction": best["direction"],
                "entry"    : price,
                "sl"       : best["sl"],
                "target1"  : best["target1"],
                "qty"      : quantity,
                "order_id" : order_id,
                "time"     : datetime.now(IST).isoformat(),
            }
            state["trade_count"] += 1

            self.notifier.send(f"✅ *ORDER PLACED*\n{symbol} {best['direction']} × {quantity} @ ₹{price}\n🆔 {order_id}")
            return {"signals": len(signals), "traded": True, "symbol": symbol}

        except Exception as e:
            self.notifier.send(f"❌ Order failed for {symbol}: {str(e)}")
            return {"signals": len(signals), "traded": False, "error": str(e)}


    # ── Fetch 15-min candles from Dhan ────────────────────
    def _fetch_candles(self, security_id: str, interval: str = "15") -> list:
        from datetime import date, timedelta
        today     = date.today()
        from_date = (today - timedelta(days=5)).strftime("%Y-%m-%d")
        to_date   = today.strftime("%Y-%m-%d")

        url = f"{self.base_url}/v2/charts/intraday"
        payload = {
            "securityId"     : security_id,
            "exchangeSegment": "NSE_EQ",
            "instrument"     : "EQUITY",
            "interval"       : interval,
            "fromDate"       : from_date,
            "toDate"         : to_date,
        }
        response = requests.post(
            url,
            headers = self.trader.headers,
            json    = payload,
            timeout = 10,
        )
        response.raise_for_status()
        data = response.json()

        # Dhan returns open, high, low, close, volume arrays
        opens   = data.get("open",   [])
        highs   = data.get("high",   [])
        lows    = data.get("low",    [])
        closes  = data.get("close",  [])
        volumes = data.get("volume", [])

        if not closes:
            return []

        candles = []
        for i in range(len(closes)):
            candles.append({
                "open"  : opens[i]   if i < len(opens)   else closes[i],
                "high"  : highs[i]   if i < len(highs)   else closes[i],
                "low"   : lows[i]    if i < len(lows)    else closes[i],
                "close" : closes[i],
                "volume": volumes[i] if i < len(volumes) else 0,
            })
        return candles


    # ── Analyze candles — EMA + scoring ──────────────────
    def _analyze(self, symbol: str, security_id: str, candles: list) -> dict:
        closes  = [c["close"]  for c in candles]
        volumes = [c["volume"] for c in candles]

        ema9  = self._ema(closes, 9)
        ema26 = self._ema(closes, 26)

        # Current and previous values
        curr_ema9   = ema9[-1]
        curr_ema26  = ema26[-1]
        prev_ema9   = ema9[-2]
        prev_ema26  = ema26[-2]
        curr_close  = closes[-1]
        curr_vol    = volumes[-1]
        avg_vol     = sum(volumes[-9:]) / 9 if len(volumes) >= 9 else curr_vol

        curr_candle = candles[-1]
        high        = curr_candle["high"]
        low         = curr_candle["low"]
        open_price  = curr_candle["open"]
        day_range   = high - low
        atr         = day_range if day_range > 0 else curr_close * 0.015

        # ── Detect signal ──────────────────────────────────
        bull_cross = prev_ema9 <= prev_ema26 and curr_ema9 > curr_ema26
        bear_cross = prev_ema9 >= prev_ema26 and curr_ema9 < curr_ema26
        bull_bounce = (curr_ema9 > curr_ema26 and                   # uptrend
                       low <= curr_ema26 * 1.002 and                 # touched EMA26
                       curr_close > open_price)                      # closed green
        bear_bounce = (curr_ema9 < curr_ema26 and                   # downtrend
                       high >= curr_ema26 * 0.998 and                # touched EMA26
                       curr_close < open_price)                      # closed red

        if bull_cross or bull_bounce:
            direction = "BUY"
        elif bear_cross or bear_bounce:
            direction = "SELL"
        else:
            return None   # No signal

        # ── Score the signal ──────────────────────────────
        score = 0

        # Volume (25 pts)
        vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 1
        if vol_ratio >= 3:   score += 25
        elif vol_ratio >= 2: score += 20
        elif vol_ratio >= 1.5: score += 12
        elif vol_ratio >= 1: score += 6

        # Signal type (20 pts) — crossover > bounce
        if bull_cross or bear_cross:
            score += 20
        else:
            score += 12

        # Price range (15 pts)
        if 100 <= curr_close <= 1500:   score += 15
        elif 50 <= curr_close <= 2500:  score += 10
        elif 20 <= curr_close <= 4000:  score += 5

        # Day range / volatility (15 pts)
        range_pct = (day_range / open_price * 100) if open_price > 0 else 0
        if 2 <= range_pct <= 8:   score += 15
        elif 1 <= range_pct < 2:  score += 8
        elif range_pct > 8:       score += 5

        # Price position in day range (15 pts)
        if day_range > 0:
            pos = (curr_close - low) / day_range
            if direction == "BUY":
                if pos < 0.4:    score += 15   # near low — great entry
                elif pos < 0.7:  score += 10
                else:            score += 3
            else:
                if pos > 0.6:    score += 15   # near high — great sell entry
                elif pos > 0.3:  score += 10
                else:            score += 3

        # EMA separation quality (10 pts)
        ema_gap_pct = abs(curr_ema9 - curr_ema26) / curr_ema26 * 100
        if ema_gap_pct >= 0.5:  score += 10
        elif ema_gap_pct >= 0.2: score += 6
        else:                    score += 2

        score = min(score, 100)

        # ── Calculate levels ──────────────────────────────
        if direction == "BUY":
            sl      = round(curr_close - atr * 1.0, 2)
            target1 = round(curr_close + atr * 1.5, 2)
            target2 = round(curr_close + atr * 3.0, 2)
        else:
            sl      = round(curr_close + atr * 1.0, 2)
            target1 = round(curr_close - atr * 1.5, 2)
            target2 = round(curr_close - atr * 3.0, 2)

        return {
            "symbol"     : symbol,
            "security_id": security_id,
            "direction"  : direction,
            "price"      : curr_close,
            "sl"         : sl,
            "target1"    : target1,
            "target2"    : target2,
            "score"      : score,
            "vol_ratio"  : round(vol_ratio, 2),
            "ema9"       : round(curr_ema9, 2),
            "ema26"      : round(curr_ema26, 2),
            "signal_type": "CROSS" if (bull_cross or bear_cross) else "BOUNCE",
        }


    # ── EMA calculation ───────────────────────────────────
    def _ema(self, values: list, period: int) -> list:
        if len(values) < period:
            return values
        k      = 2 / (period + 1)
        ema    = [sum(values[:period]) / period]
        for v in values[period:]:
            ema.append(v * k + ema[-1] * (1 - k))
        # Pad front so indices match
        padding = [None] * (period - 1)
        return padding + ema
