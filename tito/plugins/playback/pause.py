# ==============================================================================
# pause.py - Pause Command
# ==============================================================================
# /pause command to suspend playback in the voice chat.
# ==============================================================================

import logging
from pyrogram import filters, types
from pyrogram.errors import ChatSendPlainForbidden, ChatWriteForbidden

from tito import tune, app, db, lang
from tito.helpers import buttons, can_manage_vc, utils

logger = logging.getLogger(__name__)


@app.on_message(filters.command(["ايقاف", "pause"], prefixes=["", "/"]) & (filters.group | filters.channel) & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _pause(_, m: types.Message):
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

    if not await db.playing(m.chat.id):
        try:
            return await m.reply_text(m.lang["play_already_paused"])
        except (ChatSendPlainForbidden, ChatWriteForbidden):
            return

    await tune.pause(m.chat.id)
    try:
        await m.reply_text(
            text=m.lang["play_paused"].format(utils.actor_mention(m)),
            reply_markup=buttons.controls(m.chat.id),
        )
    except (ChatSendPlainForbidden, ChatWriteForbidden):
        logger.warning("Cannot send text in media-only chat")
