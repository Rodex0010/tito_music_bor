# ==============================================================================
# assistant_cleanup.py - Assistant Containment Sweep
# ==============================================================================
# Enforces one invariant: an assistant/userbot account is never left sitting
# in a group or channel that the BOT (app) isn't currently a member of.
#
# This runs automatically once at every startup (see the call wired into
# tito/__main__.py) and can also be triggered on demand by the owner/sudo
# with "تنضيف_الحسابات" / "cleanupassistants" - useful right after rotating
# a leaked session, to immediately strip out anything an assistant joined
# outside of this codebase's own join_chat() calls.
#
# _guard.bot_is_member() is the single source of truth this checks against,
# same helper every join_chat() call site in the bot uses before joining.
# ==============================================================================

import asyncio

from pyrogram import enums, filters, types

from tito import app, logger, userbot
from tito.helpers import bot_is_member

# Only groups/channels count - a private chat (DM) isn't something an
# assistant "joins", so there's nothing to sweep there.
_CHAT_KINDS = (
    enums.ChatType.GROUP,
    enums.ChatType.SUPERGROUP,
    enums.ChatType.CHANNEL,
)


async def sweep_client(client) -> tuple[int, int]:
    """Walk every dialog a single assistant is in and leave any
    group/channel the bot isn't confirmed present in. Returns
    (left_count, checked_count)."""
    left = 0
    checked = 0

    try:
        async for dialog in client.get_dialogs():
            chat = dialog.chat
            if chat.type not in _CHAT_KINDS:
                continue

            checked += 1

            if await bot_is_member(chat.id):
                continue

            try:
                await client.leave_chat(chat.id)
                left += 1
                logger.info(
                    f"assistant_cleanup: @{getattr(client, 'username', client.id)} "
                    f"left {chat.id} ('{chat.title}') - bot isn't there."
                )
            except Exception as e:
                logger.warning(
                    f"assistant_cleanup: couldn't leave {chat.id} for "
                    f"@{getattr(client, 'username', client.id)}: {e}"
                )

            # Gentle pacing so a big cleanup doesn't trip Telegram's flood
            # limits on the assistant account.
            await asyncio.sleep(0.5)

    except Exception as e:
        logger.warning(
            f"assistant_cleanup: dialog sweep failed for "
            f"@{getattr(client, 'username', client.id)}: {e}"
        )

    return left, checked


async def sweep_all_assistants() -> tuple[int, int]:
    """Sweep every currently-connected assistant. Returns
    (total_left, total_checked) across all of them."""
    total_left = 0
    total_checked = 0

    for client in userbot.clients:
        left, checked = await sweep_client(client)
        total_left += left
        total_checked += checked

    logger.info(
        f"assistant_cleanup: sweep done - left {total_left} stray chat(s) "
        f"out of {total_checked} checked across {len(userbot.clients)} assistant(s)."
    )
    return total_left, total_checked


@app.on_message(
    filters.command(["تنضيف_الحسابات", "cleanupassistants"], prefixes=["", "/"])
    & app.sudo_filter
)
async def cleanup_assistants_command(_, m: types.Message) -> None:
    if not userbot.clients:
        return await m.reply_text(
            "<blockquote>⚠️ <b>مفيش أي أسستنت شغال دلوقتي.</b></blockquote>"
        )

    sent = await m.reply_text(
        "<blockquote>🧹 <b>بفحص كل الأسستنتس وباخرجهم من أي مكان البوت مش موجود فيه...</b></blockquote>"
    )

    total_left, total_checked = await sweep_all_assistants()

    await sent.edit_text(
        "<blockquote>✅ <b>تم التنظيف.</b>\n\n"
        f"اتفحص {total_checked} شات.\n"
        f"اتشال (غادر) {total_left} شات كان البوت مش موجود فيه.</blockquote>"
    )
