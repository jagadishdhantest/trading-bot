import os, requests, logging
from dotenv import load_dotenv

load_dotenv()

class TelegramNotifier:
    def __init__(self):
        self.token   = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.base    = f"https://api.telegram.org/bot{self.token}"

    def send(self, message: str):
        if not self.token or not self.chat_id:
            logging.warning("Telegram credentials not set — skipping notification")
            return
        try:
            r = requests.post(
                f"{self.base}/sendMessage",
                json    = {
                    "chat_id"    : self.chat_id,
                    "text"       : message,
                    "parse_mode" : "Markdown",
                },
                timeout = 5,
            )
            r.raise_for_status()
            logging.info("Telegram notification sent ✅")
        except Exception as e:
            logging.error(f"Telegram send failed: {e}")
