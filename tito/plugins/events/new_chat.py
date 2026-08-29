# ==============================================================================
# new_chat.py - Group Join/Leave Logs
# ==============================================================================
# Logs whenever the bot is added to or kicked from a group to the logger channel.
# ==============================================================================

from pyrogram import enums, filters, types
from pyrogram.errors import ChatAdminRequired

from tito import app, config, db
from tito.helpers import utils


# ------------------------------------------------------------------------------
# SELF-HEAL: register groups/channels the bot is already sitting in
# ------------------------------------------------------------------------------
# add_chat() used to only ever get called from the "bot was just added"
# events below. That means any group/channel the bot was already a member
# of before this tracking existed (or where the join event was missed for
# any reason - offline restart, privacy mode, etc.) never made it into the
# database - invisible to the owner-panel broadcast and the /بث command,
# even though the bot is actively playing music there.
#
# This catches ANY incoming message from a group/channel that isn't in the
# DB yet and registers it right then, so broadcast reach self-heals without
# anyone needing to remove/re-add the bot anywhere. Runs in its own handler
# group so it never blocks/interferes with the normal command handlers.
# ------------------------------------------------------------------------------
@app.on_message((filters.group | filters.channel) & ~filters.service, group=99)
async def _ensure_chat_registered(_, message: types.Message):
    try:
        if await db.is_chat(message.chat.id):
            return
        kind = "channel" if message.chat.type == enums.ChatType.CHANNEL else "group"
        await db.add_chat(message.chat.id, kind=kind)
    except Exception:
        pass


# ------------------------------------------------------------------------------
# Channels don't send a "new_chat_members" service message when the bot is
# added — Telegram only reports it through a chat-member update. This handler
# catches that case so channels get registered/logged just like groups.
# ------------------------------------------------------------------------------
@app.on_chat_member_updated(filters.channel)
async def _channel_added(_, update: types.ChatMemberUpdated):

    if not update.new_chat_member or update.new_chat_member.user.id != app.id:
        return

    # Only act the first time we see this channel
    if await db.is_chat(update.chat.id):
        return

    chat = update.chat
    chat_name = utils.esc(chat.title)
    chat_id = chat.id
    chat_username = f"@{chat.username}" if chat.username else "ᴘʀɪᴠᴀᴛᴇ ᴄʜᴀɴɴᴇʟ"

    added_by = update.from_user
    added_by_name = added_by.mention if added_by else "ᴜɴᴋɴᴏᴡɴ"

    await db.add_chat(chat_id, kind="channel")

    text = f"""<blockquote>🟢 <b>˹˹ᴛɪᴛᴏ ꭙ ᴍᴜꜱɪᴄ˼ ᴀᴅᴅᴇᴅ ɪɴ ᴀ ɴᴇᴡ ᴄʜᴀɴɴᴇʟ</b></blockquote>

<blockquote>
🔖 <b>ᴄʜᴀɴɴᴇʟ ɴᴀᴍᴇ:</b> {chat_name}
🆔 <b>ᴄʜᴀɴɴᴇʟ ɪᴅ:</b> <code>{chat_id}</code>
👤 <b>ᴄʜᴀɴɴᴇʟ ᴜꜱᴇʀɴᴀᴍᴇ:</b> {chat_username}
🔗 <b>ᴄʜᴀɴɴᴇʟ ʟɪɴᴋ:</b> {f"https://t.me/{chat.username}" if chat.username else "ᴄʟɪᴄᴋ ʜᴇʀᴇ"}
🤵 <b>ᴀᴅᴅᴇᴅ ʙʏ:</b> {added_by_name}
</blockquote>
"""

    try:
        await app.send_photo(
            chat_id=config.LOGGER_ID,
            photo=config.START_IMG,
            caption=text,
        )
    except Exception as e:
        print(f"Failed to send new channel notification: {e}")


# Mirror of _channel_added: cleans the channel out of the DB the moment the
# bot is kicked/leaves, so the owner panel and reconciliation stay accurate
# without anyone needing to do anything manually.
@app.on_chat_member_updated(filters.channel)
async def _channel_removed(_, update: types.ChatMemberUpdated):

    if not update.new_chat_member or update.new_chat_member.user.id != app.id:
        return

    status = update.new_chat_member.status
    if status.name not in ("LEFT", "BANNED"):
        return

    if not await db.is_chat(update.chat.id):
        return

    await db.rm_chat(update.chat.id)


