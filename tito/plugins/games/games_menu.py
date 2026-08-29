# ==============================================================================
# games_menu.py - Games Menu Panel
# ==============================================================================
# Typing "الألعاب" / "العاب" / "ألعاب" / "الالعاب" / "games" opens an inline
# button panel listing every mini-game the bot supports (dicegame.py).
# Tapping a button plays that game immediately - same result as typing its
# command by hand, just one tap instead of remembering the word.
# ==============================================================================

from pyrogram import filters, types, enums
from pyrogram.errors import QueryIdInvalid

from tito import app, lang, logger
from tito.helpers import buttons

# callback_data -> (dice emoji sent to Telegram, lang key used for the result text)
GAME_ACTIONS = {
    "game_dice": ("🎲", "dice_rolled"),
    "game_jackpot": ("🎰", "dice_jackpot"),
    "game_dart": ("🎯", "dice_dart"),
    "game_basket": ("🏀", "dice_basket"),
    "game_ball": ("🎳", "dice_ball"),
    "game_football": ("⚽", "dice_football"),
}


def games_markup() -> types.InlineKeyboardMarkup:
    return buttons.ikm([
        [
            buttons.ikb(text="🎲 نرد", callback_data="game_dice"),
            buttons.ikb(text="🎰 جاكبوت", callback_data="game_jackpot"),
        ],
        [
            buttons.ikb(text="🎯 سهم", callback_data="game_dart"),
            buttons.ikb(text="🏀 كرة سلة", callback_data="game_basket"),
        ],
        [
            buttons.ikb(text="🎳 بولينج", callback_data="game_ball"),
            buttons.ikb(text="⚽ كرة قدم", callback_data="game_football"),
        ],
        [
            buttons.ikb(
                text="❌ إغلاق",
                callback_data="games_close",
                style=enums.ButtonStyle.DANGER,
            ),
        ],
    ])


@app.on_message(
    filters.command(
        ["الالعاب", "العاب", "ألعاب", "الألعاب", "games"],
        prefixes=["", "/"],
    ) & ~app.bl_users
)
@lang.language()
async def games_menu(bot, message: types.Message):
    try:
        await message.delete()
    except Exception:
        pass

    try:
        await message.reply_text(
            message.lang["games_menu_title"],
            reply_markup=games_markup(),
            quote=True,
        )
    except Exception as e:
        logger.error(f"Failed to show games menu: {e}")


@app.on_callback_query(
    filters.regex("^game_(dice|jackpot|dart|basket|ball|football)$") & ~app.bl_users
)
@lang.language()
async def _game_button(bot, query: types.CallbackQuery):
    try:
        await query.answer()
    except QueryIdInvalid:
        pass

    emoji, result_key = GAME_ACTIONS[query.data]
    chat_id = query.message.chat.id

    try:
        rolled = await bot.send_dice(chat_id, emoji)
        score = rolled.dice.value
        await query.message.reply_text(
            query.lang[result_key].format(query.from_user.mention, score),
            quote=True,
        )
    except Exception as e:
        if "FLOOD_WAIT" not in str(e):
            try:
                await query.message.reply_text(query.lang["dice_error"].format(str(e)))
            except Exception:
                pass


@app.on_callback_query(filters.regex("^games_close$") & ~app.bl_users)
async def _games_close(bot, query: types.CallbackQuery):
    try:
        await query.answer()
    except QueryIdInvalid:
        pass
    try:
        await query.message.delete()
    except Exception:
        pass
