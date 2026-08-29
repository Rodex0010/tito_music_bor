# ==============================================================================
# mongo.py - MongoDB Database Manager
# ==============================================================================
# This file handles all database operations using MongoDB.
# Collections:
# - users: User data (sudo users)
# - chats: Group/chat data (language, channel play mode, authorized users)
# - blacklist: Blacklisted users/chats
# - calls: Active voice call sessions
# - cache: Admin list cache
#
# Features:
# - Async MongoDB operations for better performance
# - Connection pooling for efficiency
# - Admin list caching to reduce database queries
# - Random assistant selection for load balancing
# ==============================================================================

from random import randint, choice
from time import time
import asyncio
import logging

from pymongo import AsyncMongoClient

from tito import config, logger, userbot


# hide harmless MongoDB background errors
class MongoBackgroundFilter(logging.Filter):
    def filter(self, record):
        # ignore background reconnect and cancellation errors
        msg = record.getMessage()
        return not (
            'MongoClient background task encountered an error' in msg or
            ('AutoReconnect' in msg and 'background task' in msg) or
            ('_OperationCancelled' in msg and 'background task' in msg)
        )

logging.getLogger('pymongo.client').addFilter(MongoBackgroundFilter())


class MongoDB:
    def __init__(self):
        
        # set up the MongoDB connection

        self.mongo = AsyncMongoClient(
            config.MONGO_URL,
            serverSelectionTimeoutMS=12500,
            connectTimeoutMS=20000,
            socketTimeoutMS=20000,
            maxPoolSize=20,  # Reduced from 50 to prevent too many open connections
            minPoolSize=5,   # Reduced from 10 to prevent too many open connections
            maxIdleTimeMS=30000,  # Reduced from 45000 - close idle connections faster
            waitQueueTimeoutMS=10000,
            retryWrites=True,
            retryReads=True
        )
        self.db = self.mongo.HasiiTune

        self.admin_list = {}  # Cache admin lists
        self.admin_cache_time = {}  # Track cache freshness
        self.active_calls = {}
        self.blacklisted = []
        self.notified = []
        self.cache = self.db.cache
        self.logger = False
        self.vplay_enabled = config.VIDEO_PLAY

        self.assistant = {}
        self.assistantdb = self.db.assistant

        self.auth = {}
        self.authdb = self.db.auth

        self.chats = []
        self.chatsdb = self.db.chats
        self.chat_kind = {}  # chat_id -> "group" | "channel" (persisted alongside chatsdb)

        self.lang = {}
        self.langdb = self.db.lang

        self.play_mode = []
        self.playmodedb = self.db.play

        self.users = []
        self.usersdb = self.db.users

        self.azan = {}
        self.azandb = self.db.azan

    async def connect(self) -> None:
        # connect to the database and retry if it fails.
        max_retries = 3
        retry_delay = 5  # Initial delay in seconds
        
        for attempt in range(1, max_retries + 1):
            try:
                start = time()
                await self.mongo.admin.command("ping")
                logger.info(
                    f"✅ Database connection successful. ({time() - start:.2f}s)")

                # Create indexes for faster queries
                await self.authdb.create_index("_id")
                await self.langdb.create_index("_id")
                await self.cache.create_index("_id")

                await self.load_cache()
                return # connected successfully
            except Exception as e:
                if attempt < max_retries:
                    # wait longer after each failed attempt
                    wait_time = retry_delay * (2 ** (attempt - 1))
                    logger.warning(f"Database connection attempt {attempt}/{max_retries} failed: {type(e).__name__}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise SystemExit(
                        f"Database connection failed after {max_retries} attempts: {type(e).__name__}") from e

    async def close(self) -> None:
        # close the database connection
        await self.mongo.close()
        logger.info("Database connection closed.")

    # CACHE
    async def get_call(self, chat_id: int) -> bool:
        return chat_id in self.active_calls

    async def add_call(self, chat_id: int) -> None:
        self.active_calls[chat_id] = 1

    async def remove_call(self, chat_id: int) -> None:
        self.active_calls.pop(chat_id, None)

    async def playing(self, chat_id: int, paused: bool = None) -> bool | None:
        if paused is not None:
            self.active_calls[chat_id] = int(not paused)
        return bool(self.active_calls[chat_id])

    async def get_admins(self, chat_id: int, reload: bool = False) -> list[int]:
        from tito.helpers._admins import reload_admins

        # keep the admin cache for 15 minutes
        # helps reduce database queries during heavy use
        current_time = time()
        cache_age = current_time - self.admin_cache_time.get(chat_id, 0)

        if chat_id not in self.admin_list or reload or cache_age > 900:  # 15 minutes
            self.admin_list[chat_id] = await reload_admins(chat_id)
            self.admin_cache_time[chat_id] = current_time
        return self.admin_list[chat_id]

    # AUTH METHODS
    async def _get_auth(self, chat_id: int) -> set[int]:
        if chat_id not in self.auth:
            doc = await self.authdb.find_one({"_id": chat_id}) or {}
            self.auth[chat_id] = set(doc.get("user_ids", []))
        return self.auth[chat_id]

    async def is_auth(self, chat_id: int, user_id: int) -> bool:
        return user_id in await self._get_auth(chat_id)

    async def add_auth(self, chat_id: int, user_id: int) -> None:
        users = await self._get_auth(chat_id)
        if user_id not in users:
            users.add(user_id)
            await self.authdb.update_one(
                {"_id": chat_id}, {"$addToSet": {"user_ids": user_id}}, upsert=True
            )

    async def rm_auth(self, chat_id: int, user_id: int) -> None:
        users = await self._get_auth(chat_id)
        if user_id in users:
            users.discard(user_id)
            await self.authdb.update_one(
                {"_id": chat_id}, {"$pull": {"user_ids": user_id}}
            )

    # ASSISTANT METHODS
    def _connected_assistant_nums(self) -> list[int]:
        """Real assistant numbers (1/2/3) that are actually connected right now.

        userbot.clients only contains the assistants that started successfully,
        so its *length* is not the same thing as *which* numbers are alive
        (e.g. if assistant 2 fails to start, clients = [assistant1, assistant3],
        which has length 2 but does NOT mean assistant "2" is usable). We must
        check identity against .one/.two/.three instead of using len().
        """
        nums = []
        if hasattr(userbot, 'one') and userbot.one in userbot.clients:
            nums.append(1)
        if hasattr(userbot, 'two') and userbot.two in userbot.clients:
            nums.append(2)
        if hasattr(userbot, 'three') and userbot.three in userbot.clients:
            nums.append(3)
        return nums

    async def set_assistant(self, chat_id: int) -> int | None:
        nums = self._connected_assistant_nums()
        if not nums:
            # no assistant is connected at all right now
            self.assistant[chat_id] = None
            return None

        num = choice(nums)
        await self.assistantdb.update_one(
            {"_id": chat_id},
            {"$set": {"num": num}},
            upsert=True,
        )
        self.assistant[chat_id] = num
        return num

    async def get_assistant(self, chat_id: int):
        from tito import tune

        if chat_id not in self.assistant:
            doc = await self.assistantdb.find_one({"_id": chat_id})
            self.assistant[chat_id] = doc["num"] if doc else None

        # reassign if the stored assistant isn't actually connected right now
        if self.assistant[chat_id] not in self._connected_assistant_nums():
            await self.set_assistant(chat_id)

        if self.assistant[chat_id] is None:
            return None

        return tune.clients_by_num.get(self.assistant[chat_id])

    async def get_assistant_num(self, chat_id: int) -> int | None:
        """Return which assistant slot (1/2/3) is serving this chat.

        Ensures get_assistant() has already run so self.assistant[chat_id]
        is populated and pointing at a currently-connected assistant.
        """
        await self.get_assistant(chat_id)
        return self.assistant.get(chat_id)

    async def get_client(self, chat_id: int):
        if chat_id not in self.assistant:
            await self.get_assistant(chat_id)

        # make sure the assigned assistant is still connected
        if self.assistant[chat_id] not in self._connected_assistant_nums():
            await self.set_assistant(chat_id)

        if self.assistant[chat_id] is None:
            return None

        available_clients = {}
        if hasattr(userbot, 'one') and userbot.one in userbot.clients:
            available_clients[1] = userbot.one
        if hasattr(userbot, 'two') and userbot.two in userbot.clients:
            available_clients[2] = userbot.two
        if hasattr(userbot, 'three') and userbot.three in userbot.clients:
            available_clients[3] = userbot.three

        return available_clients.get(self.assistant[chat_id])

    # BLACKLIST METHODS
    async def add_blacklist(self, chat_id: int) -> None:
        if str(chat_id).startswith("-"):
            self.blacklisted.append(chat_id)
            return await self.cache.update_one(
                {"_id": "bl_chats"}, {"$addToSet": {"chat_ids": chat_id}}, upsert=True
            )
        await self.cache.update_one(
            {"_id": "bl_users"}, {"$addToSet": {"user_ids": chat_id}}, upsert=True
        )

    async def del_blacklist(self, chat_id: int) -> None:
        if str(chat_id).startswith("-"):
            self.blacklisted.remove(chat_id)
            return await self.cache.update_one(
                {"_id": "bl_chats"},
                {"$pull": {"chat_ids": chat_id}},
            )
        await self.cache.update_one(
            {"_id": "bl_users"},
            {"$pull": {"user_ids": chat_id}},
        )

    async def get_blacklisted(self, chat: bool = False) -> list[int]:
        if chat:
            if not self.blacklisted:
                doc = await self.cache.find_one({"_id": "bl_chats"})
                self.blacklisted.extend(doc.get("chat_ids", []) if doc else [])
            return self.blacklisted
        doc = await self.cache.find_one({"_id": "bl_users"})
        return doc.get("user_ids", []) if doc else []

    # CHAT METHODS
    async def is_chat(self, chat_id: int) -> bool:
        return chat_id in self.chats

    async def add_chat(self, chat_id: int, kind: str = None) -> None:
        """kind is 'group' or 'channel' when known at add-time (new_chat.py
        passes it in). Kept optional so old callers / old DB rows still work -
        anything without a known kind gets classified lazily by get_chat_kind()."""
        if not await self.is_chat(chat_id):
            self.chats.append(chat_id)
            doc = {"_id": chat_id}
            if kind:
                doc["kind"] = kind
                self.chat_kind[chat_id] = kind
            await self.chatsdb.insert_one(doc)
        elif kind and self.chat_kind.get(chat_id) != kind:
            await self.set_chat_kind(chat_id, kind)

    async def rm_chat(self, chat_id: int) -> None:
        if await self.is_chat(chat_id):
            self.chats.remove(chat_id)
            self.chat_kind.pop(chat_id, None)
            await self.chatsdb.delete_one({"_id": chat_id})

    async def get_chats(self) -> list:
        if not self.chats:
            async for chat in self.chatsdb.find():
                self.chats.append(chat["_id"])
                if chat.get("kind"):
                    self.chat_kind[chat["_id"]] = chat["kind"]
        return self.chats

    # CHAT KIND (group vs channel) - lets the owner panel list them separately.
    async def set_chat_kind(self, chat_id: int, kind: str) -> None:
        self.chat_kind[chat_id] = kind
        await self.chatsdb.update_one(
            {"_id": chat_id}, {"$set": {"kind": kind}}, upsert=True
        )

    async def get_chat_kind(self, chat_id: int) -> str:
        """Returns 'group' or 'channel'. Classifies (and persists) on first
        use for any chat added before this feature existed, or added without
        a known kind - no re-setup needed, it self-heals."""
        if chat_id in self.chat_kind:
            return self.chat_kind[chat_id]

        from tito import app
        import pyrogram

        kind = "group"
        try:
            chat = await app.get_chat(chat_id)
            if chat.type == pyrogram.enums.ChatType.CHANNEL:
                kind = "channel"
        except Exception:
            pass  # can't resolve right now - guess "group", will re-check later

        await self.set_chat_kind(chat_id, kind)
        return kind

    async def get_groups(self) -> list[int]:
        chats = await self.get_chats()
        return [cid for cid in chats if await self.get_chat_kind(cid) == "group"]

    async def get_channels(self) -> list[int]:
        chats = await self.get_chats()
        return [cid for cid in chats if await self.get_chat_kind(cid) == "channel"]

    async def reconcile_chats(self) -> None:
        """Startup self-heal: confirms the bot is still actually present in
        every group/channel on record (drops stale ones it was kicked from
        while offline), and classifies group-vs-channel for any chat that
        was added before that distinction existed. Meant to run as a
        background task right after boot so nobody has to redo any setup
        after a restart - the owner panel just reflects reality."""
        from tito import app, logger

        chats = list(await self.get_chats())
        removed = 0
        classified = 0

        for chat_id in chats:
            try:
                chat = await app.get_chat(chat_id)
            except Exception:
                await self.rm_chat(chat_id)
                removed += 1
                await asyncio.sleep(0.05)
                continue

            if chat_id not in self.chat_kind:
                import pyrogram
                kind = "channel" if chat.type == pyrogram.enums.ChatType.CHANNEL else "group"
                await self.set_chat_kind(chat_id, kind)
                classified += 1

            await asyncio.sleep(0.05)

        logger.info(
            f"🔄 Chat reconciliation done: removed {removed} stale, classified {classified}."
        )

    # AZAN (PRAYER TIMES) METHODS
    async def set_azan(self, chat_id: int, enabled: bool, city: str = None, country: str = None) -> None:
        doc = {"enabled": enabled}
        if city:
            doc["city"] = city
        if country:
            doc["country"] = country

        await self.azandb.update_one(
            {"_id": chat_id}, {"$set": doc}, upsert=True
        )
        self.azan[chat_id] = await self.azandb.find_one({"_id": chat_id})

    async def get_azan(self, chat_id: int) -> dict | None:
        if chat_id not in self.azan:
            self.azan[chat_id] = await self.azandb.find_one({"_id": chat_id})
        return self.azan[chat_id]

    async def get_azan_chats(self) -> list[dict]:
        return [doc async for doc in self.azandb.find({"enabled": True})]

    # LANGUAGE METHODS
    async def set_lang(self, chat_id: int, lang_code: str):
        await self.langdb.update_one(
            {"_id": chat_id},
            {"$set": {"lang": lang_code}},
            upsert=True,
        )
        self.lang[chat_id] = lang_code

    async def get_lang(self, chat_id: int) -> str:
        if chat_id not in self.lang:
            doc = await self.langdb.find_one({"_id": chat_id})
            # Default language for anyone who hasn't picked one yet is English.
            self.lang[chat_id] = doc["lang"] if doc else "en"
        return self.lang[chat_id]

    # VPLAY TOGGLE METHODS
    async def get_vplay_enabled(self) -> bool:
        # check if /vplay commands are enabled
        if hasattr(self, "vplay_enabled"):
            return self.vplay_enabled

        doc = await self.cache.find_one({"_id": "vplay_toggle"})
        self.vplay_enabled = doc.get("enabled", config.VIDEO_PLAY) if doc else config.VIDEO_PLAY
        return self.vplay_enabled

    async def set_vplay_enabled(self, enabled: bool) -> None:
        # Enable or disable /vplay commands globally.
        self.vplay_enabled = enabled
        await self.cache.update_one(
            {"_id": "vplay_toggle"},
            {"$set": {"enabled": enabled}},
            upsert=True,
        )

    # FORCE-SUBSCRIBE TOGGLE METHODS
    async def get_fsub_enabled(self) -> bool:
        # Whether users must join the support channel to use the bot.
        if hasattr(self, "fsub_enabled"):
            return self.fsub_enabled

        doc = await self.cache.find_one({"_id": "fsub_toggle"})
        self.fsub_enabled = doc.get("enabled", False) if doc else False
        return self.fsub_enabled

    async def set_fsub_enabled(self, enabled: bool) -> None:
        self.fsub_enabled = enabled
        await self.cache.update_one(
            {"_id": "fsub_toggle"},
            {"$set": {"enabled": enabled}},
            upsert=True,
        )



    # LOGGER METHODS
    async def is_logger(self) -> bool:
        return self.logger

    async def get_logger(self) -> bool:
        doc = await self.cache.find_one({"_id": "logger"})
        if doc:
            self.logger = doc["status"]
        return self.logger

    async def set_logger(self, status: bool) -> None:
        self.logger = status
        await self.cache.update_one(
            {"_id": "logger"},
            {"$set": {"status": status}},
            upsert=True,
        )



    # AUTO LEAVE METHODS
    async def get_autoleave(self, chat_id: int) -> bool:

        # Get auto-leave status for a chat. Default is False
        doc = await self.cache.find_one({"_id": f"autoleave_{chat_id}"})
        return doc.get("enabled", False) if doc else False

    async def set_autoleave(self, chat_id: int, enabled: bool) -> None:

        # Enable or disable auto-leave for a chat
        await self.cache.update_one(
            {"_id": f"autoleave_{chat_id}"},
            {"$set": {"enabled": enabled}},
            upsert=True,
        )

    # LOOP MODE METHODS
    async def get_loop(self, chat_id: int) -> int:

        # get the loop mode for a chat: 0=off, 1=single, 10=queue
        doc = await self.cache.find_one({"_id": f"loop_{chat_id}"})
        return doc.get("mode", 0) if doc else 0

    async def set_loop(self, chat_id: int, mode: int) -> None:

        # Set loop mode for a chat
        if mode == 0:
            await self.cache.delete_one({"_id": f"loop_{chat_id}"})
        else:
            await self.cache.update_one(
                {"_id": f"loop_{chat_id}"},
                {"$set": {"mode": mode}},
                upsert=True,
            )

    # PLAY MODE METHODS
    async def get_play_mode(self, chat_id: int) -> bool:
        if chat_id not in self.play_mode:
            doc = await self.playmodedb.find_one({"_id": chat_id})
            if doc:
                self.play_mode.append(chat_id)
        return chat_id in self.play_mode

    async def set_play_mode(self, chat_id: int, remove: bool = False) -> None:
        if remove:
            self.play_mode.remove(chat_id)
            await self.playmodedb.delete_one({"_id": chat_id})
        else:
            self.play_mode.append(chat_id)
            await self.playmodedb.insert_one({"_id": chat_id})

    # SUDO METHODS
    async def add_sudo(self, user_id: int) -> None:
        await self.cache.update_one(
            {"_id": "sudoers"}, {"$addToSet": {"user_ids": user_id}}, upsert=True
        )

    async def del_sudo(self, user_id: int) -> None:
        await self.cache.update_one(
            {"_id": "sudoers"}, {"$pull": {"user_ids": user_id}}
        )

    async def get_sudoers(self) -> list[int]:
        doc = await self.cache.find_one({"_id": "sudoers"})
        return doc.get("user_ids", []) if doc else []

    # USER METHODS
    async def is_user(self, user_id: int) -> bool:
        return user_id in self.users

    async def add_user(self, user_id: int) -> None:
        if not await self.is_user(user_id):
            self.users.append(user_id)
            await self.usersdb.insert_one({"_id": user_id})

    async def rm_user(self, user_id: int) -> None:
        if await self.is_user(user_id):
            self.users.remove(user_id)
            await self.usersdb.delete_one({"_id": user_id})

    async def get_users(self) -> list:
        if not self.users:
            self.users.extend([user["_id"] async for user in self.usersdb.find()])
        return self.users


    async def load_cache(self) -> None:
        
        # load cache data from the database for faster access
        logger.info("📦 Loading database cache...")
        # Load chats, users, blacklists, and logger status
        await self.get_chats()
        await self.get_users()
        await self.get_blacklisted(chat=True)  # Load blacklisted chats
        await self.get_logger()
        await self.get_vplay_enabled()
        
        # Preload sudoers list
        await self.get_sudoers()
        
        logger.info(f"✅ Cache loaded: {len(self.chats)} chats, {len(self.users)} users, {len(self.blacklisted)} blacklisted.")
