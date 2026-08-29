# ==============================================================================
# start.py - Basics
# ==============================================================================
# Essential user-facing commands: /start, /help, /settings, etc.
# ==============================================================================

import html

from pyrogram import enums, errors, filters, types

from tito import app, config, db, lang, logger
from tito.helpers import buttons, utils


def _safe_html(value) -> str:
    """Escape dynamic values before inserting them into HTML captions."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


@app.on_message(
    filters.command(
        ["مساعدة", "الأوامر", "الاوامر", "help"],
        prefixes=["", "/"]
    )
    & filters.private
    & ~app.bl_users
)
@lang.language()
async def _help(_, m: types.Message):
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass

    try:
        await m.reply_photo(
            photo=config.START_IMG,
            caption=m.lang["help_menu"],
            reply_markup=buttons.help_markup(m.lang),
            quote=False,
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:
        # Fallback to text if photo/caption fails
        try:
            await m.reply_text(
                text=m.lang["help_menu"],
                reply_markup=buttons.help_markup(m.lang),
                quote=True,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            # Last fallback: send plain text without HTML parsing
            await m.reply_text(
                text=html.unescape(
                    m.lang["help_menu"]
                    .replace("<blockquote>", "")
                    .replace("</blockquote>", "")
                    .replace("<b>", "")
                    .replace("</b>", "")
                    .replace("<u>", "")
                    .replace("</u>", "")
                ),
                reply_markup=buttons.help_markup(m.lang),
                quote=True,
            )


@app.on_message(
    filters.command(
        ["بدء", "ابدأ", "ابدا", "start"],
        prefixes=["", "/"]
    )
)
@lang.language()
async def start(_, message: types.Message):

    # --------------------------------------------------------------------------
    # Delete /start command in groups
    # --------------------------------------------------------------------------
    if message.chat.type != enums.ChatType.PRIVATE:
        try:
            await message.delete()
        except Exception:
            pass

    # --------------------------------------------------------------------------
    # Ignore channel posts / anonymous admins
    # --------------------------------------------------------------------------
    if not message.from_user:
        return

    # --------------------------------------------------------------------------
    # Check blacklist
    # --------------------------------------------------------------------------
    if (
        message.from_user.id in app.bl_users
        and message.from_user.id not in db.notified
    ):
        return await message.reply_text(
            message.lang["bl_user_notify"],
            parse_mode=enums.ParseMode.HTML,
        )

    # --------------------------------------------------------------------------
    # Pinned reminder - sent first, before anything else, in the bot's DM
    # --------------------------------------------------------------------------
    if message.chat.type == enums.ChatType.PRIVATE:
        try:
            _salawat = await message.reply_text(
                "صلي على النبي وتبسم ❤️✨",
                quote=False,
            )
        except Exception as e:
            logger.warning(f"start: failed to send salawat message: {e}")
            _salawat = None

        if _salawat:
            try:
                await _salawat.pin(disable_notification=True, both_sides=True)
            except Exception as e:
                logger.warning(
                    f"start: failed to pin salawat message for "
                    f"{message.from_user.id}: {e}"
                )

    # --------------------------------------------------------------------------
    # /start help
    # --------------------------------------------------------------------------
    if (
        len(message.command) > 1
        and message.command[1].lower() == "help"
    ):
        return await _help(_, message)

    # --------------------------------------------------------------------------
    # Determine chat type
    # --------------------------------------------------------------------------
    private = message.chat.type == enums.ChatType.PRIVATE

    # --------------------------------------------------------------------------
    # Prepare safe dynamic values
    # --------------------------------------------------------------------------
    first_name = _safe_html(message.from_user.first_name)
    app_name = _safe_html(app.name)

    # --------------------------------------------------------------------------
    # Build welcome message
    # --------------------------------------------------------------------------
    try:
        if private:
            _text = message.lang["start_pm"].format(
                first_name,
                app_name,
            )
        else:
            _text = message.lang["start_gp"].format(
                app_name,
            )

    except Exception:
        # Safe fallback if language formatting fails
        if private:
            _text = f"<blockquote>أهلًا {first_name},</blockquote>\n<blockquote>أنا {app_name} 🎵</blockquote>"
        else:
            _text = f"<blockquote>أهلًا،\nأنا {app_name}</blockquote>"

    # --------------------------------------------------------------------------
    # Buttons
    # --------------------------------------------------------------------------
    key = buttons.start_key(
        message.lang,
        private,
        is_owner=(
            private
            and message.from_user.id == app.owner
        ),
    )

    # --------------------------------------------------------------------------
    # Send welcome photo
    # --------------------------------------------------------------------------
    try:
        await message.reply_photo(
            photo=config.START_IMG,
            caption=_text,
            reply_markup=key,
            quote=False,
            parse_mode=enums.ParseMode.HTML,
        )

    except errors.ChatSendPhotosForbidden:
        # Photos are not allowed
        await message.reply_text(
            text=_text,
            reply_markup=key,
            quote=False,
            parse_mode=enums.ParseMode.HTML,
        )

    except errors.exceptions.bad_request_400.EntityBoundsInvalid:
        # ----------------------------------------------------------------------
        # Telegram rejected the generated entities.
        #
        # Send the same message as plain text to prevent /start from crashing.
        # ----------------------------------------------------------------------
        plain_text = (
            _text
            .replace("<blockquote>", "")
            .replace("</blockquote>", "")
            .replace("<b>", "")
            .replace("</b>", "")
            .replace("<strong>", "")
            .replace("</strong>", "")
            .replace("<u>", "")
            .replace("</u>", "")
            .replace("<i>", "")
            .replace("</i>", "")
            .replace("<em>", "")
            .replace("</em>", "")
            .replace("<code>", "")
            .replace("</code>", "")
            .replace("<pre>", "")
            .replace("</pre>", "")
        )

        # Remove HTML links safely
        import re

        plain_text = re.sub(
            r"<a\b[^>]*>",
            "",
            plain_text,
            flags=re.IGNORECASE,
        )
        plain_text = re.sub(
            r"</a>",
            "",
            plain_text,
            flags=re.IGNORECASE,
        )

        # NOTE: <emoji id="..."> tags are intentionally kept here (not
        # stripped), and parse_mode stays HTML below (not DISABLED / no
        # html.unescape). The blockquote/bold/underline tags above were the
        # ones Telegram rejected as invalid entity bounds - the custom-emoji
        # tag on its own is valid HTML and still needs to be parsed to
        # render as a premium emoji instead of leaking as raw text, and
        # any escaped "&amp;"-style values (from _safe_html earlier) must
        # stay escaped for the HTML parser to read them correctly.

        try:
            await message.reply_photo(
                photo=config.START_IMG,
                caption=plain_text,
                reply_markup=key,
                quote=False,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            await message.reply_text(
                text=plain_text,
                reply_markup=key,
                quote=False,
                parse_mode=enums.ParseMode.HTML,
            )

    # --------------------------------------------------------------------------
    # Add private user to database
    # --------------------------------------------------------------------------
    if private:

        if await db.is_user(message.from_user.id):
            return

        # Log new user
        try:
            await utils.send_log(message)
        except Exception:
            pass

        # Add user
        return await db.add_user(message.from_user.id)


@app.on_message(
    filters.command(
        ["وضع_التشغيل", "اعدادات", "settings"],
        prefixes=["", "/"]
    )
    & (filters.group | filters.channel)
    & ~app.bl_users
)
@lang.language()
async def settings(_, message: types.Message):

    # Auto-delete command message
    try:
        await message.delete()
    except Exception:
        pass

    admin_only = await db.get_play_mode(
        message.chat.id
    )

    _language = "en"

    await utils.safe_text(
        message,
        message.lang["start_settings"].format(
            utils.esc(message.chat.title)
        ),
        reply_markup=buttons.settings_markup(
            message.lang,
            admin_only,
            _language,
            message.chat.id,
        ),
        quote=True,
    )


@app.on_message(
    filters.new_chat_members,
    group=7
)
@lang.language()
async def _new_member(_, message: types.Message):

    # Only work in supergroups and channels
    if message.chat.type not in (
        enums.ChatType.SUPERGROUP,
        enums.ChatType.CHANNEL,
    ):
        return await message.chat.leave()

    # Check each new member
    for member in message.new_chat_members:

        # Bot itself was added
        if member.id == app.id:

            # Chat already exists
            if await db.is_chat(message.chat.id):
                return

            # Add chat to database (kind known up-front from the chat type
            # check above, so the owner panel can list groups/channels
            # separately without needing an extra lookup later)
            kind = "channel" if message.chat.type == enums.ChatType.CHANNEL else "group"
            await db.add_chat(message.chat.id, kind=kind)