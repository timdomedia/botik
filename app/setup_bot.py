"""Приводит бота в чистое состояние: убирает всё, что осталось от прошлых проектов на этом токене,
и ставит наши команды и описание. Безопасно запускать при каждом старте — всё идемпотентно."""
import logging

from aiogram import Bot
from aiogram.types import (BotCommand, BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats,
                           BotCommandScopeAllPrivateChats, BotCommandScopeDefault, MenuButtonCommands)

log = logging.getLogger(__name__)

COMMANDS = [
    ("pending", "не отправленные заказы"),
    ("dolg", "за какие заказы не отдал Дане"),
    ("stats", "выручка, прибыль, долг Дане"),
    ("sizes", "верхняя одежда по размерам"),
    ("ship", "отметить заказ отправленным"),
    ("sync", "обновить таблицу"),
    ("help", "что умеет бот"),
]
DESCRIPTION = ("Присылает заказы с Тильды в общий чат: что продано, размеры, прибыль, "
               "сколько отдать Дане, что ещё не отправлено.")
SHORT_DESCRIPTION = "Заказы с Тильды, размеры, прибыль и доля Дани"

OLD_SCOPES = [BotCommandScopeDefault(), BotCommandScopeAllPrivateChats(),
              BotCommandScopeAllGroupChats(), BotCommandScopeAllChatAdministrators()]


async def _safe(what: str, coro) -> None:
    try:
        await coro
    except Exception as e:  # чистка не должна валить запуск
        log.warning("setup: %s failed: %s", what, e)


async def prepare_bot(bot: Bot, name: str = "") -> None:
    # 1. Вебхук старого проекта: снимаем вместе с накопившимися старыми апдейтами.
    info = await bot.get_webhook_info()
    if info.url:
        log.info("setup: removing old webhook %s", info.url)
        await bot.delete_webhook(drop_pending_updates=True)

    # 2. Старые команды во всех областях и языках, потом наши.
    for scope in OLD_SCOPES:
        for lang in (None, "ru", "en"):
            await _safe("delete commands", bot.delete_my_commands(scope=scope, language_code=lang))
    await bot.set_my_commands(commands=[BotCommand(command=c, description=d) for c, d in COMMANDS])

    # 3. Кнопка меню: вместо мини-аппа старого проекта — обычный список команд.
    await _safe("menu button", bot.set_chat_menu_button(menu_button=MenuButtonCommands()))

    # 4. Описание и имя (меняем, только если отличаются — у этих методов жёсткие лимиты).
    if (await bot.get_my_description()).description != DESCRIPTION:
        await _safe("description", bot.set_my_description(description=DESCRIPTION))
    if (await bot.get_my_short_description()).short_description != SHORT_DESCRIPTION:
        await _safe("short description", bot.set_my_short_description(short_description=SHORT_DESCRIPTION))
    if name and (await bot.get_my_name()).name != name:
        await _safe("name", bot.set_my_name(name=name))
