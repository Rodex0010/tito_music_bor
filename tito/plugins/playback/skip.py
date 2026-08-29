# ==============================================================================
# skip.py - Skip Track
# ==============================================================================
# Skips the current track and automatically starts the next one in queue.
# ==============================================================================

import asyncio
import logging
from pyrogram import filters, types
from pyrogram.errors import ChatSendPlainForbidden, ChatWriteForbidden

from tito import tune, app, db, lang
from tito.helpers import can_manage_vc, utils

logger = logging.getLogger(__name__)


@app.on_message(filters.command(["تخطي", "التالي", "skip", "next"], prefixes=["", "/"]) & (filters.group | filters.channel) & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _skip(_, m: types.Message):
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass
    
    if not await db.get_call(m.chat.id):
        try:
            return await m.reply_text(m.lang["not_playing"])
        except (ChatSendPlainForbidden, ChatWriteForbidden):
            return

    # If a previous skip/track-transition for this chat is still busy
    # downloading, don't fire another one on top of it - that race is what
    # was causing "session invalidated" aborts where nothing ends up
    # playing. Just let the one in-flight finish; it will play the
    # currently-current track anyway.
    if tune.get_lock(m.chat.id).locked():
        try:
            sent_msg = await m.reply_text("⏳ جاري التحميل بالفعل، لحظات...")
            await asyncio.sleep(3)
            try:
                await sent_msg.delete()
            except Exception:
                pass
        except (ChatSendPlainForbidden, ChatWriteForbidden):
            pass
        return

    await tune.play_next(m.chat.id)
    try:
        sent_msg = await m.reply_text(m.lang["play_skipped"].format(utils.actor_mention(m)))
    except (ChatSendPlainForbidden, ChatWriteForbidden):
        logger.warning("Cannot send plain text in media-only chat")
        return
    
    # Auto-delete after 5 seconds
    await asyncio.sleep(5)
    try:
        await sent_msg.delete()
    except Exception:
        pass
