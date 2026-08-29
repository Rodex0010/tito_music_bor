# ==============================================================================
# ˹ʜᴀꜱɪɪ ᴍᴜꜱɪᴄ˼ Core Initialization
# ==============================================================================
# Sets up logging, config, and instantiates the main singleton objects (db, bot, etc.)
# ==============================================================================

import asyncio
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
from typing import List
from pyrogram.errors import ChannelInvalid

# --------------------------------------------------------------------------
# Patch: kurigram's User/Chat `.mention` property builds its HTML anchor as
# `<a href={url}>{text}</a>` (no quotes around the href value). Telegram's
# HTML entity parser rejects that as ENTITY_TEXT_INVALID whenever the URL
# contains characters like `:`/`?` (e.g. every `tg://user?id=...` mention
# link), which breaks any outgoing message/caption that embeds a user
# mention. Patch the template to quote the href, matching valid HTML.
# --------------------------------------------------------------------------
try:
    from pyrogram.types.user_and_chats.user import Link as _MentionLink
    _MentionLink.HTML = '<a href="{url}">{text}</a>'
except Exception:
    logging.getLogger("tito").exception("Failed to patch pyrogram mention Link.HTML")

# Force UTF-8 on stdout/stderr so emoji in log messages don't crash on
# Windows consoles that default to a legacy codepage (e.g. cp1256).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# Configure logging
logging.basicConfig(
    format="[%(asctime)s - %(levelname)s] - %(name)s: %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    handlers=[
        RotatingFileHandler("log.txt", maxBytes=10485760, backupCount=5, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
    level=logging.INFO,
)

# Reduce noise from third-party libraries
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("ntgcalls").setLevel(logging.CRITICAL)
logging.getLogger("pymongo").setLevel(logging.ERROR)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("pytgcalls").setLevel(logging.ERROR)

logger = logging.getLogger("tito")


def _asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    exc = context.get("exception")
    if isinstance(exc, ChannelInvalid):
        logger.warning("Ignoring CHANNEL_INVALID update (channel probably removed).")
        return
    loop.default_exception_handler(context)


asyncio.get_event_loop().set_exception_handler(_asyncio_exception_handler)

# Version
__version__ = "3.0.1"

# Load configuration
from config import Config

config = Config()
config.check()

# Global task list for background tasks
tasks: List = []
boot: float = time.time()

# Initialize bot client
from tito.core.bot import Bot
app = Bot()

# Ensure required directories exist
from tito.core.dir import ensure_dirs
ensure_dirs()

# Initialize userbot/assistant clients
from tito.core.userbot import Userbot
userbot = Userbot()

# Force custom emojis everywhere: patches pyrogram.Client once so every
# outgoing text/caption (from any plugin, present or future) automatically
# gets swapped to the configured custom emoji, falling back to the normal
# unicode emoji wherever no custom_emoji_id is set in core/emojis.py.
from tito.core.emoji_patch import install as _install_emoji_patch
_install_emoji_patch()

# Initialize database connection
from tito.core.mongo import MongoDB
db = MongoDB()

# Initialize language system
from tito.core.lang import Language
lang = Language()

# Initialize Telegram and YouTube utilities
from tito.core.telegram import Telegram
from tito.core.youtube import YouTube
tg = Telegram()
yt = YouTube()

# Initialize preload manager for background track downloading
from tito.core.preload import PreloadManager
preload = PreloadManager()

# Initialize queue manager
from tito.helpers import Queue
queue = Queue()

# Initialize call handler
from tito.core.calls import TgCall
tune = TgCall()

# Initialize prayer-time (azan) scheduler
from tito.core.azan import PrayerScheduler
azan = PrayerScheduler()


async def stop() -> None:
    logger.info("🛑 Stopping bot...")
    
    # Cancel all background tasks
    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Expected when cancelling tasks - suppress the error
            pass
        except Exception:
            pass
    
    # Close all connections
    await app.exit()
    await userbot.exit()
    await db.close()
    
    logger.info("✅ Bot stopped successfully.\n")
