# ==============================================================================
# broadcast.py - Mass Messaging
# ==============================================================================
# Owner-only command to send a message to all active groups and users.
# Sudo users can still call the command, but they get a "currently disabled"
# message instead of the broadcast being executed.
# Supports various flags for pinning, forwarding, copying, etc.
#
# Formatting fix: the command text is now read as HTML (message.text.html) and
# every send uses parse_mode=HTML, so bold / blockquote / links / custom emoji
# survive the broadcast instead of arriving as plain or raw-tag text.
# ==============================================================================

import os
import asyncio
from typing import List, Optional, Tuple

from pyrogram import enums, errors, filters, types

from config import Config
from tito import app, db, lang, logger


# ------------------------------------------------------------------------------
# Owner ID (read from the Config class in config.py)
# ------------------------------------------------------------------------------
OWNER_ID: int = Config().OWNER_ID

# Message shown to sudo users who try to use the broadcast commands
BROADCAST_DISABLED_TEXT = "⚠️ أمر البث معطّل حاليًا."

HTML = enums.ParseMode.HTML


def _is_owner(message: types.Message) -> bool:
    """Return True only if the sender is the bot owner."""
    return bool(message.from_user and message.from_user.id == OWNER_ID)


def _html(value) -> str:
    """Return the HTML-formatted version of a Pyrogram Str (or plain str)."""
    if not value:
        return ""
    return getattr(value, "html", value)


class _SkipChat(Exception):
    """Raised when a chat should be skipped with a readable reason."""


# Global flag to track if a broadcast is currently running
broadcasting: bool = False


@app.on_message(filters.command(["بث", "broadcast"], prefixes=["", "/"]) & app.sudo_filter)
@lang.language()
async def broadcast_message(_, message: types.Message) -> None:

    # Owner-only: sudo users are told the command is disabled
    if not _is_owner(message):
        try:
            await message.reply_text(BROADCAST_DISABLED_TEXT)
        except Exception:
            pass
        try:
            await message.delete()
        except Exception:
            pass
        return

    # Auto-delete command message
    try:
        await message.delete()
    except Exception:
        pass

    global broadcasting

    # Check if another broadcast is already running
    if broadcasting:
        return await message.reply_text(message.lang["gcast_active"])

    # Determine if the command was a reply to a media message
    media_message = None
    media_group = None
    if message.reply_to_message:
        media_message = message.reply_to_message

        # Check if it's part of a media group (album)
        if media_message.media_group_id:
            try:
                media_group = await _get_media_group(message.chat.id, media_message)
            except Exception:
                # If fetching media group fails, just use single message
                pass

    # Parse command: extract flags and actual message.
    # IMPORTANT: use .html so formatting and custom emoji are preserved.
    flags, broadcast_text = _parse_broadcast_command(
        message.text.html if message.text else ""
    )

    # Validate: either text or media must be present
    if not broadcast_text and not media_message:
        return await message.reply_text(message.lang["gcast_usage"])

    # Determine recipients based on flags
    groups, users = await _get_broadcast_recipients(flags)
    all_chats = groups + users

    if not all_chats:
        return await message.reply_text(
            "❌ No recipients found. Make sure the bot is added to groups or has users."
        )

    # Set broadcasting flag
    broadcasting = True
    sent = await message.reply_text(message.lang["gcast_start"])

    # Log broadcast initiation (show the actual content being broadcast)
    await _log_broadcast_start(message, broadcast_text, media_message)
    await asyncio.sleep(5)

    # Perform the broadcast (supports text and media messages)
    try:
        success_groups, success_users, failed_chats, pinned_count = await _send_broadcast(
            broadcast_text, groups, users, sent, media_message, flags, message.lang, media_group
        )
    finally:
        # Always reset the flag, even if something unexpected blows up
        broadcasting = False

    # Send completion message
    await _send_broadcast_completion(
        message, sent, success_groups, success_users, failed_chats, media_message, pinned_count
    )