@app.on_message(filters.new_chat_members & filters.group)
async def new_chat_member(_, message: types.Message):

    # Check if the bot itself was added
    for member in message.new_chat_members:
        if member.id == app.id:
            chat = message.chat

            # Get chat information
            chat_name = utils.esc(chat.title)
            chat_id = chat.id
            chat_username = f"@{chat.username}" if chat.username else "ᴘʀɪᴠᴀᴛᴇ ɢʀᴏᴜᴘ"
            members_count = await app.get_chat_members_count(chat_id)

            # Get the user who added the bot
            added_by = message.from_user
            added_by_name = added_by.mention if added_by else "ᴜɴᴋɴᴏᴡɴ"

            await db.add_chat(chat_id, kind="group")

            # Create the formatted message with blockquote
            text = f"""<blockquote>🟢 <b>˹˹ᴛɪᴛᴏ ꭙ ᴍᴜꜱɪᴄ˼ ᴀᴅᴅᴇᴅ ɪɴ ᴀ ɴᴇᴡ ɢʀᴏᴜᴘ</b></blockquote>

<blockquote>
🔖 <b>ᴄʜᴀᴛ ɴᴀᴍᴇ:</b> {chat_name}
🆔 <b>ᴄʜᴀᴛ ɪᴅ:</b> <code>{chat_id}</code>
👤 <b>ᴄʜᴀᴛ ᴜꜱᴇʀɴᴀᴍᴇ:</b> {chat_username}
🔗 <b>ᴄʜᴀᴛ ʟɪɴᴋ:</b> {f"https://t.me/{chat.username}" if chat.username else "ᴄʟɪᴄᴋ ʜᴇʀᴇ"}
👥 <b>ɢʀᴏᴜᴘ ᴍᴇᴍʙᴇʀs:</b> {members_count}
🤵 <b>ᴀᴅᴅᴇᴅ ʙʏ:</b> {added_by_name}
</blockquote>
"""

            try:
                # send the notification to the logger group
                await app.send_photo(
                    chat_id=config.LOGGER_ID,
                    photo=config.START_IMG,
                    caption=text
                )
            except Exception as e:
                print(f"Failed to send new chat notification: {e}")

            break


@app.on_message(filters.left_chat_member & filters.group)
async def left_chat_member(_, message: types.Message):

    # Check if the bot itself was removed
    if message.left_chat_member.id == app.id:
        chat = message.chat

        await db.rm_chat(chat.id)

        # Get chat information
        chat_name = utils.esc(chat.title)
        chat_id = chat.id
        chat_username = f"@{chat.username}" if chat.username else "ᴘʀɪᴠᴀᴛᴇ ɢʀᴏᴜᴘ"

        # Get the user who removed the bot
        removed_by = message.from_user
        removed_by_name = removed_by.mention if removed_by else "ᴜɴᴋɴᴏᴡɴ"

        # Create the formatted message with blockquote
        text = f"""<blockquote>🔴 <b>˹ᴛɪᴛᴏ ꭙ ᴍᴜꜱɪᴄ˼ ʀᴇᴍᴏᴠᴇᴅ ꜰʀᴏᴍ ᴀ ɢʀᴏᴜᴘ</b></blockquote>

<blockquote>
🔖 <b>ᴄʜᴀᴛ ɴᴀᴍᴇ:</b> {chat_name}
🆔 <b>ᴄʜᴀᴛ ɪᴅ:</b> <code>{chat_id}</code>
👤 <b>ᴄʜᴀᴛ ᴜꜱᴇʀɴᴀᴍᴇ:</b> {chat_username}
🔗 <b>ᴄʜᴀᴛ ʟɪɴᴋ:</b> {f"https://t.me/{chat.username}" if chat.username else "ᴄʟɪᴄᴋ ʜᴇʀᴇ"}
🚫 <b>ʀᴇᴍᴏᴠᴇᴅ ʙʏ:</b> {removed_by_name}</blockquote>
"""

        try:
            # Send the notification to the logger group
            await app.send_photo(
                chat_id=config.LOGGER_ID,
                photo=config.START_IMG,
                caption=text
            )
        except Exception as e:
            print(f"Failed to send left chat notification: {e}")
