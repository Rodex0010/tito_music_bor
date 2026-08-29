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


@app.on_message(filters.command(["تخطي", "اسكيب", "سكيب", "التالي", "skip", "next"], prefixes=["", "/"]) & (filters.group | filters.channel) & ~app.bl_users)
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
    # downloading, DON'T just show a "wait" message and drop the request -
    # that used to make skip feel laggy/unresponsive when someone tapped it
    # while the current one was still loading (nothing happened, the tap was
    # silently lost). tune.play_next() takes this same per-chat lock
    # internally, so calling it directly here already queues this skip to
    # run the instant the in-flight one finishes - we just let asyncio do
    # that naturally instead of hand-rolling a sleep-and-bail.
    busy = tune.get_lock(m.chat.id).locked()
    busy_msg = None
    if busy:
        try:
            busy_msg = await m.reply_text("⏳ تمام، هيتم التخطي حالاً...")
        except (ChatSendPlainForbidden, ChatWriteForbidden):
            busy_msg = None

    await tune.play_next(m.chat.id)

    if busy_msg:
        try:
            await busy_msg.delete()
        except Exception:
            pass

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
