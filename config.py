# ==============================================================================
# config.py - Configuration
# ==============================================================================
# Pulls in all environment variables and sets defaults.
# Don't commit your .env file!
# ==============================================================================

from os import getenv
from typing import List
from dotenv import load_dotenv

load_dotenv()

class Config:
    def __init__(self):

        # TELEGRAM API CREDENTIALS
        self.API_ID: int = int(getenv("API_ID", 23873818))
        self.API_HASH: str = getenv("API_HASH", "0fb82e50665a5406979304c7fce10a6f")

        # BOT CONFIGURATION
        self.BOT_TOKEN: str = getenv("BOT_TOKEN", "8534099445:AAHedA5IxZekxEx4MLp_-YJaPnTT6yMwA8E")
        self.LOGGER_ID: int = -1004387392097
        self.OWNER_ID: int = int(getenv("OWNER_ID", 7876741744))

        # DATABASE CONFIGURATION
        self.MONGO_URL: str = getenv("MONGO_DB_URI", "mongodb+srv://oop94968_db_user:9PoeTGrOU6qU5Xf4@cluster0.dp5t3kz.mongodb.net/?appName=Cluster0")
                                                      
        # MUSIC BOT LIMITS
        self.DURATION_LIMIT: int = int(getenv("DURATION_LIMIT", "300")) * 60
        self.QUEUE_LIMIT: int = int(getenv("QUEUE_LIMIT", "30"))
        self.PLAYLIST_LIMIT: int = int(getenv("PLAYLIST_LIMIT", "20"))

        # ASSISTANT SESSIONS
        # required at least one
        self.SESSION1: str = getenv("STRING_SESSION", "")
        self.SESSION2: str = getenv("STRING_SESSION2", "")
        self.SESSION3: str = getenv("STRING_SESSION3", "")

        self.PRAYER_METHOD: int = int(getenv("PRAYER_METHOD", "5"))

        # Path or URL to the azan (call to prayer) audio file that gets
        # streamed into the voice chat when a prayer time is reached.
        # Leave empty to only send the text announcement (no audio/call).
        self.ADHAN_AUDIO_PATH: str = getenv("ADHAN_AUDIO_PATH", "")
        
        # SUPPORT LINKS
        self.SUPPORT_CHANNEL: str = getenv(
            "SUPPORT_CHANNEL", "https://t.me/l_zor_l")
        self.SUPPORT_CHAT: str = getenv("SUPPORT_CHAT", "https://t.me/XCODE000")

        # EXCLUDED CHATS
        self.EXCLUDED_CHATS: List[int] = self._parse_excluded_chats()

        # FEATURE FLAGS
        self.QUEUE_END_MESSAGE: bool = self._str_to_bool(getenv("QUEUE_END_MESSAGE", "True"))
        self.AUTO_LEAVE: bool = self._str_to_bool(getenv("AUTO_LEAVE", "True"))
        self.THUMB_GEN: bool = self._str_to_bool(getenv("THUMB_GEN", "True"))
        self.VIDEO_PLAY: bool = self._str_to_bool(getenv("VIDEO_PLAY", "True"))
        self.VIDEO_MAX_HEIGHT: int = self._parse_video_height()

        # YOUTUBE COOKIES
        self.COOKIES_URL: List[str] = self._parse_cookies()

        # Cookie refresh interval in hours
        self.COOKIE_REFRESH_HOURS: int = int(getenv("COOKIE_REFRESH_HOURS", "6"))   

        # IMAGE URLS
        self.DEFAULT_THUMB: str = getenv(
            "DEFAULT_THUMB",
            "https://files.catbox.moe/uzz1mu.png"  # Default thumbnail
        )
        self.PING_IMG: str = getenv(
            "PING_IMG", "https://files.catbox.moe/5wwbe4.png")    # Ping command image
        self.START_IMG: str = getenv(
            "START_IMG", "https://files.catbox.moe/h4q8se.png")  # Start command image   https://files.catbox.moe/l0fan7.png
        self.RADIO_IMG: str = getenv(
            "RADIO_IMG", "https://files.catbox.moe/gj26zf.png")    # Radio command image

        # MODERATION
        self.EXCLUDED_USERNAMES: List[str] = getenv("EXCLUDED_USERNAMES", "").split()

    def _parse_video_height(self) -> int:
        """Clamp configured video height to a safe range."""
        default_height = 480
        raw_value = getenv("VIDEO_MAX_HEIGHT", str(default_height))
        try:
            height = int(raw_value)
        except (TypeError, ValueError):
            return default_height

        # Allow disabling the cap by setting to 0 or negative (interpreted as unlimited)
        if height <= 0:
            return 0

        # Clamp between 360p and 1080p
        return max(360, min(height, 1080))

    def _parse_excluded_chats(self) -> List[int]:
        excluded = getenv("EXCLUDED_CHATS", "")
        if not excluded:
            return []

        chat_ids = []
        for chat_id in excluded.split(","):
            chat_id = chat_id.strip()
            if chat_id.lstrip('-').isdigit():
                chat_ids.append(int(chat_id))
        return chat_ids

    def _parse_cookies(self) -> List[str]:
        cookie_str = getenv("COOKIE_URL", "")
        if not cookie_str:
            return []

        valid_sources = ["batbin.me", "pastebin.com", "paste.ee", "rentry.co"]
        return [
            url.strip()
            for url in cookie_str.split()
            if url.strip() and any(source in url for source in valid_sources)
        ]

    @staticmethod
    def _str_to_bool(value: str) -> bool:
        return value.lower() in ("true", "1", "yes", "y", "on")

    def check(self) -> None:
        required_vars = {
            "API_ID": self.API_ID,
            "API_HASH": self.API_HASH,
            "BOT_TOKEN": self.BOT_TOKEN,
            "MONGO_DB_URI": self.MONGO_URL,
            "LOGGER_ID": self.LOGGER_ID,
            "OWNER_ID": self.OWNER_ID,
            "STRING_SESSION": self.SESSION1,
        }

        missing = [
            name for name, value in required_vars.items()
            if not value or (isinstance(value, int) and value == 0)
        ]

        if missing:
            raise SystemExit(
                f"❌ Missing required environment variables: {', '.join(missing)}\n"
                f"Please check your .env file and ensure all required variables are set."
            )



"""
████████╗██╗████████╗ ██████╗     ██████╗  ██████╗ ████████╗
╚══██╔══╝██║╚══██╔══╝██╔═══██╗    ██╔══██╗██╔═══██╗╚══██╔══╝
   ██║   ██║   ██║   ██║   ██║    ██████╔╝██║   ██║   ██║   
   ██║   ██║   ██║   ██║   ██║    ██╔══██╗██║   ██║   ██║   
   ██║   ██║   ██║   ╚██████╔╝    ██████╔╝╚██████╔╝   ██║   
   ╚═╝   ╚═╝   ╚═╝    ╚═════╝     ╚═════╝  ╚═════╝    ╚═╝   
                                                             
"""