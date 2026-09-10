# ==============================================================================
# _guard.py - Assistant Containment Guard
# ==============================================================================
# Two absolute rules, no exceptions, no "unless":
#
#   1. An assistant/userbot account must NEVER be in a group or channel
#      that the BOT (app) itself isn't currently a member of.
#   2. An assistant/userbot account must NEVER send/forward/copy any
#      message, anywhere - not even in a chat the bot IS in. Assistants
#      only exist to stream audio into voice chats; all actual text
#      communication goes through the BOT account instead.
#
# tito/plugins/events/assistant_cleanup.py sweeps and undoes anything that
# slips through rule 1 (assistant added manually, bot later kicked, etc).
# install_messaging_guard() (below) enforces rule 2 unconditionally.
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


# ------------------------------------------------------------------------------
# Messaging containment
# ------------------------------------------------------------------------------
# Every outgoing "write" method an assistant Client exposes, wrapped so it
# is a hard no-op - unconditionally, regardless of which chat it targets or
# whether the bot is in that chat. Assistants stream voice; they never
# write. This is applied once per assistant client (see
# install_messaging_guard(), called from Userbot._build_client) so it
# covers every call site - present and future, anywhere in the codebase -
# not just the ones audited by hand.
# ------------------------------------------------------------------------------

_GUARDED_METHODS = (
    "send_message", "send_photo", "send_video", "send_audio",
    "send_document", "send_voice", "send_sticker", "send_animation",
    "send_media_group", "forward_messages", "copy_message",
)


def _extract_chat_id(args: tuple, kwargs: dict):
    if "chat_id" in kwargs:
        return kwargs["chat_id"]
    if args:
        return args[0]
    return None


def install_messaging_guard(client) -> None:
    """Monkey-patch one assistant Client instance so NONE of its
    message-sending methods can ever fire, in any chat, for any reason.
    Safe to call once per client, right after it's constructed."""

    for method_name in _GUARDED_METHODS:
        original = getattr(client, method_name, None)
        if original is None:
            continue

        def _make_wrapper(name):
            async def _wrapper(*args, **kwargs):
                chat_id = _extract_chat_id(args, kwargs)
                logger.warning(
                    f"guard: blocked assistant.{name}() targeting "
                    f"{chat_id!r} - assistants are not allowed to send "
                    f"messages, period."
                )
                return None
            return _wrapper

        setattr(client, method_name, _make_wrapper(method_name))
