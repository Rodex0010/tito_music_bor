# ==============================================================================
# emoji_patch.py - Force Custom Emojis Across The Whole Bot
# ==============================================================================
# Instead of relying on every plugin to remember to call
# `apply_custom_emojis()` (which only happened automatically for strings that
# went through the lang.py / locales system), this patches Pyrogram's Client
# class itself - once, at import time - so ANY outgoing text or caption from
# ANY part of the bot (plugins, helpers, keyword auto-replies, error
# messages, future code, etc.) gets the swap applied automatically.
#
# Covers, at the Client level (every Message.reply_*/edit_* shortcut
# ultimately calls one of these under the hood, so patching here is enough -
# no need to touch dozens of plugin files):
#   - send_message / edit_message_text          (text=...)
#   - send_photo / send_audio / send_video /
#     send_document / send_animation / send_voice /
#     edit_message_caption                       (caption=...)
#
# Fallback behaviour is unchanged: apply_custom_emojis() only swaps an emoji
# if a custom_emoji_id has actually been configured in core/emojis.py - any
# emoji left as "" there is sent as the normal unicode emoji, so nothing ever
# breaks or goes missing if custom emojis aren't set up.
# ==============================================================================

import functools

import pyrogram

from tito.core.emojis import apply_custom_emojis

# method_name -> name of the keyword/positional arg that holds the text
_TEXT_METHODS = {
    "send_message": "text",
    "edit_message_text": "text",
}

_CAPTION_METHODS = {
    "send_photo": "caption",
    "send_audio": "caption",
    "send_video": "caption",
    "send_document": "caption",
    "send_animation": "caption",
    "send_voice": "caption",
    "edit_message_caption": "caption",
}

_installed = False


def _wrap(method, arg_name, arg_index):
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        if arg_name in kwargs:
            kwargs[arg_name] = apply_custom_emojis(kwargs[arg_name])
        elif len(args) > arg_index:
            args = list(args)
            args[arg_index] = apply_custom_emojis(args[arg_index])
            args = tuple(args)
        return await method(self, *args, **kwargs)

    return wrapper


def install() -> None:
    """Patch pyrogram.Client so every outgoing text/caption gets custom emojis.

    Safe to call more than once - only patches on the first call.
    """
    global _installed
    if _installed:
        return

    # chat_id is always the first positional arg after self, so text/caption
    # (the next declared param) sits at index 1 for every method below.
    ARG_INDEX = 1

    for name, arg_name in {**_TEXT_METHODS, **_CAPTION_METHODS}.items():
        original = getattr(pyrogram.Client, name, None)
        if original is None:
            continue  # method renamed/removed upstream - skip gracefully
        setattr(pyrogram.Client, name, _wrap(original, arg_name, ARG_INDEX))

    _installed = True
