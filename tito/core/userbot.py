# ==============================================================================
# userbot.py - Assistant/Userbot Client Manager
# ==============================================================================
# This file manages assistant accounts (userbots) that join voice chats to play music.
# Assistants are user accounts (not bots) that can join and stream audio/video.
# The number of assistants is NOT fixed at 3 anymore: set config.MAX_ASSISTANTS
# (env var MAX_ASSISTANTS, default 20) to however many slots you want, then fill
# in STRING_SESSION (slot 1), STRING_SESSION2, STRING_SESSION3, ... up to that
# number. Empty slots are simply ignored at boot and stay available for the
# "🔄 تحديث الجلسات" panel to fill in later without a restart.
# More assistants = ability to serve more groups simultaneously.
# ==============================================================================

import asyncio

from pyrogram import Client

from tito import config, logger


class Userbot(Client):
    def __init__(self):
        """
        Initialize userbot with as many assistant clients as MAX_ASSISTANTS
        allows.

        Every slot from 1..MAX_ASSISTANTS gets a Client object up front
        (even if its session string is empty), so the session-management
        panel can always offer it as an "add assistant here" target and
        replace_client()/remove_client() never have to special-case a slot
        that didn't exist yet. Each assistant can independently join voice
        chats and stream music.
        """
        self.clients = []  # List of assistant clients that started successfully

        # by_num[num] -> Client for every slot 1..MAX_ASSISTANTS (whether or
        # not it currently has a session string). This replaces the old
        # fixed self.one / self.two / self.three attributes.
        self.by_num: dict[int, Client] = {}

        for num in range(1, config.MAX_ASSISTANTS + 1):
            self.by_num[num] = self._build_client(num, getattr(config, f"SESSION{num}", ""))

    @staticmethod
    def _build_client(num: int, session: str) -> Client:
        return Client(
            name=f"HasiiTuneUB{num}",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=session,  # Pyrogram session string, may be empty
        )

    async def boot_client(self, num: int, ub: Client):
        """
        Boot a client and perform initial setup.
        Args:
            num (int): The assistant slot number to boot.
            ub (Client): The userbot client instance.
        """
        client = ub
        try:
            await client.start()
        except Exception as e:
            logger.error(f"❌ Assistant {num} failed to start: {e}")
            logger.error(f"   This could be due to:")
            logger.error(f"   • Invalid session string (STRING_SESSION{num})")
            logger.error(f"   • Session logged out from another device")
            logger.error(f"   • Network/connectivity issues")
            return

        try:
            await client.send_message(config.LOGGER_ID, f"Assistant {num} Started")
        except Exception as e:
            logger.warning(
                f"⚠️ Assistant {num} couldn't send message to logger: {e}")
            # Continue anyway - this is not critical

        client.id = client.me.id if hasattr(
            client, 'me') and client.me else None
        client.name = client.me.first_name if hasattr(
            client, 'me') and client.me else f"Assistant{num}"
        client.username = client.me.username if hasattr(
            client, 'me') and client.me else None
        client.mention = client.me.mention if hasattr(
            client, 'me') and client.me else client.name
        self.clients.append(client)
        logger.info(f"👤 Assistant {num} started as @{client.username}")

    async def sync_overrides(self):
        """
        Pull any regenerated session strings out of the DB (saved by the
        "🔄 تحديث الجلسات" panel) and rebuild the matching Client *before*
        boot() starts it, so a restart keeps using the fresh session instead
        of the old one still sitting in .env.
        Must run after db.connect() and before userbot.boot().
        """
        from tito import db, logger as _logger  # deferred: db doesn't exist yet at import time

        for num in range(1, config.MAX_ASSISTANTS + 1):
            try:
                override = await db.get_session_override(num)
            except Exception as e:
                _logger.warning(f"Couldn't check session override for assistant {num}: {e}")
                continue
            if not override:
                continue
            self.by_num[num] = self._build_client(num, override)
            setattr(config, f"SESSION{num}", override)
            _logger.info(f"🔄 Loaded a refreshed session for assistant {num} from the database.")

    async def boot(self):

        # Asynchronously starts every configured assistant (any slot that
        # has a non-empty session string), in parallel rather than one at a
        # time, so N assistants come online in roughly the time one does
        # instead of N times as long.
        slots = [
            num for num in range(1, config.MAX_ASSISTANTS + 1)
            if getattr(config, f"SESSION{num}", "")
        ]
        await asyncio.gather(
            *(self.boot_client(num, self.by_num[num]) for num in slots)
        )

    async def replace_client(self, num: int, new_session_string: str) -> Client:
        """
        Hot-swap assistant <num> to a brand-new session string without a
        restart: stop the old client, boot a fresh one, and keep it in
        self.clients so the rest of the bot (call routing, /leave, etc.)
        picks it up transparently. Works for any slot up to MAX_ASSISTANTS,
        including one that was never configured before (adding a new
        assistant).
        """
        old_client = self.by_num.get(num)
        if old_client is not None:
            self.clients = [c for c in self.clients if c is not old_client]
            try:
                if old_client.is_connected:
                    await old_client.stop()
            except Exception as e:
                logger.warning(f"Error stopping old assistant {num} before swap: {e}")

        new_client = self._build_client(num, new_session_string)
        self.by_num[num] = new_client
        setattr(config, f"SESSION{num}", new_session_string)

        await self.boot_client(num, new_client)

        from tito import tune  # deferred: tune doesn't exist yet at import time
        await tune.register_client(num, new_client)

        return self.by_num[num]

    async def remove_client(self, num: int) -> None:
        """
        Fully wipe assistant <num>'s session: log it out on Telegram's side
        (so the string can never be reused / left half-alive), disconnect
        it, and drop it from self.clients so nothing routes calls to it
        anymore. This is the opposite of replace_client() - it leaves the
        slot unconfigured instead of swapping in a new session, which is
        what actually stops a bad/leftover session from sitting there
        "frozen" (connected but unusable).
        """
        old_client = self.by_num.get(num)
        if old_client is not None:
            self.clients = [c for c in self.clients if c is not old_client]
            try:
                if not old_client.is_connected:
                    await old_client.connect()
                await old_client.log_out()
            except Exception as e:
                logger.warning(f"Error logging out assistant {num} during delete: {e}")
                try:
                    if old_client.is_connected:
                        await old_client.stop()
                except Exception:
                    pass

        # Rebuild the slot as an empty, unconfigured client so the rest of
        # the bot sees it exactly like a SESSION{num} that was never set.
        self.by_num[num] = self._build_client(num, "")
        setattr(config, f"SESSION{num}", "")

        from tito import tune  # deferred: tune doesn't exist yet at import time
        await tune.register_client(num, None)

    async def exit(self):

        # Asynchronously stops every connected assistant, all at once
        # instead of one at a time.
        async def _stop(num: int, client: Client):
            try:
                if getattr(config, f"SESSION{num}", "") and getattr(client, "is_connected", False):
                    await client.stop()
            except Exception as e:
                logger.warning(f"Error stopping assistant {num}: {e}")

        await asyncio.gather(
            *(_stop(num, client) for num, client in self.by_num.items())
        )

        logger.info("Assistants stopped.")
