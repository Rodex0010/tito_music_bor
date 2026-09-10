# ==============================================================================
# auto_join.py - Auto-add assistants when the bot is promoted to admin
# ==============================================================================
# When the BOT account is made admin in a group/channel, every configured
# assistant (userbot) account is automatically joined into that chat, so
# there's no need to add them manually before /play works.
# ==============================================================================

import asyncio

from pyrogram import filters, types, errors, enums

from tito import app, config, logger, userbot
from tito.helpers import bot_is_member


async def _get_invite_link(chat: types.Chat) -> str | None:
    if chat.username:
        return f"https://t.me/{chat.username}"
    try:
        link = chat.invite_link
        if not link:
            link = await app.export_chat_invite_link(chat.id)
        return link
    except Exception as e:
        logger.warning(f"auto_join: couldn't get invite link for {chat.id}: {e}")
        return None


async def _join_assistant(client, chat_id: int, invite_link: str) -> None:
    # Hard gate: never join an assistant anywhere the bot isn't confirmed
    # present, even though this handler only fires on a bot-promotion
    # event. Re-checking here (instead of trusting the event) means this
    # still holds even if something else ever calls _join_assistant.
    if not await bot_is_member(chat_id):
        logger.warning(f"auto_join: blocked - bot not confirmed present in {chat_id}")
        return

    try:
        member = await app.get_chat_member(chat_id, client.id)
        if member and member.status != enums.ChatMemberStatus.LEFT:
            return  # already in the chat
    except Exception:
        pass  # not in chat yet, continue to join

    try:
        await client.join_chat(invite_link)
        logger.info(f"auto_join: assistant @{client.username} joined {chat_id}")

    except errors.UserAlreadyParticipant:
        pass

    except errors.InviteRequestSent:
        # bot is admin here, so it can approve the assistant's join request
        try:
            await app.approve_chat_join_request(chat_id, client.id)
        except Exception as e:
            logger.warning(f"auto_join: couldn't approve join request in {chat_id}: {e}")

    except errors.FloodWait as fw:
        await asyncio.sleep(fw.value + 1)
        try:
            await client.join_chat(invite_link)
        except Exception:
            pass

    except Exception as e:
        logger.warning(f"auto_join: assistant @{client.username} failed to join {chat_id}: {e}")


@app.on_chat_member_updated(filters.group | filters.channel)
async def bot_promoted(_, update: types.ChatMemberUpdated):

    new = update.new_chat_member
    old = update.old_chat_member

    # only care about the BOT itself being (re)promoted to admin
    if not new or not new.user or new.user.id != app.id:
        return

    if new.status != enums.ChatMemberStatus.ADMINISTRATOR:
        return

    # avoid re-triggering every time perms are edited while already admin
    if old and old.status == enums.ChatMemberStatus.ADMINISTRATOR:
        return

    chat = update.chat
    invite_link = await _get_invite_link(chat)
    if not invite_link:
        return

    await asyncio.gather(*[
        _join_assistant(client, chat.id, invite_link)
        for client in userbot.clients
    ])