@app.on_message(filters.command(["ايقاف_البث", "وقف_البث", "stopbroadcast", "cancelbroadcast"], prefixes=["", "/"]) & app.sudo_filter)
@lang.language()
async def stop_broadcast(_, message: types.Message) -> None:

    # Owner-only: sudo users are told the command is disabled
    if not _is_owner(message):
        try:
            await message.reply_text(BROADCAST_DISABLED_TEXT)
        except Exception:
            pass
        try:
            await message.delete()
        except Exception:
            pass
        return

    # Auto-delete command message
    try:
        await message.delete()
    except Exception:
        pass

    global broadcasting

    if not broadcasting:
        return await message.reply_text(message.lang["gcast_inactive"])

    broadcasting = False

    # Log broadcast stop
    await (await app.send_message(
        chat_id=app.logger,
        text=message.lang["gcast_stop_log"].format(
            message.from_user.id,
            message.from_user.mention
        )
    )).pin(disable_notification=False)

    await message.reply_text(message.lang["gcast_stop"])


async def _get_media_group(chat_id: int, message: types.Message) -> Optional[List[types.Message]]:
    if not message.media_group_id:
        return None

    media_group_id = message.media_group_id
    messages = []

    # Telegram typically sends media group messages with consecutive IDs,
    # so search a window around the replied message.
    search_range = 20

    try:
        start_id = max(1, message.id - search_range)
        end_id = message.id + search_range

        for msg_id in range(start_id, end_id + 1):
            try:
                msg = await app.get_messages(chat_id, msg_id)
                if msg and getattr(msg, "media_group_id", None) == media_group_id:
                    messages.append(msg)
            except Exception:
                continue

        messages.sort(key=lambda x: x.id)
        return messages if messages else None
    except Exception:
        return None


def _parse_broadcast_command(text: str) -> Tuple[List[str], str]:
    """
    Parse broadcast command to extract flags and message.

    Args:
        text: The full command text (HTML-formatted).

    Returns:
        Tuple of (flags list, message text)
    """
    if not text:
        return [], ""

    # Split command from the rest (preserve everything after command)
    parts = text.split(None, 1)
    if len(parts) < 2:
        return [], ""

    remaining_text = parts[1]

    # Extract flags (words starting with '-') from the beginning
    flags = []
    lines = remaining_text.split("\n")
    first_line_parts = lines[0].split()

    # Collect flags only from first line
    message_start_index = 0
    for i, part in enumerate(first_line_parts):
        if part.startswith("-"):
            flags.append(part)
            message_start_index = i + 1
        else:
            break

    # Reconstruct message preserving all newlines and formatting
    if message_start_index > 0:
        first_line_without_flags = " ".join(first_line_parts[message_start_index:])
        if len(lines) > 1:
            message_text = first_line_without_flags + "\n" + "\n".join(lines[1:])
        else:
            message_text = first_line_without_flags
    else:
        message_text = remaining_text

    return flags, message_text.strip()


async def _get_broadcast_recipients(flags: List[str]) -> Tuple[List[int], List[int]]:
    groups = []
    users = []

    # Include groups + channels (db.get_chats() returns both) unless -nochat
    if "-nochat" not in flags:
        groups = await db.get_chats()

    # Users are included by default; -nouser opts out.
    # -user is kept as a harmless no-op for anyone used to the old syntax.
    if "-nouser" not in flags:
        users = await db.get_users()

    return groups, users


