"""
# ==============================================================================
# bot.py - Main Bot Client Manager
# ==============================================================================
# This file manages the main Telegram bot client.
# Features:
# - Extends the Pyrogram client
# - Handles bot login and connection
# - Starts and stops the bot
# - Sets owner, logger, and sudo filters
# - Stores bot details
# ==============================================================================
"""

import asyncio
import sqlite3
import pyrogram
from typing import Optional

from tito import config, logger


class Bot(pyrogram.Client):

    # Sets up the bot and handles starting and stopping it.

    def __init__(self):
        # Initialize the bot client.
        extra_kwargs = {}
        # LinkPreviewOptions only exists on kurigram (this project's actual
        # dependency, see requirements.txt). If the wrong "pyrogram" package
        # ended up installed instead of/alongside "kurigram", this attribute
        # won't exist - degrade gracefully instead of crashing on boot.
        if hasattr(pyrogram.types, "LinkPreviewOptions"):
            extra_kwargs["link_preview_options"] = pyrogram.types.LinkPreviewOptions(
                is_disabled=True)
        else:
            logger.warning(
                "⚠️ pyrogram.types.LinkPreviewOptions not found - you likely "
                "have the wrong package installed. Run: pip uninstall pyrogram -y "
                "&& pip install -r requirements.txt to get 'kurigram' instead."
            )

        super().__init__(
            name="tito",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            parse_mode=pyrogram.enums.ParseMode.HTML,
            max_concurrent_transmissions=7,
            **extra_kwargs,
        )

        self.owner: int = config.OWNER_ID
        self.logger: int = config.LOGGER_ID
        self.bl_users: pyrogram.filters.Filter = pyrogram.filters.user()
        self.sudoers: set = {self.owner}  # keep track of sudo users
        self.sudo_filter: pyrogram.filters.Filter = pyrogram.filters.user(
            self.owner)

        # These will be set after boot()
        self.id: Optional[int] = None
        self.name: Optional[str] = None
        self.username: Optional[str] = None
        self.mention: Optional[str] = None

    async def boot(self) -> None:

        # Start the bot and complete the setup.

        # "database is locked" from the local tito.session sqlite file is
        # almost always transient on Windows - a leftover process that
        # hasn't released the file yet, or antivirus/OneDrive briefly
        # grabbing it since this folder tends to live under a synced
        # Downloads directory. Retry a few times with backoff instead of
        # dying on the very first hiccup; if it's still locked after that,
        # it's a real problem (another bot instance actually running) and
        # we let the error surface normally.
        attempts = 5
        for attempt in range(1, attempts + 1):
            try:
                await super().start()
                break
            except sqlite3.OperationalError as ex:
                if "locked" not in str(ex).lower() or attempt == attempts:
                    raise
                logger.warning(
                    f"⚠️ tito.session is locked (attempt {attempt}/{attempts}), "
                    f"retrying in 2s - if this keeps happening, make sure no "
                    f"other copy of the bot is already running, and that the "
                    f"bot's folder isn't inside a OneDrive/Google Drive synced "
                    f"path."
                )
                await asyncio.sleep(2)

        # Set bot information
        self.id = self.me.id
        self.name = self.me.first_name
        self.username = self.me.username
        self.mention = self.me.mention

        # Verify logger group access
        try:
            await self.send_message(self.logger, "🤖 ʙᴏᴛ ꜱᴛᴀʀᴛᴇᴅ")
            member = await self.get_chat_member(self.logger, self.id)
        except Exception as ex:
            raise SystemExit(
                f"❌ ʙᴏᴛ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴀᴄᴄᴇꜱꜱ ʟᴏɢɢᴇʀ ɢʀᴏᴜᴘ: {self.logger}\n"
                f"ʀᴇᴀꜱᴏɴ: {ex}\n"
                f"ᴘʟᴇᴀꜱᴇ ᴇɴꜱᴜʀᴇ ᴛʜᴇ ʙᴏᴛ ɪꜱ ᴀᴅᴅᴇᴅ ᴛᴏ ᴛʜᴇ ʟᴏɢɢᴇʀ ɢʀᴏᴜᴘ."
            )

        # Verify admin status
        if member.status != pyrogram.enums.ChatMemberStatus.ADMINISTRATOR:
            raise SystemExit(
                f"❌ ʙᴏᴛ ɪꜱ ɴᴏᴛ ᴀɴ ᴀᴅᴍɪɴɪꜱᴛʀᴀᴛᴏʀ ɪɴ ʟᴏɢɢᴇʀ ɢʀᴏᴜᴘ: {self.logger}\n"
                f"ᴘʟᴇᴀꜱᴇ ᴘʀᴏᴍᴏᴛᴇ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ᴀᴅᴍɪɴɪꜱᴛʀᴀᴛᴏʀ ᴡɪᴛʜ ɴᴇᴄᴇꜱꜱᴀʀʏ ᴘᴇʀᴍɪꜱꜱɪᴏɴꜱ."
            )

        logger.info(f"🤖 Bot started successfully as @{self.username}")

    async def exit(self) -> None:

        # Stop the bot.
        await super().stop()
        logger.info("🤖 Bot client stopped.")