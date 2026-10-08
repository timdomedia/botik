import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
                           MessageReactionUpdated)

from .catalog import Catalog
from .db import DB
from .formatting import money, order_card, sizes_text, totals_text
from .sheets import Sheets
from .tilda import Order

log = logging.getLogger(__name__)
TRACK_RE = re.compile(r"(?=.*\d)[A-Za-z0-9-]{8,40}")

HELP = """<b>Команды</b>
/pending — не отправленные заказы
/sizes — верхняя одежда по размерам
/stats — выручка, прибыль, долг Дане
/dolg — за какие заказы ещё не отдал Дане
/ship 1234 [трек] — отметить заказ отправленным
/sync — обновить онлайн-таблицу
/reload — перечитать каталог products.yaml
/chatid — id этого чата и твой id

<b>Отдал Дане</b> — поставь любую реакцию на карточку заказа. Снял реакцию — снова в долг.
Под заказом кнопки «Отправлено» / «Отмена».
Ответь (reply) на карточку заказа трек-номером — заказ отметится отправленным с этим треком."""


def keyboard(order) -> InlineKeyboardMarkup:
    pk = order["id"]
    if order["status"] == "new":
        rows = [[InlineKeyboardButton(text="📦 Отправлено", callback_data=f"ship:{pk}"),
                 InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel:{pk}")]]
    else:
        rows = [[InlineKeyboardButton(text="↩️ Вернуть в «не отправлен»", callback_data=f"undo:{pk}")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


class App:
    """Общий контекст: бот, БД, каталог, таблица."""

    def __init__(self, bot: Bot, db: DB, catalog: Catalog, sheets: Sheets, settings):
        self.bot, self.db, self.catalog, self.sheets, self.settings = bot, db, catalog, sheets, settings

    async def new_order(self, order: Order) -> bool:
        if not order.order_id:
            order.order_id = datetime.now().strftime("T%Y%m%d%H%M%S")
        if self.db.exists(order.order_id):
            log.info("duplicate order %s ignored", order.order_id)
            return False
        pk = self.db.save_order(order, self.catalog, self.settings.acquiring_percent,
                                self.settings.dane_share_percent, self.settings.tax_percent)
        await self.post_order(pk)
        await self.sheets.sync()
        return True

    def card(self, row) -> str:
        return order_card(row, self.db.items(row["id"]), self.db.totals().dane_debt)

    async def post_order(self, pk: int) -> None:
        row = self.db.order(pk)
        msg = await self.bot.send_message(self.settings.chat_id, self.card(row), reply_markup=keyboard(row))
        self.db.set_message_id(pk, msg.message_id)

    async def refresh_card(self, pk: int) -> None:
        row = self.db.order(pk)
        if not row["message_id"]:
            return
        try:
            await self.bot.edit_message_text(self.card(row), chat_id=self.settings.chat_id,
                                             message_id=row["message_id"], reply_markup=keyboard(row))
        except Exception as e:  # сообщение удалено / не изменилось
            log.warning("cannot edit card %s: %s", pk, e)

    async def set_status(self, pk: int, status: str, track: str | None = None) -> None:
        self.db.set_status(pk, status, track)
        await self.refresh_card(pk)
        await self.sheets.sync()

    async def set_dane_paid(self, pk: int, paid: bool) -> None:
        self.db.set_dane_paid(pk, paid)
        await self.refresh_card(pk)
        await self.sheets.sync()

    def dolg_text(self) -> str:
        unpaid = [r for r in self.db.dane_by_order() if not r["dane_paid_at"]]
        if not unpaid:
            return "✅ Дане всё отдано."
        lines = [f"💸 <b>Не отдал Дане: {money(sum(r['dane'] for r in unpaid))} ₽</b>"]
        lines += [f"• #{r['order_id']} {r['created_at'][:10]} {r['products']} — {money(r['dane'])}" for r in unpaid]
        return "\n".join(lines)

    def pending_text(self) -> str:
        orders = self.db.not_shipped()
        if not orders:
            return "✅ Всё отправлено."
        deadline = timedelta(days=self.settings.ship_deadline_days)
        lines = [f"⏳ <b>Не отправлено: {len(orders)}</b>"]
        for o in orders:
            items = ", ".join(f"{i['product']} {i['size']}".strip() for i in self.db.items(o["id"]))
            late = datetime.now() - datetime.strptime(o["created_at"], "%Y-%m-%d %H:%M") > deadline
            lines.append(f"{'🔴' if late else '•'} #{o['order_id']} {o['created_at'][:10]} — {o['name']}: {items}")
        return "\n".join(lines)


def build_router(app: App) -> Router:
    router = Router()
    allowed = F.chat.id == app.settings.chat_id

    @router.message(Command("chatid"))
    async def chatid(m: Message):
        user = f"\nтвой id: <code>{m.from_user.id}</code>" if m.from_user else ""
        await m.answer(f"chat id: <code>{m.chat.id}</code>{user}")

    @router.message(Command("start", "help"), allowed)
    async def help_(m: Message):
        await m.answer(HELP)

    @router.message(Command("pending"), allowed)
    async def pending(m: Message):
        await m.answer(app.pending_text())

    @router.message(Command("sizes"), allowed)
    async def sizes(m: Message):
        await m.answer(sizes_text(app.db.size_matrix(), app.catalog))

    @router.message(Command("stats"), allowed)
    async def stats(m: Message):
        await m.answer(totals_text(app.db.totals()))

    @router.message(Command("dolg"), allowed)
    async def dolg(m: Message):
        await m.answer(app.dolg_text())

    @router.message(Command("ship"), allowed)
    async def ship(m: Message, command: CommandObject):
        args = (command.args or "").split(maxsplit=1)
        order = app.db.order_by_number(args[0].lstrip("#")) if args else None
        if not order:
            await m.answer("Формат: /ship <номер заказа> [трек]")
            return
        await app.set_status(order["id"], "shipped", args[1] if len(args) > 1 else None)
        await m.answer(f"✅ #{order['order_id']} отправлен")

    @router.message(Command("sync"), allowed)
    async def sync(m: Message):
        if not app.sheets.enabled:
            await m.answer("Google-таблица не настроена (GOOGLE_SHEET_ID пустой).")
            return
        await app.sheets.sync()
        await m.answer("Таблица обновлена.")

    @router.message(Command("reload"), allowed)
    async def reload(m: Message):
        fresh = Catalog.load(app.settings.products_file)
        app.catalog.products, app.catalog.payments = fresh.products, fresh.payments
        await m.answer(f"Каталог перечитан: {len(app.catalog.products)} товаров. "
                       "Уже пришедшие заказы не пересчитываются.")

    @router.message(allowed, F.reply_to_message, F.text, ~F.text.startswith("/"))
    async def track_reply(m: Message):
        order = app.db.order_by_message(m.reply_to_message.message_id)
        track = re.sub(r"\s+", "", m.text)
        if not order or not TRACK_RE.fullmatch(track):  # обычный ответ в обсуждении, не трек
            return
        await app.set_status(order["id"], "shipped", track)
        await m.reply(f"✅ #{order['order_id']} отправлен, трек <code>{track}</code>")

    @router.message_reaction(F.chat.id == app.settings.chat_id)
    async def on_reaction(r: MessageReactionUpdated):
        """Реакция на карточку заказа = отдал Дане его долю, снял реакцию = не отдал."""
        payers = app.settings.payer_ids
        if payers and (not r.user or r.user.id not in payers):
            return
        order = app.db.order_by_message(r.message_id)
        if not order:
            return
        paid = bool(r.new_reaction)
        if paid != bool(order["dane_paid_at"]):
            await app.set_dane_paid(order["id"], paid)

    @router.callback_query(F.data.regexp(r"^(ship|cancel|undo):\d+$"))
    async def on_button(c: CallbackQuery):
        if c.message.chat.id != app.settings.chat_id:
            await c.answer()
            return
        action, pk = c.data.split(":")
        status = {"ship": "shipped", "cancel": "cancelled", "undo": "new"}[action]
        await app.set_status(int(pk), status)
        await c.answer({"ship": "Отмечено отправленным", "cancel": "Отменено",
                        "undo": "Вернул в не отправленные"}[action])

    return router