async def _log_broadcast_start(
    message: types.Message,
    broadcast_text: str = "",
    media_message: Optional[types.Message] = None,
) -> None:
    # Build a preview of what's actually being sent, instead of the raw command text
    _has_real_media = media_message and any(
        getattr(media_message, attr, None) for attr in (
            "photo", "video", "audio", "voice", "document", "animation", "sticker",
        )
    )

    if _has_real_media:
        _kind = "media"
        if media_message.photo:
            _kind = "📷 صورة"
        elif getattr(media_message, "video", None):
            _kind = "🎬 فيديو"
        elif getattr(media_message, "audio", None):
            _kind = "🎵 صوت"
        elif getattr(media_message, "voice", None):
            _kind = "🎙️ رسالة صوتية"
        elif getattr(media_message, "document", None):
            _kind = "📄 ملف"
        elif getattr(media_message, "animation", None):
            _kind = "🎞️ GIF"
        elif getattr(media_message, "sticker", None):
            _kind = "🖼️ ملصق"

        preview = f"{_kind}"
        if broadcast_text:
            preview += f"\n{broadcast_text}"
        elif media_message.caption:
            preview += f"\n{_html(media_message.caption)}"
    elif media_message:
        # Replying to a plain text message: show that message's own text
        preview = (
            broadcast_text
            or _html(media_message.text)
            or _html(media_message.caption)
            or "-"
        )
    else:
        preview = broadcast_text or "-"

    async def _send_log():
        return await app.send_message(
            chat_id=app.logger,
            text=message.lang["gcast_log"].format(
                message.from_user.id,
                message.from_user.mention,
                preview,
            ),
        )

    try:
        log_message = await _send_log()
    except errors.FloodWait as fw:
        await asyncio.sleep(fw.value + 1)
        log_message = await _send_log()

    try:
        await log_message.pin(disable_notification=False)
    except errors.FloodWait as fw:
        await asyncio.sleep(fw.value + 1)
        try:
            await log_message.pin(disable_notification=False)
        except Exception:
            pass
    except Exception:
        pass


# ------------------------------------------------------------------------------
# Sending helpers (shared by the normal path and the FloodWait retry path)
# ------------------------------------------------------------------------------

def _photo_file_id(msg: types.Message) -> str:
    photo = msg.photo
    return photo.file_id if hasattr(photo, "file_id") else photo[-1].file_id


def _build_album(media_group: List[types.Message], text: str) -> list:
    """Build InputMedia list for copying an album without the forward tag."""
    media_list = []
    for idx, msg in enumerate(media_group):
        if idx == 0:
            if text:
                caption = text
            else:
                caption = msg.caption.html if msg.caption else None
        else:
            caption = None

        if msg.photo:
            media_list.append(types.InputMediaPhoto(
                media=_photo_file_id(msg), caption=caption, parse_mode=HTML))
        elif getattr(msg, "video", None):
            media_list.append(types.InputMediaVideo(
                media=msg.video.file_id, caption=caption, parse_mode=HTML))
        elif getattr(msg, "audio", None):
            media_list.append(types.InputMediaAudio(
                media=msg.audio.file_id, caption=caption, parse_mode=HTML))
        elif getattr(msg, "document", None):
            media_list.append(types.InputMediaDocument(
                media=msg.document.file_id, caption=caption, parse_mode=HTML))
    return media_list


