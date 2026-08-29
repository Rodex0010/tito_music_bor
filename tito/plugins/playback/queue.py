# ==============================================================================
# queue.py - Now Playing & Queue
# ==============================================================================
# Displays the currently playing track and upcoming queue list.
# ==============================================================================

from pyrogram import filters, types

from tito import app, config, db, lang, queue
from tito.helpers import Track, buttons, thumb, utils


@app.on_message(filters.command(["قائمة", "يعمل", "queue", "q"], prefixes=["", "/"]) & (filters.group | filters.channel) & ~app.bl_users)
@lang.language()
async def _queue_func(_, m: types.Message):
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass
    
    if not await db.get_call(m.chat.id):
        return await m.reply_text(m.lang["not_playing"])

    _reply = await m.reply_text(m.lang["queue_fetching"])
    _queue = queue.get_queue(m.chat.id)
    _media = _queue[0]
    _thumb = (
        await thumb.generate(_media)
        if isinstance(_media, Track)
        else config.DEFAULT_THUMB
    )
    _text = m.lang["queue_curr"].format(
        utils.esc(_media.url),
        utils.esc(_media.title[:50]),
        _media.duration,
        _media.user,
    )
    _queue.pop(0)

    if _queue:
        _text += "<blockquote expandable>"
        for i, media in enumerate(_queue, start=1):
            if i == 15:
                break
            _text += m.lang["queue_item"].format(
                i, utils.esc(media.title), media.duration  # Show 1, 2, 3... for queued songs
            )
        _text += "</blockquote>"

    _playing = await db.playing(m.chat.id)
    await _reply.edit_media(
        media=types.InputMediaPhoto(
            media=_thumb,
            caption=_text,
        ),
        reply_markup=buttons.queue_markup(
            m.chat.id,
            m.lang["playing"] if _playing else m.lang["paused"],
            _playing,
        ),
    )
