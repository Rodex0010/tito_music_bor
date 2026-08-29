# ==============================================================================
# calls.py - Voice Call Handler (PyTgCalls Integration)
# ==============================================================================
# This file manages voice/video chat functionality using PyTgCalls.
# Features:
# - Stream audio/video to Telegram voice chats
# - Playback controls (play, pause, resume, stop, seek)
# - Queue management (play next track automatically)
# - Multi-assistant support (load balancing)
# - Live stream support
# - Thumbnail updates during playback
# ==============================================================================

import asyncio
import logging
import re
from html import unescape

from ntgcalls import (
    ConnectionNotFound,
    TelegramServerError,
    TransportParseException,
)

from pyrogram import enums, errors
from pyrogram.errors import MessageIdInvalid
from pyrogram.types import InputMediaPhoto, Message

from pytgcalls import PyTgCalls, exceptions, types
from pytgcalls.pytgcalls_session import PyTgCallsSession

from tito import (
    app,
    config,
    db,
    lang,
    logger,
    preload,
    queue,
    userbot,
    yt,
)

from tito.helpers import Media, Track, buttons, thumb, utils


# ==============================================================================
# PyTgCalls Error Filter
# ==============================================================================

class PyTgCallsErrorFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()

        # Ignore UpdateGroupCall errors
        if "UpdateGroupCall" in msg:
            return False

        # Ignore connection errors after a call ends
        if "Connection with chat id" in msg and "not found" in msg:
            return False

        # Ignore InvalidStateError from PyTgCalls clear_call
        if "invalid state" in msg.lower() and "set_exception" in msg:
            return False

        return True


logging.getLogger("pyrogram.dispatcher").addFilter(
    PyTgCallsErrorFilter()
)


# ==============================================================================
# TgCall
# ==============================================================================