async def _send_one(
    chat_id: int,
    text: str,
    media_message: Optional[types.Message],
    media_group: Optional[List[types.Message]],
    flags: List[str],
) -> Optional[types.Message]:
    """
    Send the broadcast payload to a single chat.

    Returns the first sent message (for pinning), or None.
    Raises _SkipChat when there is nothing valid to send.
    """
    copy_mode = "-copy" in flags

    # Priority 1: album
    if media_group:
        if copy_mode:
            media_list = _build_album(media_group, text)
            if not media_list:
                raise _SkipChat("No valid media in group")
            sent_messages = await app.send_media_group(chat_id=chat_id, media=media_list)
            return sent_messages[0] if sent_messages else None

        first = None
        for msg in media_group:
            try:
                fwd = await msg.forward(chat_id)
            except errors.FloodWait:
                raise
            except Exception:
                continue
            if first is None:
                first = fwd
        if first is None:
            raise _SkipChat("Failed to forward media group")
        return first

    # Priority 2: single media / replied message
    if media_message:
        if not copy_mode:
            return await media_message.forward(chat_id)

        m = media_message
        caption = text if text else (m.caption or "")
        # When no new text is given, keep the original entities (blockquote,
        # bold, custom emoji...). Pyrogram ignores parse_mode if entities are set.
        caption_entities = None if text else (m.caption_entities or None)

        if m.photo:
            return await app.send_photo(
                chat_id=chat_id, photo=_photo_file_id(m), caption=caption,
                caption_entities=caption_entities, parse_mode=HTML)
        if getattr(m, "video", None):
            return await app.send_video(
                chat_id=chat_id, video=m.video.file_id, caption=caption,
                caption_entities=caption_entities, parse_mode=HTML)
        if getattr(m, "audio", None):
            return await app.send_audio(
                chat_id=chat_id, audio=m.audio.file_id, caption=caption,
                caption_entities=caption_entities, parse_mode=HTML)
        if getattr(m, "voice", None):
            return await app.send_voice(
                chat_id=chat_id, voice=m.voice.file_id, caption=caption,
                caption_entities=caption_entities, parse_mode=HTML)
        if getattr(m, "document", None):
            return await app.send_document(
                chat_id=chat_id, document=m.document.file_id, caption=caption,
                caption_entities=caption_entities, parse_mode=HTML)
        if getattr(m, "animation", None):
            return await app.send_animation(
                chat_id=chat_id, animation=m.animation.file_id, caption=caption,
                caption_entities=caption_entities, parse_mode=HTML)
        if getattr(m, "sticker", None):
            return await app.send_sticker(chat_id=chat_id, sticker=m.sticker.file_id)

        # Text-only message: copy the text with its formatting
        message_text = text if text else (m.text or m.caption or "")
        message_entities = None if text else (m.entities or m.caption_entities or None)
        if not message_text:
            raise _SkipChat("Empty message")
        return await app.send_message(
            chat_id, message_text, entities=message_entities, parse_mode=HTML)

    # Priority 3: plain text
    return await app.send_message(chat_id, text, parse_mode=HTML)


async def _pin_if_requested(
    sent_message: Optional[types.Message], flags: List[str], chat_id: int
) -> int:
    """Pin the sent message when -pin / -pinloud is set. Returns 1 if pinned."""
    if not sent_message:
        return 0

    if "-pin" in flags:
        silent = True
    elif "-pinloud" in flags:
        silent = False
    else:
        return 0

    try:
        await sent_message.pin(disable_notification=silent)
        return 1
    except Exception as pin_ex:
        logger.warning(
            f"Broadcast: pin failed for {chat_id} "
            f"({type(pin_ex).__name__}: {pin_ex})")
        return 0


