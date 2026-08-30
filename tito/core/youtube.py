# ==============================================================================
# youtube.py - YouTube Integration
# ==============================================================================
# Handles searching for tracks, downloading them via yt-dlp, and managing cookies.
# ==============================================================================

import os
import re
import glob
import time
import yt_dlp
import random
import asyncio
import aiohttp
from dataclasses import replace
from pathlib import Path
from typing import Optional, Union

from pyrogram import enums, types
from tito import config, logger
from tito.helpers import Track, utils


class YouTube:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="  # Base YouTube URL
        self.cookies = []  # List of available cookie files
        self.checked = False  # Whether cookies directory has been checked
        self.warned = False  # Whether missing cookies warning has been shown

        # Match YouTube URLs
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|live/|embed/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )

        # Cache results for 10 mins
        self.search_cache = {}  # {"query_video": (result, timestamp)}
        self.cache_time = {}  # Deprecated, using tuple in search_cache instead

        # Limit concurrent downloads to prevent lag
        self._download_semaphore = asyncio.Semaphore(5)  # Max 5 simultaneous downloads
        self._max_video_height = getattr(config, "VIDEO_MAX_HEIGHT", 1080)

        # Video IDs currently being downloaded, right now, by anyone (a
        # normal /play, a preload, a loop-restart, etc). cleanup.py checks
        # this before deleting anything so an in-progress download is never
        # touched, no matter what folder-wiping is happening at the same time.
        self.active_downloads: set[str] = set()

        # Per-video_id locks so concurrent download() calls for the same
        # video (e.g. preload + play_next) queue up instead of racing each
        # other on disk. See _download_to_disk.
        self._download_locks: dict[str, asyncio.Lock] = {}

    def _locate_download_file(self, video_id: str, video: bool = False) -> Optional[str]:
        pattern = f"downloads/{video_id}*"
        candidates = sorted([
            path for path in glob.glob(pattern)
            if not path.endswith((".part", ".ytdl", ".info.json", ".temp"))
        ])

        video_exts = {".mp4", ".mkv", ".mov"}
        audio_exts = {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".wav", ".flac"}

        if video:
            for path in candidates:
                if os.path.isdir(path):
                    continue
                if Path(path).suffix.lower() in video_exts:
                    return path
        else:
            for path in candidates:
                if os.path.isdir(path):
                    continue
                if Path(path).suffix.lower() in audio_exts:
                    return path

        for path in candidates:
            if os.path.isdir(path):
                continue
            return path
        return None

    def get_cookies(self):
        if not self.checked:
            for file in os.listdir("tito/cookies"):
                if file.endswith(".txt"):
                    self.cookies.append(file)
            self.checked = True
        if not self.cookies:
            if not self.warned:
                self.warned = True
                logger.warning("Cookies are missing; downloads might fail.")
            return None
        return f"tito/cookies/{random.choice(self.cookies)}"

    async def save_cookies(self, urls: list[str]) -> None:
        logger.info("🍪 Saving cookies from urls...")
        saved_count = 0
        for url in urls:
            try:
                path = f"tito/cookies/cookie{random.randint(10000, 99999)}.txt"
                link = url.replace("me/", "me/raw/")
                async with aiohttp.ClientSession() as session:
                    async with session.get(link) as resp:
                        if resp.status != 200:
                            logger.error(f"❌ Cookie download failed: HTTP {resp.status} from {url}")
                            continue
                        content = await resp.read()
                        if not content or len(content) < 50:
                            logger.error(f"❌ Cookie file empty or invalid from {url}")
                            continue
                        with open(path, "wb") as fw:
                            fw.write(content)
                        if os.path.exists(path) and os.path.getsize(path) > 0:
                            saved_count += 1
                            # Update cookie list
                            cookie_filename = os.path.basename(path)
                            if cookie_filename not in self.cookies:
                                self.cookies.append(cookie_filename)
                            logger.info(f"✅ Saved: {cookie_filename} ({len(content)} bytes)")
            except Exception as e:
                logger.error(f"❌ Cookie download error from {url}: {e}")
        
        # Refresh cookie list
        self.checked = True
        
        if saved_count > 0:
            logger.info(f"✅ Cookies saved. ({saved_count} file(s))")
        else:
            logger.error("❌ No cookies saved! Check COOKIE_URL in .env. YouTube downloads will fail!")

    async def refresh_cookies(self, urls: list[str]) -> None:
        """Re-download cookies from COOKIE_URL, replacing whatever is
        currently on disk. Used both at startup and by the periodic
        cookie_refresh background task so cookies never go stale while the
        bot keeps running.
        """
        if not urls:
            return

        # Wipe old cookie files first so get_cookies() never hands out a
        # stale/expired one that's been sitting around from a previous run.
        try:
            for file in os.listdir("tito/cookies"):
                if file.endswith(".txt"):
                    try:
                        os.remove(os.path.join("tito/cookies", file))
                    except Exception as e:
                        logger.debug(f"refresh_cookies: could not remove {file}: {e}")
        except FileNotFoundError:
            os.makedirs("tito/cookies", exist_ok=True)

        self.cookies = []
        self.checked = False
        self.warned = False

        await self.save_cookies(urls)

    def valid(self, url: str) -> bool:
        return bool(re.match(self.regex, url))

    def url(self, message_1: types.Message) -> Union[str, None]:
        messages = [message_1]
        link = None
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)

        for message in messages:
            text = message.text or message.caption or ""

            if message.entities:
                for entity in message.entities:
                    if entity.type == enums.MessageEntityType.URL:
                        link = text[entity.offset: entity.offset +
                                    entity.length]
                        break

            if message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == enums.MessageEntityType.TEXT_LINK:
                        link = entity.url
                        break

        if link:
            return link.split("&si")[0].split("?si")[0]
        return None

    async def search(self, query: str, m_id: int) -> Track | None:
        # Check cache (10 min TTL)
        cache_key = query
        current_time = asyncio.get_running_loop().time()

        if cache_key in self.search_cache:
            cached_result, cache_timestamp = self.search_cache[cache_key]
            if current_time - cache_timestamp < 600:  # 10 minutes
                # Return a fresh copy
                fresh = replace(cached_result)
                fresh.message_id = m_id
                fresh.file_path = None
                fresh.user = None
                fresh.time = 0
                fresh.video = False
                return fresh

        try:
            if self.valid(query):
                def _extract():
                    cookie = self.get_cookies() if self.checked else None
                    ydl_opts = {
                        "quiet": True,
                        "noplaylist": True,
                        "extract_flat": "in_playlist",
                        
                "js_runtimes": {"deno": {}},
                        "cookiefile": cookie
                    }
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        return ydl.extract_info(query, download=False)

                data = await asyncio.to_thread(_extract)
                if not data:
                    return None

                duration_sec = data.get("duration")
                is_live = data.get("is_live", False)
                if duration_sec is None and is_live:
                    duration = "LIVE"
                    duration_sec = 0
                else:
                    duration = utils.format_duration(int(duration_sec)) if duration_sec else "0:00"

                track = Track(
                    id=data.get("id"),
                    channel_name=data.get("uploader") or data.get("channel", ""),
                    duration=duration,
                    duration_sec=int(duration_sec) if duration_sec else 0,
                    message_id=m_id,
                    title=(data.get("title") or "")[:25],
                    thumbnail=data.get("thumbnail") or "",
                    url=data.get("webpage_url") or query,
                    view_count=str(data.get("view_count", "")),
                    is_live=is_live,
                )
            else:
                def _extract_search():
                    cookie = self.get_cookies() if self.checked else None
                    ydl_opts = {
                        "quiet": True,
                        "extract_flat": True,
                        
                "js_runtimes": {"deno": {}},
                        "cookiefile": cookie
                    }
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        return ydl.extract_info(f"ytsearch1:{query}", download=False)
                        
                results = await asyncio.to_thread(_extract_search)
                
                if not results or "entries" not in results or not results["entries"]:
                    return None
                    
                data = results["entries"][0]
                duration_sec = data.get("duration")
                is_live = data.get("is_live", False)
                if duration_sec is None and is_live:
                    duration = "LIVE"
                    duration_sec = 0
                else:
                    duration = utils.format_duration(int(duration_sec)) if duration_sec else "0:00"

                raw_url = data.get("url") or ""
                # yt-dlp's flat search extraction often puts just the bare
                # video ID in "url" (not a full link) - if that's what we
                # got, prefer webpage_url or build the canonical link from
                # the ID instead, otherwise the raw ID ends up as an <a
                # href> and Telegram rejects the message with
                # ENTITY_TEXT_INVALID.
                if raw_url.startswith(("http://", "https://")):
                    video_url = raw_url
                else:
                    video_url = data.get("webpage_url") or (
                        f"https://youtube.com/watch?v={data.get('id')}"
                        if data.get("id") else raw_url
                    )

                track = Track(
                    id=data.get("id"),
                    channel_name=data.get("uploader") or data.get("channel", ""),
                    duration=duration,
                    duration_sec=int(duration_sec) if duration_sec else 0,
                    message_id=m_id,
                    title=(data.get("title") or "")[:25],
                    thumbnail=data.get("thumbnails", [{}])[-1].get("url", "").split("?")[0] if data.get("thumbnails") else "",
                    url=video_url,
                    view_count=str(data.get("view_count", "")),
                    is_live=is_live,
                )

            # Cache result (max 100)
            self.search_cache[cache_key] = (track, current_time)
            if len(self.search_cache) > 100:
                oldest_key = min(self.search_cache.keys(),
                                 key=lambda k: self.search_cache[k][1])
                del self.search_cache[oldest_key]

            return replace(track)
            
        except Exception as e:
            logger.warning(f"⚠️ YouTube search failed for '{query}': {e}")
            return None

    async def playlist(self, limit: int, user: str, url: str) -> list[Track]:
        try:
            def _extract_playlist():
                cookie = self.get_cookies() if self.checked else None
                ydl_opts = {
                    "quiet": True,
                    "extract_flat": "in_playlist",
                    
                "js_runtimes": {"deno": {}},
                    "cookiefile": cookie
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)
                    
            plist = await asyncio.to_thread(_extract_playlist)
            tracks = []

            # Check for videos
            if not plist or "entries" not in plist or not plist["entries"]:
                return []

            for data in plist["entries"][:limit]:
                try:
                    duration_sec = data.get("duration")
                    is_live = data.get("is_live", False)
                    if duration_sec is None and is_live:
                        duration = "LIVE"
                        duration_sec = 0
                    else:
                        duration = utils.format_duration(int(duration_sec)) if duration_sec else "0:00"

                    raw_url = data.get("url") or ""
                    if raw_url.startswith(("http://", "https://")):
                        video_url = raw_url
                    else:
                        video_url = data.get("webpage_url") or (
                            f"https://youtube.com/watch?v={data.get('id')}"
                            if data.get("id") else raw_url
                        )

                    track = Track(
                        id=data.get("id", ""),
                        channel_name=data.get("uploader") or data.get("channel", ""),
                        duration=duration,
                        duration_sec=int(duration_sec) if duration_sec else 0,
                        title=(data.get("title", "Unknown")[:25]),
                        thumbnail=data.get("thumbnails", [{}])[-1].get("url", "").split("?")[0] if data.get("thumbnails") else "",
                        url=video_url,
                        user=user,
                        view_count="",
                    )
                    tracks.append(track)
                except Exception as e:
                    # Skip broken tracks
                    continue

            return tracks
        except Exception as e:
            logger.warning(f"⚠️ YouTube playlist extraction failed for '{url}': {e}")
            return []

    async def _extract_stream_url(self, url: str, video_id: str) -> Optional[str]:
        """Resolve a direct, playable audio URL without downloading anything
        to disk first. ffmpeg (via PyTgCalls) can read straight from this
        URL exactly like it already does for live streams, so playback can
        start almost immediately instead of waiting on a full download.
        Returns None (never raises) if resolution fails for any reason, so
        callers can safely fall back to the normal download path.
        """
        cookie = self.get_cookies()
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "cookiefile": cookie,
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "noplaylist": True,
            "socket_timeout": 20,
            "extractor_retries": 3,
            "nocheckcertificate": True,
            "geo_bypass": True,
            
                "js_runtimes": {"deno": {}},
        }

        def _extract():
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if not info:
                        return None
                    direct = info.get("url")
                    if direct:
                        return direct
                    for fmt in info.get("formats", []):
                        if fmt.get("acodec") != "none" and fmt.get("url"):
                            return fmt["url"]
                    return None
            except Exception as ex:
                logger.debug(
                    f"Direct stream URL extraction failed for {video_id}: {ex}"
                )
                return None

        try:
            return await asyncio.wait_for(asyncio.to_thread(_extract), timeout=15)
        except asyncio.TimeoutError:
            logger.debug(f"Direct stream URL extraction timed out for {video_id}")
            return None

    async def download(
        self,
        video_id: str,
        is_live: bool = False,
        video: bool = False,
        prefer_stream: bool = False,
    ) -> Optional[str]:
        """
        prefer_stream: when True (and video=False), try to resolve a direct
        playable URL first instead of downloading the whole file to disk -
        used for voice-chat playback, where ffmpeg can read straight from
        the URL and playback starts almost instantly. Leave False (default)
        for anything that needs a real local file on disk, e.g. the
        /download command which sends the file itself to the user.
        """
        url = self.base + video_id

        # Extract live stream URL
        if is_live:
            cookie = self.get_cookies()
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "cookiefile": cookie,
                "format": "bestaudio/best",
                "noplaylist": True,
                "socket_timeout": 20,
                "extractor_retries": 5,
                "sleep_interval_requests": 1,
                # android/ios clients don't need the nsig/JS-signature challenge
                # that the "web" client currently breaks on ("The page needs to
                # be reloaded." errors) - so put them first and fall back to web.
                
                "js_runtimes": {"deno": {}},
            }

            def _extract_url():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    try:
                        info = ydl.extract_info(url, download=False)
                        if not info:
                            return None

                        direct = info.get("url")
                        if direct:
                            return direct

                        # Find URL in formats
                        for fmt in info.get("formats", []):
                            if fmt.get("acodec") != "none" and fmt.get("url"):
                                return fmt["url"]

                        return info.get("manifest_url")
                    except yt_dlp.utils.ExtractorError as ex:
                        error_msg = str(ex)
                        if "not available" in error_msg.lower():
                            logger.error(
                                "Video format not available or region-blocked.")
                        else:
                            logger.error(
                                "Live stream URL extraction failed: %s", ex)
                        return None
                    except Exception as ex:
                        logger.error(
                            "Unexpected error during live stream extraction: %s", ex)
                        return None

            try:
                stream_url = await asyncio.wait_for(asyncio.to_thread(_extract_url), timeout=35)
            except asyncio.TimeoutError:
                logger.error("Live stream URL extraction timed out for %s", video_id)
                return None

            return stream_url

        # Let yt-dlp choose the best format
        filename_pattern = f"downloads/{video_id}"
        
        # Check existing files
        existing_files = [
            f for f in glob.glob(f"{filename_pattern}.*")
            if not f.endswith('.part')
        ]
        if video:
            # NOTE: .webm excluded — audio downloads from /play produce .webm
            # which are audio-only. Only trust .mp4/.mkv/.mov as cached video.
            video_candidates = [
                f for f in existing_files
                if Path(f).suffix.lower() in {".mp4", ".mkv", ".mov"}
            ]
            if video_candidates:
                return video_candidates[0]
        else:
            audio_candidates = [
                f for f in existing_files
                if Path(f).suffix.lower() in {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".wav", ".flac"}
            ]
            if audio_candidates:
                return audio_candidates[0]

            # Fallback to mp4 for audio
            container_fallbacks = [
                f for f in existing_files
                if Path(f).suffix.lower() in {".mp4", ".mkv", ".mov"}
            ]
            if container_fallbacks:
                return container_fallbacks[0]

            # --------------------------------------------------------------
            # **PERFORMANCE FIX**: don't wait for a full download before
            # playback can start. This is the #1 cause of "the bot takes
            # forever to play the song": the old code always downloaded the
            # entire audio file to disk first, so a 4-minute song meant
            # waiting for a multi-MB download over a slow connection before
            # a single second of audio was heard.
            #
            # Instead, when the caller says it's OK (prefer_stream=True,
            # used for voice-chat playback only - never for /download,
            # which needs a real file), resolve a direct playable stream
            # URL (same trick already used for live streams a few lines
            # above) and hand that straight to PyTgCalls/ffmpeg. Playback
            # then starts in roughly the time it takes to resolve one API
            # call, not a full download. If resolving fails for any reason
            # we transparently fall back to the old download-to-disk path
            # below, so nothing breaks.
            # --------------------------------------------------------------
            if prefer_stream:
                stream_url = await self._extract_stream_url(url, video_id)
                if stream_url:
                    return stream_url

        # Create downloads dir
        downloads_dir = Path("downloads")
        if not downloads_dir.exists():
            try:
                downloads_dir.mkdir(parents=True, exist_ok=True)
                logger.info("📁 Created downloads directory")
            except Exception as e:
                logger.error(f"❌ Cannot create downloads directory: {e}")
                return None

        # Serialize disk downloads per video_id. Without this, preload
        # (background) and the main play_next path can both end up
        # downloading the exact same video_id to disk at the same time -
        # two yt-dlp processes writing/renaming the same
        # "downloads/<id>.mp4.part" file. On Windows that's a hard file
        # lock, so whichever process renames second gets
        # "[WinError 32] ... being used by another process" and gives up.
        # A per-id lock means the second caller just waits for the first
        # download to finish and then reuses its file instead of racing it.
        dl_lock = self._download_locks.get(video_id)
        if dl_lock is None:
            dl_lock = asyncio.Lock()
            self._download_locks[video_id] = dl_lock

        async with dl_lock:
            # Someone else may have finished downloading this exact video
            # while we were waiting for the lock - reuse that file instead
            # of starting a second, redundant download.
            already = self._locate_download_file(video_id, video=video)
            if already:
                return already

            return await self._download_to_disk(video_id, url, video)

    async def _download_to_disk(self, video_id: str, url: str, video: bool) -> Optional[str]:
        # **PERFORMANCE FIX**: Use semaphore to limit concurrent downloads
        # Prevents bandwidth saturation when 15-20 groups download simultaneously
        async with self._download_semaphore:
            # Mark this video as actively downloading so the periodic
            # cleanup sweep (cleanup.py) never deletes its file mid-write.
            self.active_downloads.add(video_id)
            base_opts = {
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "quiet": True,
                "js_runtimes": {"deno": {}},
                "noplaylist": True,
                "geo_bypass": True,
                "no_warnings": True,
                "overwrites": False,
                "nocheckcertificate": True,
                "continuedl": True,
                "noprogress": True,
                # More parallel fragments = faster downloads
                "concurrent_fragment_downloads": 8,
                # Bigger chunks = far fewer HTTP round-trips = much faster
                "http_chunk_size": 10485760,  # 10MB chunks
                "socket_timeout": 30,
                "retries": 2,
                "fragment_retries": 2,
                "extractor_retries": 5,
                # No artificial delay between requests - this was adding
                # dead time to every single download
                "sleep_interval_requests": 0,
                # Retries file rename/replace on Windows instead of failing
                # outright (fixes "Unable to rename file" WinError 2 caused
                # by antivirus/OS briefly locking the .part file)
                "file_access_retries": 10,
                # android/ios clients skip the nsig/JS-signature challenge that
                # the "web" client currently breaks on ("The page needs to be
                # reloaded." errors) - try those first, fall back to web.
                
            }

            if video:
                # Download best video
                height_filter = ""
                if self._max_video_height and self._max_video_height > 0:
                    height_filter = f"[height<={self._max_video_height}]"
                format_chain = (
                    f"bestvideo[ext=mp4]{height_filter}+bestaudio[ext=m4a]/"
                    f"bestvideo{height_filter}+bestaudio/"
                    "bestvideo+bestaudio/best"
                )
                ydl_opts = {
                    **base_opts,
                    "format": format_chain,
                    "merge_output_format": "mp4",
                    "postprocessors": [
                        {
                            "key": "FFmpegVideoConvertor",
                            "preferedformat": "mp4",
                        }
                    ],
                }
            else:
                # Download best audio
                ydl_opts = {
                    **base_opts,
                    # "format": "bestaudio[ext=m4a]/bestaudio[acodec=opus]/bestaudio/best",
                    "format": "bestaudio/best",
                    "postprocessors": [],
                }

            # Signals that mean "YouTube flagged this specific cookie/client
            # combo as a bot", as opposed to a real "video doesn't exist"
            # error. These are worth retrying with a different cookie file
            # and/or client order - the same request often succeeds on the
            # very next attempt once a fresh identity is used.
            BOT_DETECTION_MARKERS = (
                "sign in to confirm",
                "not a bot",
                "confirm you're not a bot",
                "429",
                "too many requests",
                # JS-challenge (nsig) solving failed for this client/cookie
                # combo - a fresh cookie or different client often clears it.
                "the page needs to be reloaded",
            )

            def _is_bot_detection(error_msg: str) -> bool:
                low = error_msg.lower()
                return any(marker in low for marker in BOT_DETECTION_MARKERS)

            def _download(ydl_runtime_opts, bad_cookie_path: Optional[str]):
                """Returns (file_path_or_None, bot_detected: bool)."""
                ydl_instance = None
                try:
                    ydl_instance = yt_dlp.YoutubeDL(ydl_runtime_opts)
                    info = ydl_instance.extract_info(url, download=True)
                    if not info:
                        logger.error(f"❌ Failed to extract info for {video_id}")
                        return None, False

                    time.sleep(0.5)
                    located = self._locate_download_file(video_id, video=video)
                    if located:
                        return located, False
                    logger.error(f"❌ Download completed but file not found for: {video_id}")
                    return None, False
                except yt_dlp.utils.ExtractorError as ex:
                    error_msg = str(ex)
                    if "not available" in error_msg.lower():
                        logger.error(
                            "❌ Video not available: May be region-blocked or private.")
                        return None, False
                    elif "age" in error_msg.lower():
                        logger.error(
                            "❌ Age-restricted video: Cookies required.")
                        return None, False
                    bot_detected = _is_bot_detection(error_msg)
                    if bot_detected and bad_cookie_path:
                        logger.warning(
                            f"⚠️ Cookie {Path(bad_cookie_path).name} looks stale/blocked "
                            f"(bot detection), dropping it and retrying with another one."
                        )
                    else:
                        logger.error("❌ YouTube extraction failed: %s", ex)
                    return None, bot_detected
                except yt_dlp.utils.DownloadError as ex:
                    error_msg = str(ex)
                    recovered = self._locate_download_file(video_id, video=video)
                    if "unable to rename file" in error_msg.lower() and recovered:
                        logger.warning(
                            f"⚠️ Renaming failed for {video_id}, using recovered file {Path(recovered).name}"
                        )
                        return recovered, False
                    if "416" in error_msg or "Requested range not satisfiable" in error_msg:
                        logger.warning(f"⚠️ Range error for {video_id}, skipping")
                        return None, False
                    bot_detected = _is_bot_detection(error_msg)
                    if bot_detected and bad_cookie_path:
                        logger.warning(
                            f"⚠️ Cookie {Path(bad_cookie_path).name} looks stale/blocked "
                            f"(bot detection), dropping it and retrying with another one."
                        )
                    else:
                        logger.warning(f"⚠️ Download error for {video_id}: {ex}")
                    if recovered:
                        logger.warning(
                            f"⚠️ Using recovered file for {video_id} despite download error"
                        )
                        return recovered, False
                    return None, bot_detected
                except Exception as ex:
                    logger.warning(f"⚠️ Unexpected download error for {video_id}: {ex}")
                    return None, False
                finally:
                    if ydl_instance:
                        try:
                            ydl_instance.close()
                        except Exception:
                            pass

            # Build the list of attempts to try, in order:
            #   1. a random cookie file (if any are on disk)
            #   2. up to 2 more *different* cookie files, if bot-detected
            #   3. a final attempt with no cookie file at all - some videos
            #      don't need one, and it's a last resort before giving up.
            # Any cookie that trips bot-detection is deleted from disk so
            # it's never handed out again by get_cookies().
            tried_cookies: set[str] = set()
            attempts_left = 3
            result_path = None

            try:
                while attempts_left > 0:
                    attempts_left -= 1
                    cookie = self.get_cookies()
                    if cookie and cookie in tried_cookies:
                        # Ran out of fresh cookies to rotate through - fall
                        # back to a cookie-less attempt instead of retrying
                        # the exact same one.
                        cookie = None
                    if cookie:
                        tried_cookies.add(cookie)

                    ydl_opts_attempt = {**ydl_opts, "cookiefile": cookie}

                    result_path, bot_detected = await asyncio.to_thread(
                        _download, ydl_opts_attempt, cookie
                    )

                    if result_path:
                        break

                    if bot_detected and cookie:
                        try:
                            os.remove(cookie)
                            if os.path.basename(cookie) in self.cookies:
                                self.cookies.remove(os.path.basename(cookie))
                        except Exception:
                            pass

                    if not bot_detected:
                        # A non-bot-detection failure (video unavailable,
                        # age restricted, etc.) won't be fixed by
                        # retrying - stop.
                        break

                return result_path
            finally:
                self.active_downloads.discard(video_id)
