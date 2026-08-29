# ==============================================================================
# resume.py - Resume Command
# ==============================================================================
# /resume command to unpause playback in the voice chat.
# ==============================================================================

import logging
from pyrogram import filters, types
from pyrogram.errors import ChatSendPlainForbidden, ChatWriteForbidden

from tito import tune, app, db, lang
from tito.helpers import buttons, can_manage_vc, utils

logger = logging.getLogger(__name__)


@app.on_message(filters.command(["استمرار", "resume"], prefixes=["", "/"]) & (filters.group | filters.channel) & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _resume(_, m: types.Message):
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

    if await db.playing(m.chat.id):
        try:
            return await m.reply_text(m.lang["play_not_paused"])
        except (ChatSendPlainForbidden, ChatWriteForbidden):
            return

    await tune.resume(m.chat.id)
    try:
        await m.reply_text(
            text=m.lang["play_resumed"].format(utils.actor_mention(m)),
            reply_markup=buttons.controls(m.chat.id),
        )
    except (ChatSendPlainForbidden, ChatWriteForbidden):
        logger.warning("Cannot send text in media-only chat")