async def _send_broadcast(
    text: str,
    groups: List[int],
    users: List[int],
    status_message: types.Message,
    media_message: Optional[types.Message] = None,
    flags: Optional[List[str]] = None,
    lang: Optional[dict] = None,
    media_group: Optional[List[types.Message]] = None,
) -> Tuple[int, int, str, int]:
    global broadcasting

    if flags is None:
        flags = []

    success_groups = 0
    success_users = 0
    failed_log = ""
    pinned_count = 0
    group_ids = set(groups)
    all_chats = groups + users
    total_chats = len(all_chats)

    for index, chat_id in enumerate(all_chats, start=1):
        # Check if broadcast was stopped
        if not broadcasting:
            await status_message.edit_text(
                lang["gcast_stopped"].format(success_groups, success_users)
            )
            break

        # Update progress every 50 chats
        if index % 50 == 0:
            try:
                await status_message.edit_text(
                    f"📤 Broadcasting...\n\n"
                    f"Progress: {index}/{total_chats}\n"
                    f"✅ Groups: {success_groups}\n"
                    f"✅ Users: {success_users}"
                )
            except Exception:
                pass

        try:
            try:
                sent_message = await _send_one(
                    chat_id, text, media_message, media_group, flags)
            except errors.FloodWait as fw:
                # Wait, then retry the same chat once (don't stop the broadcast)
                try:
                    await status_message.edit_text(
                        f"⏳ Flood wait triggered. Waiting {fw.value} seconds...\n\n"
                        f"Progress: {index}/{total_chats}\n"
                        f"Don't worry, broadcast will continue!"
                    )
                except Exception:
                    pass

                await asyncio.sleep(fw.value + 5)

                try:
                    sent_message = await _send_one(
                        chat_id, text, media_message, media_group, flags)
                except Exception as retry_ex:
                    failed_log += f"{chat_id} - FloodWait retry failed: {retry_ex}\n"
                    continue

            pinned_count += await _pin_if_requested(sent_message, flags, chat_id)

            if chat_id in group_ids:
                success_groups += 1
            else:
                success_users += 1

            # Anti-flood delay
            await asyncio.sleep(0.3)

        except _SkipChat as skip:
            failed_log += f"{chat_id} - {skip}\n"
            continue

        except errors.UserIsBlocked:
            failed_log += f"{chat_id} - User blocked bot\n"
            continue

        except errors.ChatWriteForbidden:
            failed_log += f"{chat_id} - No write permission\n"
            continue

        except errors.ChannelPrivate:
            # Bot was removed from channel/group - remove from database
            if chat_id in group_ids:
                try:
                    await db.rm_chat(chat_id)
                    failed_log += f"{chat_id} - Removed from group (cleaned from database)\n"
                except Exception:
                    failed_log += f"{chat_id} - Channel private (bot not member)\n"
            else:
                failed_log += f"{chat_id} - Channel private\n"
            continue

        except errors.PeerIdInvalid:
            # Invalid chat ID - remove from database
            if chat_id in group_ids:
                try:
                    await db.rm_chat(chat_id)
                    failed_log += f"{chat_id} - Invalid ID (cleaned from database)\n"
                except Exception:
                    failed_log += f"{chat_id} - Invalid chat ID\n"
            else:
                failed_log += f"{chat_id} - Invalid user ID\n"
            continue

        except Exception as ex:
            # Log failed send but CONTINUE to next chat
            failed_log += f"{chat_id} - {type(ex).__name__}: {str(ex)}\n"
            continue

    return success_groups, success_users, failed_log, pinned_count


async def _send_broadcast_completion(
    message: types.Message,
    status_message: types.Message,
    success_groups: int,
    success_users: int,
    failed_log: str,
    media_message: Optional[types.Message] = None,
    pinned_count: int = 0,
) -> None:
    media_type = "text"
    if media_message:
        if media_message.photo:
            media_type = "photo"
        elif getattr(media_message, "video", None):
            media_type = "video"
        elif getattr(media_message, "audio", None):
            media_type = "audio"
        elif getattr(media_message, "document", None):
            media_type = "document"
        elif getattr(media_message, "animation", None):
            media_type = "animation"
        elif getattr(media_message, "sticker", None):
            media_type = "sticker"

    completion_text = message.lang["gcast_end"].format(
        success_groups, success_users)
    if media_message:
        completion_text += f"\n📎 Media type: {media_type}"
    if pinned_count:
        completion_text += f"\n📌 Pinned: {pinned_count}"

    # If there were failures, send the error file to the log channel instead
    # of the chat where the command was run (keeps group chats clean)
    if failed_log:
        error_file = "errors.txt"
        with open(error_file, "w", encoding="utf-8") as f:
            f.write(failed_log)

        try:
            await app.send_document(
                chat_id=app.logger,
                document=error_file,
                caption=completion_text,
            )
        except Exception:
            pass
        os.remove(error_file)

    await status_message.edit_text(completion_text)
