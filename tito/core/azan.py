# ==============================================================================
# azan.py - Prayer Time Scheduler   (حطه في: tito/core/azan.py)
# ==============================================================================
# For every chat that enabled /تفعيل_الاذان:
#   - fetches today's prayer times (Aladhan API) for the chat's city/country
#   - at each prayer time, joins the voice chat (via the chat's assigned
#     assistant, same as normal music playback) and streams the azan file
#   - sends a text announcement in the chat
#
# التعديلات في النسخة دي:
#   - Media(...) و play_media جوه try واحد، واللوج بيطبع الـ traceback كامل
#   - _fire_at ملفوفة بـ try/except عشان الـ task ماتموتش بصمت
#   - الـ tasks بتتخزن عشان بايثون ماتعملهاش garbage collect
# ==============================================================================

import asyncio
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro) -> None:
        """create_task + keep a reference so it isn't garbage collected."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _fetch_timings(self, city: str, country: str) -> tuple[dict, str] | None:
        """Returns (timings, iana_timezone) for the given city, or None."""
        params = {
            "city": city,
            "country": country,
            "method": config.PRAYER_METHOD,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(API_URL, params=params, timeout=15) as resp:
                    if resp.status != 200:
                        logger.warning(
                            f"azan: aladhan returned {resp.status} for {city},{country}"
                        )
                        return None
                    data = await resp.json()
                    payload = data.get("data", {})
                    timings = payload.get("timings")
                    tz_name = payload.get("meta", {}).get("timezone")
                    if not timings or not tz_name:
                        return None
                    return timings, tz_name
        except Exception as e:
            logger.warning(f"azan: failed fetching timings for {city},{country}: {e}")
            return None

    async def _announce_and_play(self, chat_id: int, prayer_key: str) -> None:
        prayer_name = PRAYERS[prayer_key]

        # 1) text announcement
        try:
            await app.send_message(
                chat_id,
                f"🕌 <b>حان الآن موعد أذان {prayer_name}</b>",
            )
        except Exception:
            logger.error(
                f"azan: couldn't send announcement to {chat_id}:\n{traceback.format_exc()}"
            )

        # 2) audio + voice chat
        audio_path = getattr(config, "ADHAN_AUDIO_PATH", "")
        if not audio_path:
            logger.warning(
                "azan: ADHAN_AUDIO_PATH is not set - sending text announcement "
                "only, the voice chat will NOT be opened."
            )
            return

        logger.info(f"azan: starting playback in {chat_id} ({prayer_key}) -> {audio_path}")
        try:
            media = Media(
                id="azan",
                duration="",
                duration_sec=0,
                file_path=audio_path,
                message_id=0,
                title=f"أذان {prayer_name}",
                url="",
            )
            await tune.play_media(chat_id, None, media)
            logger.info(f"azan: play_media finished OK for {chat_id}")
        except Exception:
            # traceback كامل عشان نعرف أنهي سطر بيفشل
            logger.error(
                f"azan: FAILED to play in {chat_id}:\n{traceback.format_exc()}"
            )

    async def _schedule_chat_today(self, chat_id: int, city: str, country: str) -> None:
        try:
            result = await self._fetch_timings(city, country)
            if not result:
                return
            timings, tz_name = result

            try:
                tz = ZoneInfo(tz_name)
            except Exception as e:
                logger.warning(
                    f"azan: unknown timezone '{tz_name}' for {city},{country}: {e}"
                )
                return

            # "now" لازم يتحسب بتوقيت المدينة مش السيرفر
            now = datetime.now(tz)
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

                logger.info(
                    f"azan: scheduled {key} for chat {chat_id} in {int(delay)}s ({raw} {tz_name})"
                )
                self._spawn(self._fire_at(delay, chat_id, key))
        except Exception:
            logger.error(
                f"azan: _schedule_chat_today crashed for {chat_id}:\n{traceback.format_exc()}"
            )

    async def _fire_at(self, delay: float, chat_id: int, prayer_key: str) -> None:
        try:
            await asyncio.sleep(delay)
            # re-check the chat still has azan enabled before playing
            doc = await db.get_azan(chat_id)
            if doc and doc.get("enabled"):
                await self._announce_and_play(chat_id, prayer_key)
        except Exception:
            logger.error(
                f"azan: _fire_at crashed for {chat_id}/{prayer_key}:\n{traceback.format_exc()}"
            )

    async def _daily_loop(self) -> None:
        while True:
            try:
                chats = await db.get_azan_chats()
                for doc in chats:
                    city = doc.get("city")
                    country = doc.get("country")
                    if not city or not country:
                        continue
                    self._spawn(self._schedule_chat_today(doc["_id"], city, country))
            except Exception:
                logger.error(
                    f"azan: daily scheduling loop error:\n{traceback.format_exc()}"
                )

            # sleep until just after midnight, then re-schedule for the new day
            now = datetime.now()
            tomorrow = (now + timedelta(days=1)).replace(
                hour=0, minute=5, second=0, microsecond=0
            )
            await asyncio.sleep((tomorrow - now).total_seconds())

    def boot(self) -> None:
        self._task = asyncio.create_task(self._daily_loop())
        logger.info("🕌 Azan (prayer time) scheduler started.")
