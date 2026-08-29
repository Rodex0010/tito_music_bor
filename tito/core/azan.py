# ==============================================================================
# azan.py - Prayer Time Scheduler
# ==============================================================================
# For every chat that enabled /تفعيل_الاذان:
#   - fetches today's prayer times (Aladhan API) for the chat's city/country
#   - at each prayer time, joins the voice chat (via the chat's assigned
#     assistant, same as normal music playback) and streams the azan file
#   - sends a text announcement in the chat
#
# NOTE (Telegram platform limit): only user accounts (the assistants) can
# join/stream into a voice chat - a Bot API account cannot join a group call
# at all. So the participant that appears in the call will always be the
# assistant, never the bot itself. The common workaround is cosmetic: give
# the assistant account the bot's name/photo (Settings -> Edit Profile on
# that account) so it visually reads as the bot when it appears in the call.
# ==============================================================================

import asyncio
from datetime import datetime, timedelta

import aiohttp

from tito import app, config, db, logger, tune
from tito.helpers import Media

PRAYERS = {
    "Fajr": "الفجر",
    "Dhuhr": "الظهر",
    "Asr": "العصر",
    "Maghrib": "المغرب",
    "Isha": "العشاء",
}

API_URL = "http://api.aladhan.com/v1/timingsByCity"


class PrayerScheduler:
    def __init__(self):
        self._task: asyncio.Task | None = None

    async def _fetch_timings(self, city: str, country: str) -> dict | None:
        params = {
            "city": city,
            "country": country,
            "method": config.PRAYER_METHOD,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(API_URL, params=params, timeout=15) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return data.get("data", {}).get("timings")
        except Exception as e:
            logger.warning(f"azan: failed fetching timings for {city},{country}: {e}")
            return None

    async def _announce_and_play(self, chat_id: int, prayer_key: str) -> None:
        prayer_name = PRAYERS[prayer_key]

        try:
            await app.send_message(
                chat_id,
                f"🕌 <b>حان الآن موعد أذان {prayer_name}</b>",
            )
        except Exception as e:
            logger.warning(f"azan: couldn't send announcement to {chat_id}: {e}")

        # Use getattr() defensively: if ADHAN_AUDIO_PATH is ever missing from
        # Config (e.g. an older/unsynced config.py), this must not crash the
        # scheduled task silently - it should just skip the audio/call step
        # and log a clear warning instead, same as when it's simply unset.
        audio_path = getattr(config, "ADHAN_AUDIO_PATH", "")
        if not audio_path:
            logger.warning(
                "azan: ADHAN_AUDIO_PATH is not set - sending text announcement "
                "only, the voice chat will NOT be opened. Set ADHAN_AUDIO_PATH "
                "in your .env to a path/URL of the azan audio file to enable "
                "the call + playback."
            )
            return  # no audio file configured, text-only announcement

        media = Media(
            id="azan",
            duration="",
            duration_sec=0,
            file_path=audio_path,
            message_id=0,
            title=f"أذان {prayer_name}",
            url="",
        )

        try:
            await tune.play_media(chat_id, None, media)
        except Exception as e:
            logger.warning(f"azan: failed to play in {chat_id}: {e}")

    async def _schedule_chat_today(self, chat_id: int, city: str, country: str) -> None:
        timings = await self._fetch_timings(city, country)
        if not timings:
            return

        now = datetime.now()
        for key in PRAYERS:
            raw = timings.get(key)  # e.g. "15:42"
            if not raw:
                continue
            try:
                hh, mm = map(int, raw.split()[0].split(":"))
            except ValueError:
                continue

            prayer_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            delay = (prayer_dt - now).total_seconds()
            if delay < 0:
                continue  # already passed for today

            asyncio.create_task(self._fire_at(delay, chat_id, key))

    async def _fire_at(self, delay: float, chat_id: int, prayer_key: str) -> None:
        await asyncio.sleep(delay)
        # re-check the chat still has azan enabled before playing
        doc = await db.get_azan(chat_id)
        if doc and doc.get("enabled"):
            await self._announce_and_play(chat_id, prayer_key)

    async def _daily_loop(self) -> None:
        while True:
            try:
                chats = await db.get_azan_chats()
                for doc in chats:
                    city = doc.get("city")
                    country = doc.get("country")
                    if not city or not country:
                        continue
                    asyncio.create_task(
                        self._schedule_chat_today(doc["_id"], city, country)
                    )
            except Exception as e:
                logger.error(f"azan: daily scheduling loop error: {e}")

            # sleep until just after midnight, then re-schedule for the new day
            now = datetime.now()
            tomorrow = (now + timedelta(days=1)).replace(
                hour=0, minute=5, second=0, microsecond=0
            )
            await asyncio.sleep((tomorrow - now).total_seconds())

    def boot(self) -> None:
        self._task = asyncio.create_task(self._daily_loop())
        logger.info("🕌 Azan (prayer time) scheduler started.")