class TgCall(PyTgCalls):

    def __init__(self):
        self.clients = []

        # Maps assistant slot number:
        # 1 / 2 / 3 -> PyTgCalls client
        self.clients_by_num = {}

        # One lock for each chat to avoid playback conflicts
        self._chat_locks = {}

        # Session generation prevents stale background tasks
        self._session_gen = {}

        # Track transition index
        self._track_index = {}

        # Prevent duplicate StreamEnded transitions
        self._pending_transitions = set()

    # ==========================================================================
    # Chat Lock
    # ==========================================================================

    def get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._chat_locks:
            self._chat_locks[chat_id] = asyncio.Lock()

        return self._chat_locks[chat_id]

    # ==========================================================================
    # Edit Media With Retry
    # ==========================================================================

    async def _edit_media_with_retry(
        self,
        message: Message,
        media_obj: InputMediaPhoto,
        reply_markup,
    ):
        try:
            return await message.edit_media(
                media=media_obj,
                reply_markup=reply_markup,
            )

        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value + 1)

            try:
                return await message.edit_media(
                    media=media_obj,
                    reply_markup=reply_markup,
                )

            except Exception:
                return None

        except errors.MessageNotModified:
            return None

        except Exception:
            return None

    # ==========================================================================
    # Send Now Playing Photo With Full Fallback System
    # ==========================================================================

    async def _send_photo_with_retry(
        self,
        chat_id: int,
        photo,
        caption: str,
        reply_markup,
    ):
        """
        Send the now-playing panel safely.

        Primary:
            photo + caption + buttons

        Fallback 1:
            photo + buttons

        Fallback 2:
            photo + cleaned plain-text caption + buttons

        Final fallback:
            plain text + buttons

        This prevents ENTITY_TEXT_INVALID and other malformed
        Telegram entity errors from making the now-playing panel
        disappear completely.
        """

        # ----------------------------------------------------------------------
        # Primary attempt:
        # Photo + caption + buttons
        # ----------------------------------------------------------------------

        try:
            return await app.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                reply_markup=reply_markup,
            )

        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value + 1)

            try:
                return await app.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    reply_markup=reply_markup,
                )

            except Exception as e:
                logger.warning(
                    f"send_photo retry after FloodWait failed "
                    f"for {chat_id}: {e}"
                )

        except Exception as e:
            logger.warning(
                f"send_photo failed for {chat_id} "
                f"({type(e).__name__}: {e})"
            )

        # ----------------------------------------------------------------------
        # Fallback 1:
        # Send photo WITHOUT caption.
        #
        # This guarantees that the visual panel and buttons can still
        # appear even when Telegram rejects the caption entities.
        # ----------------------------------------------------------------------

        try:
            photo_message = await app.send_photo(
                chat_id=chat_id,
                photo=photo,
                reply_markup=reply_markup,
            )

            # ------------------------------------------------------------------
            # Try to add the original caption separately.
            # ------------------------------------------------------------------

            try:
                await app.edit_message_caption(
                    chat_id=chat_id,
                    message_id=photo_message.id,
                    caption=caption,
                    reply_markup=reply_markup,
                )

                return photo_message

            except Exception as e:
                logger.warning(
                    f"Could not add HTML caption for {chat_id}: "
                    f"{type(e).__name__}: {e}"
                )

            # ------------------------------------------------------------------
            # Fallback 2:
            # Remove ALL Telegram HTML/custom emoji entities and special chars.
            # ------------------------------------------------------------------

            plain_caption = caption

            # Remove custom emoji tags completely
            plain_caption = re.sub(
                r"<emoji[^>]*>.*?</emoji>",
                "",
                plain_caption,
                flags=re.IGNORECASE | re.DOTALL,
            )

            # Remove blockquote tags and their content (keep text inside)
            plain_caption = re.sub(
                r"</?blockquote[^>]*>",
                "",
                plain_caption,
                flags=re.IGNORECASE,
            )

            # Convert <br> to new lines
            plain_caption = re.sub(
                r"<br\s*/?>",
                "\n",
                plain_caption,
                flags=re.IGNORECASE,
            )

            # Remove all HTML tags
            plain_caption = re.sub(
                r"<[^>]+>",
                "",
                plain_caption,
            )

            # Decode HTML entities
            plain_caption = unescape(plain_caption)

            # Remove excessive whitespace
            plain_caption = re.sub(
                r"\s+",
                " ",
                plain_caption
            )

            plain_caption = plain_caption.strip()

            # Remove any non-printable characters
            plain_caption = re.sub(
                r'[^\x20-\x7E\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\u200C-\u200F\u202A-\u202E]+',
                '',
                plain_caption
            )

            # ------------------------------------------------------------------
            # Try updating the photo with cleaned plain text.
            # ------------------------------------------------------------------

            if plain_caption:
                try:
                    await app.edit_message_caption(
                        chat_id=chat_id,
                        message_id=photo_message.id,
                        caption=plain_caption,
                        reply_markup=reply_markup,
                    )
                    return photo_message

                except Exception as e2:
                    logger.warning(
                        f"Plain caption also failed for {chat_id}: {e2}"
                    )

            # If even the plain caption fails, return the photo without caption
            return photo_message

        # ----------------------------------------------------------------------
        # FloodWait while sending photo-only fallback
        # ----------------------------------------------------------------------

        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value + 1)

            try:
                return await app.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    reply_markup=reply_markup,
                )

            except Exception as e:
                logger.warning(
                    f"Photo fallback after FloodWait failed "
                    f"for {chat_id}: {e}"
                )

        except Exception as e:
            logger.warning(
                f"Photo-only fallback failed for {chat_id}: "
                f"{type(e).__name__}: {e}"
            )

        # ----------------------------------------------------------------------
        # Final fallback:
        # Send plain text + buttons (no photo).
        # ----------------------------------------------------------------------

        try:
            # Clean the text thoroughly
            plain_caption = re.sub(
                r"<emoji[^>]*>.*?</emoji>",
                "",
                caption,
                flags=re.IGNORECASE | re.DOTALL,
            )

            plain_caption = re.sub(
                r"<br\s*/?>",
                "\n",
                plain_caption,
                flags=re.IGNORECASE,
            )

            plain_caption = re.sub(
                r"</?blockquote[^>]*>",
                "",
                plain_caption,
                flags=re.IGNORECASE,
            )

            plain_caption = re.sub(
                r"<[^>]+>",
                "",
                plain_caption,
            )

            plain_caption = unescape(plain_caption)

            plain_caption = re.sub(
                r"\s+",
                " ",
                plain_caption
            )

            plain_caption = plain_caption.strip()

            # Remove non-printable chars
            plain_caption = re.sub(
                r'[^\x20-\x7E\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\u200C-\u200F\u202A-\u202E]+',
                '',
                plain_caption
            )

            if not plain_caption:
                plain_caption = "🎵 Now Playing"

            return await app.send_message(
                chat_id=chat_id,
                text=plain_caption,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )

        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value + 1)

            try:
                return await app.send_message(
                    chat_id=chat_id,
                    text="🎵 Now Playing",
                    reply_markup=reply_markup,
                    disable_web_page_preview=True,
                )

            except Exception as e:
                logger.error(
                    f"Final FloodWait fallback failed "
                    f"for {chat_id}: {e}"
                )

        except Exception as e:
            logger.error(
                f"All now-playing panel fallbacks failed "
                f"for {chat_id}: {e}"
            )

        # Absolute last resort: send just buttons
        try:
            return await app.send_message(
                chat_id=chat_id,
                text="🎵 Now Playing",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
        except Exception:
            return None

    # ==========================================================================
    # Pause
    # ==========================================================================

    async def pause(self, chat_id: int) -> bool:
        async with self.get_lock(chat_id):
            client = await db.get_assistant(chat_id)

            try:
                await client.pause(chat_id)

                await db.playing(
                    chat_id,
                    paused=True,
                )

                return True

            except (
                ConnectionNotFound,
                exceptions.NotInCallError,
            ):
                await db.playing(
                    chat_id,
                    paused=False,
                )

                await db.remove_call(chat_id)

                queue.clear(chat_id)

                logger.warning(
                    f"Pause requested but assistant not in call "
                    f"for {chat_id}, syncing state"
                )

                return False

            except Exception as e:
                await db.playing(
                    chat_id,
                    paused=False,
                )

                logger.error(
                    f"Pause failed for {chat_id}: {e}"
                )

                return False

    # ==========================================================================
    # Resume
    # ==========================================================================

    async def resume(self, chat_id: int) -> bool:
        async with self.get_lock(chat_id):
            client = await db.get_assistant(chat_id)

            try:
                await client.resume(chat_id)

                await db.playing(
                    chat_id,
                    paused=False,
                )

                return True

            except (
                ConnectionNotFound,
                exceptions.NotInCallError,
            ):
                await db.playing(
                    chat_id,
                    paused=False,
                )

                await db.remove_call(chat_id)

                queue.clear(chat_id)

                logger.warning(
                    f"Resume requested but assistant not in call "
                    f"for {chat_id}, syncing state"
                )

                return False

            except Exception as e:
                logger.error(
                    f"Resume failed for {chat_id}: {e}"
                )

                return False

    # ==========================================================================
    # Stop
    # ==========================================================================

    async def stop(self, chat_id: int) -> None:
        async with self.get_lock(chat_id):
            await self._stop_impl(chat_id)

    # ==========================================================================
    # Internal Stop
    # ==========================================================================

    async def _stop_impl(self, chat_id: int) -> None:
        self._session_gen[chat_id] = (
            self._session_gen.get(chat_id, 0) + 1
        )

        try:
            client = await db.get_assistant(chat_id)
        except Exception as e:
            logger.warning(
                f"Could not get assistant for {chat_id}: {e}"
            )
            client = None

        # Cancel active preload
        try:
            await preload.cancel_preload(chat_id)
        except Exception as e:
            logger.debug(
                f"Error cancelling preload for {chat_id}: {e}"
            )

        # Clear queue and database call state
        try:
            queue.clear(chat_id)
        except Exception as e:
            logger.debug(
                f"Error clearing queue for {chat_id}: {e}"
            )

        try:
            await db.remove_call(chat_id)
        except Exception as e:
            logger.warning(
                f"Error removing call state for {chat_id}: {e}"
            )

        # Leave voice chat
        if client is not None:
            try:
                await client.leave_call(
                    chat_id,
                    close=False,
                )

                await asyncio.sleep(0.5)

            except (
                ConnectionNotFound,
                exceptions.NotInCallError,
            ):
                pass

            except Exception as e:
                error_msg = str(e).lower()

                ignored_errors = [
                    "not in a call",
                    "not in the group call",
                    "groupcall_forbidden",
                    "no active group call",
                    "call was already stopped",
                    "call already stopped",
                    "call already disconnected",
                    "connection with chat id",
                ]

                if not any(
                    text in error_msg
                    for text in ignored_errors
                ):
                    logger.warning(
                        f"Error leaving call for {chat_id}: {e}"
                    )

    # ==========================================================================
    # Play Media
    # ==========================================================================

    async def play_media(
        self,
        chat_id: int,
        message: Message | None,
        media: Media | Track,
        seek_time: int = 0,
    ) -> None:
        async with self.get_lock(chat_id):
            await self._play_media_impl(
                chat_id,
                message,
                media,
                seek_time,
            )

    # ==========================================================================
    # Internal Play Media
    # ==========================================================================

    async def _play_media_impl(
        self,
        chat_id: int,
        message: Message | None,
        media: Media | Track,
        seek_time: int = 0,
    ) -> None:

        client = await db.get_assistant(chat_id)
        _lang = await lang.get_lang(chat_id)

        # --------------------------------------------------------------
        # Thumbnail
        # --------------------------------------------------------------

        if (
            config.THUMB_GEN
            and isinstance(media, Track)
        ):
            try:
                _thumb = await thumb.generate(media)
            except Exception as e:
                logger.warning(
                    f"Thumbnail generation failed for {chat_id}: {e}"
                )
                _thumb = config.DEFAULT_THUMB
        else:
            _thumb = config.DEFAULT_THUMB

        # --------------------------------------------------------------
        # Validate file
        # --------------------------------------------------------------

        if not media.file_path:
            if message:
                try:
                    await message.edit_text(
                        _lang["error_no_file"].format(
                            config.SUPPORT_CHAT
                        )
                    )
                except Exception:
                    pass
            else:
                logger.error(
                    f"No file path for media in {chat_id}"
                )

            return

        # --------------------------------------------------------------
        # Validate chat
        # --------------------------------------------------------------

        try:
            chat = await app.get_chat(chat_id)

            valid_chat_types = [
                enums.ChatType.SUPERGROUP,
                enums.ChatType.GROUP,
                enums.ChatType.CHANNEL,
            ]

            if chat.type not in valid_chat_types:
                logger.error(
                    f"Invalid chat type for {chat_id}: {chat.type}"
                )

                if message:
                    try:
                        await message.edit_text(
                            "❌ يمكن التشغيل داخل المجموعات والقنوات فقط."
                        )
                    except Exception:
                        pass

                return

        except errors.RPCError:
            raise

        # --------------------------------------------------------------
        # FFmpeg parameters
        # --------------------------------------------------------------

        if seek_time > 1:
            ffmpeg_params = (
                f"-ss {seek_time} "
                "-probesize 10M "
                "-analyzeduration 5M "
                "-rtbufsize 5M "
                "-fflags +genpts+igndts"
            )
        else:
            ffmpeg_params = (
                "-probesize 10M "
                "-analyzeduration 5M "
                "-rtbufsize 5M "
                "-fflags +genpts+igndts "
                "-sync ext"
            )

        # --------------------------------------------------------------
        # Audio / Video
        # --------------------------------------------------------------

        is_video = getattr(
            media,
            "video",
            False,
        )

        video_flags = (
            types.MediaStream.Flags.AUTO_DETECT
            if is_video
            else types.MediaStream.Flags.IGNORE
        )

        stream_kwargs = {
            "media_path": media.file_path,
            "audio_parameters": types.AudioQuality.STUDIO,
            "audio_flags": types.MediaStream.Flags.REQUIRED,
            "video_flags": video_flags,
            "ffmpeg_parameters": ffmpeg_params,
        }

        if is_video:
            h = config.VIDEO_MAX_HEIGHT or 720

            if h <= 360:
                width = 640
                fps = 15

            elif h <= 480:
                width = 854
                fps = 20

            elif h <= 720:
                width = 1280
                fps = 25

            else:
                width = 1920
                fps = 30

            stream_kwargs["video_parameters"] = (
                types.raw.VideoParameters(
                    width=width,
                    height=h,
                    frame_rate=fps,
                )
            )

        stream = types.MediaStream(
            **stream_kwargs
        )

        # --------------------------------------------------------------
        # Remove old/ghost stream
        # --------------------------------------------------------------

        try:
            await client.leave_call(
                chat_id,
                close=False,
            )

            await asyncio.sleep(0.3)

        except (
            ConnectionNotFound,
            exceptions.NotInCallError,
        ):
            pass

        except Exception as e:
            logger.debug(
                f"Error leaving old call for {chat_id}: {e}"
            )

        # --------------------------------------------------------------
        # Start playback
        # --------------------------------------------------------------

        max_retries = 3
        retry_delay = 1

        try:
            playback_started = False

            for attempt in range(max_retries):

                try:
                    await client.play(
                        chat_id=chat_id,
                        stream=stream,
                        config=types.GroupCallConfig(
                            auto_start=True
                        ),
                    )

                    playback_started = True
                    break

                except exceptions.NoActiveGroupCall as e:

                    if attempt < max_retries - 1:
                        logger.debug(
                            f"No active group call for {chat_id}; "
                            f"retrying "
                            f"{attempt + 1}/{max_retries}"
                        )

                        await asyncio.sleep(
                            retry_delay
                        )

                        continue

                    raise

                except errors.RPCError as e:

                    error_msg = str(e)

                    if (
                        "GROUPCALL_INVALID" in error_msg
                        or "GROUPCALL" in error_msg
                    ):

                        if attempt < max_retries - 1:
                            logger.debug(
                                f"Group call transition for {chat_id}; "
                                f"retrying "
                                f"{attempt + 1}/{max_retries}"
                            )

                            await asyncio.sleep(
                                retry_delay
                            )

                            continue

                        raise

                    raise

                except TransportParseException:

                    if attempt < max_retries - 1:
                        logger.debug(
                            f"Transport negotiation failed for "
                            f"{chat_id}; retrying "
                            f"{attempt + 1}/{max_retries}"
                        )

                        try:
                            await client.leave_call(
                                chat_id,
                                close=False,
                            )
                        except Exception:
                            pass

                        await asyncio.sleep(
                            retry_delay + 1
                        )

                        continue

                    raise

                except Exception as e:

                    error_msg = str(e).lower()

                    retryable = (
                        "cannot be initialized more than once"
                        in error_msg
                        or "connection"
                        in error_msg
                    )

                    if retryable and attempt < max_retries - 1:

                        logger.debug(
                            f"Connection error for {chat_id}; "
                            f"retrying "
                            f"{attempt + 1}/{max_retries}: {e}"
                        )

                        try:
                            await client.leave_call(
                                chat_id,
                                close=False,
                            )
                        except Exception:
                            pass

                        await asyncio.sleep(
                            retry_delay
                        )

                        continue

                    raise

            if not playback_started:
                raise RuntimeError(
                    "Playback could not be started."
                )

            # ----------------------------------------------------------
            # Update playback position
            # ----------------------------------------------------------

            if seek_time:
                media.time = seek_time
            else:
                media.time = 1

            # ----------------------------------------------------------
            # Create now-playing panel
            # ----------------------------------------------------------

            if not seek_time:

                await db.add_call(chat_id)

                # ======================================================
                # 🔥 MODIFIED: Beautiful now-playing text with blockquote
                # ======================================================
                text = (
                    "<blockquote> 🔴 ᴛʜᴇ ʀᴇǫᴜᴇꜱᴛᴇᴅ ꜱᴛʀᴇᴀᴍ ꜱᴛᴀʀᴛᴇᴅ 🎵</blockquote>\n"
                    "<blockquote>\n"
                    f"➤ ᴛɪᴛʟᴇ : {utils.esc(media.title)}\n"
                    f"➤ ᴅᴜʀᴀᴛɪᴏɴ : {media.duration}\n"
                    f"➤ ʀᴇǫᴜᴇꜱᴛᴇᴅ ʙʏ : {media.user}\n"
                    "</blockquote>\n"
                )

                # ------------------------------------------------------
                # Timer
                # ------------------------------------------------------

                if (
                    not media.is_live
                    and media.duration_sec
                ):

                    import time as time_module

                    played = media.time
                    duration = media.duration_sec

                    bar_length = 12

                    if duration <= 0:
                        percentage = 0
                    else:
                        percentage = min(
                            (played / duration) * 100,
                            100,
                        )

                    filled = int(
                        round(
                            bar_length
                            * percentage
                            / 100
                        )
                    )

                    filled = max(
                        0,
                        min(
                            filled,
                            bar_length,
                        ),
                    )

                    timer_bar = (
                        "—" * filled
                        + "●"
                        + "—" * (
                            bar_length - filled
                        )
                    )

                    if duration >= 3600:

                        played_time = (
                            time_module.strftime(
                                "%H:%M:%S",
                                time_module.gmtime(
                                    played
                                ),
                            )
                        )

                        total_time = (
                            time_module.strftime(
                                "%H:%M:%S",
                                time_module.gmtime(
                                    duration
                                ),
                            )
                        )

                    else:

                        played_time = (
                            time_module.strftime(
                                "%M:%S",
                                time_module.gmtime(
                                    played
                                ),
                            )
                        )

                        total_time = (
                            time_module.strftime(
                                "%M:%S",
                                time_module.gmtime(
                                    duration
                                ),
                            )
                        )

                    timer_text = (
                        f"{played_time} "
                        f"{timer_bar} "
                        f"{total_time}"
                    )

                    keyboard = buttons.controls(
                        chat_id,
                        timer=timer_text,
                    )

                else:

                    keyboard = buttons.controls(
                        chat_id
                    )

                # ------------------------------------------------------
                # Delete old command message
                # ------------------------------------------------------

                if message:
                    try:
                        await message.delete()
                    except Exception:
                        pass

                # ------------------------------------------------------
                # IMPORTANT:
                # Release chat lock while sending Telegram message.
                # This prevents FloodWait from blocking playback.
                # ------------------------------------------------------

                lock = self.get_lock(chat_id)

                current_session = (
                    self._session_gen.get(
                        chat_id,
                        0,
                    )
                )

                lock.release()

                try:

                    sent_photo = (
                        await self._send_photo_with_retry(
                            chat_id=chat_id,
                            photo=_thumb,
                            caption=text,
                            reply_markup=keyboard,
                        )
                    )

                finally:
                    await lock.acquire()

                # ------------------------------------------------------
                # Stop stale message after stop/new session
                # ------------------------------------------------------

                if (
                    self._session_gen.get(
                        chat_id,
                        0,
                    )
                    != current_session
                ):
                    logger.info(
                        f"Session invalidated while sending "
                        f"now-playing panel for {chat_id}"
                    )
                    return

                # ------------------------------------------------------
                # Save panel message ID
                # ------------------------------------------------------

                if sent_photo:
                    try:
                        media.message_id = sent_photo.id
                    except Exception:
                        media.message_id = 0

                # ------------------------------------------------------
                # Preload next tracks
                # ------------------------------------------------------

                try:
                    asyncio.create_task(
                        preload.start_preload(
                            chat_id,
                            count=2,
                        )
                    )
                except Exception as e:
                    logger.debug(
                        f"Error starting preload for {chat_id}: {e}"
                    )

        # ==============================================================
        # Playback errors
        # ==============================================================

        except FileNotFoundError:

            if message:
                try:
                    await message.edit_text(
                        _lang["error_no_file"].format(
                            config.SUPPORT_CHAT
                        )
                    )
                except Exception:
                    pass

            await self._play_next_impl(
                chat_id
            )

        except exceptions.NoActiveGroupCall:

            await self._stop_impl(
                chat_id
            )

            if message:
                try:
                    await message.edit_text(
                        _lang["error_vc_disabled"]
                    )
                except Exception:
                    pass

        except errors.RPCError as e:

            error_str = str(e)

            forbidden_errors = [
                "CHAT_ADMIN_REQUIRED",
                "phone.CreateGroupCall",
                "GROUPCALL_FORBIDDEN",
                "GROUPCALL_CREATE_FORBIDDEN",
                "VOICE_MESSAGES_FORBIDDEN",
            ]

            if any(
                error in error_str
                for error in forbidden_errors
            ):

                await self._stop_impl(
                    chat_id
                )

                if message:
                    try:
                        await message.edit_text(
                            _lang["error_vc_disabled"]
                        )
                    except Exception:
                        pass

            elif (
                "GROUPCALL_INVALID"
                in error_str
                or "GROUPCALL"
                in error_str
            ):

                await self._stop_impl(
                    chat_id
                )

                if message:
                    try:
                        await message.edit_text(
                            _lang["error_no_call"]
                        )
                    except Exception:
                        pass

            else:

                logger.error(
                    f"RPC error in play_media for "
                    f"{chat_id}: {e}",
                    exc_info=True,
                )

                await self._stop_impl(
                    chat_id
                )

        except exceptions.NoAudioSourceFound:

            if message:
                try:
                    await message.edit_text(
                        _lang["error_no_audio"]
                    )
                except Exception:
                    pass

            await self._play_next_impl(
                chat_id
            )

        except TransportParseException:

            logger.warning(
                f"Transport not found for {chat_id} "
                f"after retries, stopping."
            )

            await self._stop_impl(
                chat_id
            )

            if message:
                try:
                    await message.edit_text(
                        _lang["error_no_call"]
                    )
                except Exception:
                    pass

        except (
            ConnectionNotFound,
            TelegramServerError,
        ):

            await self._stop_impl(
                chat_id
            )

            if message:
                try:
                    await message.edit_text(
                        _lang["error_tg_server"]
                    )
                except Exception:
                    pass

        except TimeoutError as e:

            logger.warning(
                f"⏱️ Timeout joining voice chat "
                f"{chat_id}: {e}"
            )

            await self._stop_impl(
                chat_id
            )

            if message:
                try:
                    await message.edit_text(
                        "⏱️ <b>ᴄᴏɴɴᴇᴄᴛɪᴏɴ ᴛɪᴍᴇᴅ ᴏᴜᴛ!</b>\n\n"
                        "<blockquote>"
                        "ꜰᴀɪʟᴇᴅ ᴛᴏ ᴊᴏɪɴ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ. "
                        "ᴘʟᴇᴀꜱᴇ ᴄʜᴇᴄᴋ ʏᴏᴜʀ ɴᴇᴛᴡᴏʀᴋ "
                        "ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ."
                        "</blockquote>"
                    )
                except Exception:
                    pass

            await asyncio.sleep(2)

            await self._play_next_impl(
                chat_id
            )

        except Exception as e:

            logger.error(
                f"Unexpected error in play_media "
                f"for {chat_id}: {e}",
                exc_info=True,
            )

            await self._stop_impl(
                chat_id
            )

            if message:
                try:
                    await message.edit_text(
                        f"❌ Playback error: "
                        f"{str(e)[:100]}"
                    )
                except Exception:
                    pass

    async def replay(self, chat_id: int) -> None:

        try:

            if not await db.get_call(
                chat_id
            ):
                return

            media = queue.get_current(
                chat_id
            )

            if not media:
                return

            _lang = await lang.get_lang(
                chat_id
            )

            msg = await app.send_message(
                chat_id=chat_id,
                text=_lang["play_again"],
            )

            await self.play_media(
                chat_id,
                msg,
                media,
            )

        except Exception as e:

            logger.error(
                f"Error in replay for {chat_id}: {e}",
                exc_info=True,
            )

    async def seek_stream(
        self,
        chat_id: int,
        seconds: int,
    ) -> bool:

        try:

            if not await db.get_call(
                chat_id
            ):
                return False

            media = queue.get_current(
                chat_id
            )

            if not media:
                return False

            if media.is_live:
                return False

            seconds = max(
                0,
                int(seconds),
            )

            if (
                media.duration_sec
                and seconds >= media.duration_sec
            ):
                seconds = max(
                    0,
                    media.duration_sec - 1,
                )

            media.time = seconds

            try:
                msg = await app.get_messages(
                    chat_id,
                    media.message_id,
                )
            except Exception:
                msg = None

            if not msg:

                _lang = await lang.get_lang(
                    chat_id
                )

                msg = await app.send_message(
                    chat_id=chat_id,
                    text=_lang["seeking"],
                )

            await self.play_media(
                chat_id,
                msg,
                media,
                seek_time=seconds,
            )

            return True

        except Exception as e:

            logger.warning(
                f"Seek stream failed for "
                f"{chat_id}: {e}"
            )

            return False

    async def play_next(
        self,
        chat_id: int,
        expected_index: int = None,
    ) -> None:

        lock = self.get_lock(chat_id)

        async with lock:

            self._pending_transitions.discard(
                chat_id
            )

            if (
                expected_index is not None
                and self._track_index.get(
                    chat_id,
                    0,
                ) != expected_index
            ):
                logger.info(
                    f"Skipping stale play_next "
                    f"for {chat_id}"
                )
                return

            self._track_index[
                chat_id
            ] = self._track_index.get(
                chat_id,
                0,
            ) + 1

            await self._play_next_impl(
                chat_id
            )

    async def _play_next_impl(
        self,
        chat_id: int,
    ) -> None:

        try:

            if not await db.get_call(
                chat_id
            ):
                return

            loop_mode = await db.get_loop(
                chat_id
            )

            # ==========================================================
            # LOOP CURRENT TRACK
            # ==========================================================

            if loop_mode == 1:

                media = queue.get_current(
                    chat_id
                )

                if media:

                    _lang = await lang.get_lang(
                        chat_id
                    )

                    try:

                        msg = await app.send_message(
                            chat_id=chat_id,
                            text=_lang[
                                "play_again"
                            ],
                        )

                        await self._play_media_impl(
                            chat_id,
                            msg,
                            media,
                        )

                    except errors.ChannelPrivate:

                        logger.warning(
                            f"Bot removed from "
                            f"{chat_id}, cleaning up"
                        )

                        try:
                            await self._stop_impl(
                                chat_id                            )
                        except Exception as leave_ex:
                            logger.debug(
                                f"Could not stop call "
                                f"for {chat_id}: "
                                f"{leave_ex}"
                            )

                        try:
                            await db.rm_chat(
                                chat_id
                            )
                        except Exception:
                            pass

                return

            # ==========================================================
            # GET NEXT TRACK
            # ==========================================================

            media = queue.get_next(
                chat_id
            )

            # ==========================================================
            # LOOP QUEUE
            # ==========================================================

            if not media and loop_mode == 10:

                all_items = queue.get_all(
                    chat_id
                )

                if all_items:

                    first_track = all_items[0]

                    _lang = await lang.get_lang(
                        chat_id
                    )

                    try:

                        msg = await app.send_message(
                            chat_id=chat_id,
                            text="🔁 Looping queue...",
                        )

                        # ------------------------------------------------
                        # Download first track if necessary
                        # ------------------------------------------------

                        if not first_track.file_path:

                            is_live = getattr(
                                first_track,
                                "is_live",
                                False,
                            )

                            lock = self.get_lock(
                                chat_id
                            )

                            current_session = (
                                self._session_gen.get(
                                    chat_id,
                                    0,
                                )
                            )

                            lock.release()

                            try:

                                first_track.file_path = (
                                    await yt.download(
                                        first_track.id,
                                        is_live=is_live,
                                        video=getattr(
                                            first_track,
                                            "video",
                                            False,
                                        ),
                                        prefer_stream=True,
                                    )
                                )

                            finally:

                                await lock.acquire()

                            if (
                                self._session_gen.get(
                                    chat_id,
                                    0,
                                )
                                != current_session
                            ):
                                logger.info(
                                    f"Session invalidated "
                                    f"during looping download "
                                    f"for {chat_id}"
                                )
                                return

                            if (
                                queue.get_current(
                                    chat_id
                                )
                                != first_track
                            ):
                                logger.info(
                                    f"Queue altered during "
                                    f"looping download "
                                    f"for {chat_id}"
                                )
                                return

                        first_track.message_id = (
                            msg.id
                        )

                        await self._play_media_impl(
                            chat_id,
                            msg,
                            first_track,
                        )

                    except errors.ChannelPrivate:

                        logger.warning(
                            f"Bot removed from "
                            f"{chat_id}, cleaning up"
                        )

                        await self._stop_impl(
                            chat_id
                        )

                        try:
                            await db.rm_chat(
                                chat_id
                            )
                        except Exception:
                            pass

                    except Exception as e:

                        logger.error(
                            f"Loop queue playback "
                            f"failed for {chat_id}: {e}",
                            exc_info=True,
                        )

                return

            # ==========================================================
            # DELETE PREVIOUS NOW PLAYING MESSAGE
            # ==========================================================

            try:

                if (
                    media
                    and media.message_id
                ):

                    await app.delete_messages(
                        chat_id=chat_id,
                        message_ids=media.message_id,
                        revoke=True,
                    )

                    media.message_id = 0

            except Exception as e:

                logger.debug(
                    f"Could not delete previous "
                    f"message in {chat_id}: {e}"
                )

            # ==========================================================
            # QUEUE FINISHED
            # ==========================================================

            if not media:

                if config.QUEUE_END_MESSAGE:

                    _lang = await lang.get_lang(
                        chat_id
                    )

                    try:

                        await app.send_message(
                            chat_id=chat_id,
                            text=_lang.get(
                                "queue_end_message",
                                "✅ Queue finished. "
                                "Stream ended automatically.",
                            ),
                        )

                    except Exception as e:

                        logger.debug(
                            f"Could not send queue end "
                            f"message in {chat_id}: {e}"
                        )

                await self._stop_impl(
                    chat_id
                )

                return

            # ==========================================================
            # DOWNLOAD NEXT TRACK
            # ==========================================================

            _lang = await lang.get_lang(
                chat_id
            )

            msg = None

            if not media.file_path:

                is_live = getattr(
                    media,
                    "is_live",
                    False,
                )

                lock = self.get_lock(
                    chat_id
                )

                current_session = (
                    self._session_gen.get(
                        chat_id,
                        0,
                    )
                )

                lock.release()

                try:

                    media.file_path = (
                        await yt.download(
                            media.id,
                            is_live=is_live,
                            video=getattr(
                                media,
                                "video",
                                False,
                            ),
                            prefer_stream=True,
                        )
                    )

                except Exception as e:

                    logger.error(
                        f"Download failed for "
                        f"{chat_id}: {e}",
                        exc_info=True,
                    )

                    media.file_path = None

                finally:

                    await lock.acquire()

                if (
                    self._session_gen.get(
                        chat_id,
                        0,
                    )
                    != current_session
                ):
                    logger.info(
                        f"Session invalidated during "
                        f"play_next download "
                        f"for {chat_id}"
                    )
                    return

                if (
                    queue.get_current(
                        chat_id
                    )
                    != media
                ):
                    logger.info(
                        f"Queue altered during "
                        f"play_next download "
                        f"for {chat_id}"
                    )
                    return

                if not media.file_path:

                    logger.error(
                        f"Could not download next "
                        f"track ({getattr(media, 'id', '?')}) "
                        f"for {chat_id}, trying the "
                        f"following track in queue"
                    )

                    # Don't kill playback just because one track failed
                    # to download (e.g. a transient WinError32 rename
                    # clash, region block, etc). Drop the broken track
                    # and recurse into the next one in the queue - only
                    # _stop_impl if the queue actually runs out (handled
                    # by the "QUEUE FINISHED" branch above on the next
                    # pass).
                    try:
                        _lang_fail = await lang.get_lang(chat_id)
                        await app.send_message(
                            chat_id=chat_id,
                            text=_lang_fail.get(
                                "download_failed_skipping",
                                "⚠️ تعذر تحميل المقطع، جاري تخطيه...",
                            ),
                        )
                    except Exception:
                        pass

                    # Recursing straight into _play_next_impl (not
                    # queue.remove_current + recurse) matters here: the
                    # "GET NEXT TRACK" step above already popped the old
                    # current off and left this failed track sitting at
                    # the front. queue.get_next() on the next pass pops
                    # *that* (the failed one) and hands back whatever
                    # comes after it - exactly the "drop the broken
                    # track, try the next" behaviour we want. Popping it
                    # here too would skip an extra, unrelated track.
                    await self._play_next_impl(
                        chat_id
                    )

                    return

            # ==========================================================
            # SEND PLAY NEXT MESSAGE
            # ==========================================================

            try:

                msg = await app.send_message(
                    chat_id=chat_id,
                    text=_lang[
                        "play_next"
                    ],
                )

            except errors.FloodWait as fw:

                logger.warning(
                    f"FloodWait in play_next "
                    f"for {chat_id}: "
                    f"skipping status message "
                    f"({fw.value}s)"
                )

                msg = None

            except errors.ChannelPrivate:

                logger.warning(
                    f"Bot removed from "
                    f"{chat_id}, cleaning up"
                )

                await self._stop_impl(
                    chat_id
                )

                try:
                    await db.rm_chat(
                        chat_id
                    )
                except Exception:
                    pass

                return

            except Exception as e:

                logger.error(
                    f"Failed to send play_next "
                    f"message for {chat_id}: {e}"
                )

                msg = None

            # ==========================================================
            # PLAY NEXT TRACK
            # ==========================================================

            media.message_id = (
                msg.id
                if msg
                else 0
            )

            if msg:

                await self._play_media_impl(
                    chat_id,
                    msg,
                    media,
                )

            else:

                logger.info(
                    f"Playing next track "
                    f"for {chat_id} "
                    f"without message update"
                )

                await self._play_media_impl(
                    chat_id,
                    None,
                    media,
                )

            # ==========================================================
            # PRELOAD NEXT TRACKS
            # ==========================================================

            try:

                asyncio.create_task(
                    preload.start_preload(
                        chat_id,
                        count=2,
                    )
                )

            except Exception as e:

                logger.debug(
                    f"Error starting preload "
                    f"after play_next for "
                    f"{chat_id}: {e}"
                )

        except Exception as e:

            logger.error(
                f"Error in play_next for "
                f"{chat_id}: {e}",
                exc_info=True,
            )

            try:
                await self._stop_impl(
                    chat_id
                )
            except Exception:
                pass

    async def ping(self) -> float:

        if not self.clients:
            return 0.0

        pings = []

        for client in self.clients:

            try:
                ping = client.ping

                if ping is not None:
                    pings.append(
                        float(ping)
                    )

            except Exception as e:

                logger.debug(
                    f"Could not read PyTgCalls "
                    f"ping: {e}"
                )

        if not pings:
            return 0.0

        return round(
            sum(pings) / len(pings),
            2,
        )

    async def decorators(
        self,
        client: PyTgCalls,
    ) -> None:

        @client.on_update()
        async def update_handler(
            _,
            update: types.Update,
        ):

            try:

                # ======================================================
                # STREAM ENDED
                # ======================================================

                if isinstance(
                    update,
                    types.StreamEnded,
                ):

                    if (
                        update.stream_type
                        == types.StreamEnded.Type.AUDIO
                    ):

                        chat_id = update.chat_id

                        expected_index = (
                            self._track_index.get(
                                chat_id,
                                0,
                            )
                        )

                        if (
                            chat_id
                            not in self._pending_transitions
                        ):

                            self._pending_transitions.add(
                                chat_id
                            )

                            asyncio.create_task(
                                self.play_next(
                                    chat_id,
                                    expected_index,
                                )
                            )

                # ======================================================
                # CHAT UPDATE
                # ======================================================

                elif isinstance(
                    update,
                    types.ChatUpdate,
                ):

                    if update.status in [

                        types.ChatUpdate.Status.KICKED,

                        types.ChatUpdate.Status.LEFT_GROUP,

                        types.ChatUpdate.Status.CLOSED_VOICE_CHAT,

                    ]:

                        await self.stop(
                            update.chat_id
                        )

            except (
                ConnectionNotFound,
                exceptions.NotInCallError,
                TelegramServerError,
            ):

                return

            except Exception as e:

                logger.debug(
                    f"Ignoring PyTgCalls "
                    f"update handler error: {e}"
                )

    async def boot(self) -> None:

        # Prevent PyTgCalls notice
        PyTgCallsSession.notice_displayed = True

        # ==============================================================
        # MAP USERBOT CLIENTS TO ASSISTANT NUMBERS
        # ==============================================================

        num_by_identity = {}

        if hasattr(
            userbot,
            "one",
        ):

            num_by_identity[
                id(userbot.one)
            ] = 1

        if hasattr(
            userbot,
            "two",
        ):

            num_by_identity[
                id(userbot.two)
            ] = 2

        if hasattr(
            userbot,
            "three",
        ):

            num_by_identity[
                id(userbot.three)
            ] = 3

        # ==============================================================
        # START ALL ASSISTANTS
        # ==============================================================

        for ub in userbot.clients:

            try:

                client = PyTgCalls(
                    ub,
                    cache_duration=100,
                )

                await client.start()

                self.clients.append(
                    client
                )

                num = num_by_identity.get(
                    id(ub)
                )

                if num is not None:

                    self.clients_by_num[
                        num
                    ] = client

                await self.decorators(
                    client
                )

                logger.info(
                    f"📞 PyTgCalls assistant "
                    f"{num or '?'} started."
                )

            except Exception as e:

                logger.error(
                    f"Failed to start PyTgCalls "
                    f"assistant {num or '?'}: {e}",
                    exc_info=True,
                )

        # ==============================================================
        # FINAL STATUS
        # ==============================================================

        if self.clients:

            logger.info(
                f"📞 PyTgCalls client(s) started: "
                f"{len(self.clients)}"
            )

        else:

            logger.error(
                "❌ No PyTgCalls clients were started!"
            )