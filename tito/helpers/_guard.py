# ==============================================================================
# _guard.py - Assistant Containment Guard
# ==============================================================================
# Single source of truth for one rule: an assistant/userbot account must
# NEVER be in a group or channel that the BOT (app) itself isn't currently
# a member of. Every place in the codebase that calls client.join_chat()
# for an assistant must check bot_is_member() first, and
# tito/plugins/events/assistant_cleanup.py sweeps and undoes anything that
# slips through (assistant added manually, bot later kicked, etc).
#
# This exists so a leaked assistant session string, or anyone who gets the
# bot added somewhere, can't turn into "the assistant is now sitting in a
# random chat" - the assistant's footprint is always a subset of the bot's.
# ==============================================================================

from pyrogram import enums

from tito import app, logger

# Statuses that count as "the bot is actually here" - LEFT/BANNED don't.
_PRESENT_STATUSES = (
    enums.ChatMemberStatus.OWNER,
    enums.ChatMemberStatus.ADMINISTRATOR,
    enums.ChatMemberStatus.MEMBER,
    enums.ChatMemberStatus.RESTRICTED,
)


async def bot_is_member(chat_id: int) -> bool:
    """True only if the BOT account is a current, non-kicked member of
    chat_id. Used to gate every assistant join_chat() call - if this
    returns False, the assistant must not join, full stop."""
    try:
        member = await app.get_chat_member(chat_id, "me")
    except Exception as e:
        logger.info(f"guard: couldn't confirm bot membership in {chat_id}: {e}")
        return False

    return bool(member and member.status in _PRESENT_STATUSES)


async def guarded_join(client, chat_id: int, invite_link: str) -> bool:
    """Join `chat_id` with an assistant client, but only after re-confirming
    the bot is present there. Returns True if the join was attempted (or the
    assistant was already in), False if it was blocked because the bot
    isn't in the chat. Callers should treat False as "do not proceed."""
    from pyrogram import errors

    if not await bot_is_member(chat_id):
        logger.warning(
            f"guard: blocked assistant join into {chat_id} - bot isn't a member there."
        )
        return False

    try:
        await client.join_chat(invite_link)
    except errors.UserAlreadyParticipant:
        pass
    except errors.FloodWait:
        raise  # let the caller decide how to back off
    return True
