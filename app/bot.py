import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters

from .catalog import Catalog
from .chat import Sale, parse_payout, parse_sale
from .db import DB
from .formatting import money, order_card, sizes_text, totals_text
from .sheets import Sheets
from .tilda import Item, Order

log = logging.getLogger(__name__)
TRACK_RE = re.compile(r"(?=.*\d)[A-Za-z0-9-]{8,40}")

HELP = """<b>Команды</b>
/pending — не отправленные заказы
/sizes — верхняя одежда по размерам
/stats — выручка, прибыль, долг Дане
/paid 5000 [коммент] — скинул Дане сумму
/ship 1234 [трек] — отметить заказ отправленным
/sync — обновить онлайн-таблицу
/reload — перечитать каталог products.yaml
/chatid — id этого чата

<b>Продажи из чата</b> пишем как обычно: «шуба Л с капюшоном долями», «2 тайно переводом»,
«кружево 2000», «бомбер с промо 5%». Бот ответит, сколько Дане, и запишет в таблицу.
«Наликом 30к», «скинул 12к» — бот предложит записать выплату Дане.

Под заказом с Тильды кнопки «Отправлено» / «Отмена».
Ответь (reply) на карточку заказа трек-номером — заказ отметится отправленным с этим треком."""


def keyboard(order) -> InlineKeyboardMarkup:
    pk = order["id"]
    if order["source"] == "chat":
        if order["status"] == "cancelled":
            button = InlineKeyboardButton(text="↩️ Вернуть продажу", callback_data=f"restore:{pk}")
        else:
            button = InlineKeyboardButton(text="❌ Отменить (ошибся)", callback_data=f"cancel:{pk}")
        return InlineKeyboardMarkup(inline_keyboard=[[button]])
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
                                self.settings.dane_share_percent)
        await self.post_order(pk)
        await self.sheets.sync()
        return True

    async def chat_sale(self, sale: Sale, message_id: int, author: str, text: str) -> int:
        order = Order(
            order_id=f"chat-{message_id}", name=author, comment=text,
            discount=sale.discount, total=sale.amount,
            payment_system=sale.payment.key if sale.payment else "",
            items=[Item(name=sale.product.name, qty=sale.qty, price=sale.price / sale.qty,
                        amount=sale.price, size=sale.size)],
        )
        # без указанного способа оплаты в чате считаем без комиссии (перевод/нал)
        pk = self.db.save_order(order, self.catalog, 0,
                                self.settings.dane_share_percent, source="chat", status="shipped",
                                note=sale.note)
        await self.post_order(pk, reply_to=message_id)
        await self.sheets.sync()
        return pk

    def card(self, row) -> str:
        return order_card(row, self.db.items(row["id"]), self.db.totals().dane_debt)

    async def post_order(self, pk: int, reply_to: int | None = None) -> None:
        row = self.db.order(pk)
        reply = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
        msg = await self.bot.send_message(self.settings.chat_id, self.card(row), reply_markup=keyboard(row),
                                          reply_parameters=reply)
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
        await m.answer(f"chat id: <code>{m.chat.id}</code>")

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

    @router.message(Command("paid"), allowed)
    async def paid(m: Message, command: CommandObject):
        args = (command.args or "").split(maxsplit=1)
        try:
            amount = float(args[0].replace(",", ".").replace(" ", ""))
        except (IndexError, ValueError):
            await m.answer("Формат: /paid 5000 [комментарий]")
            return
        app.db.add_payout(amount, args[1] if len(args) > 1 else "")
        await m.answer(f"Записал: Дане скинули {money(amount)} ₽.\nОсталось должны: "
                       f"<b>{money(app.db.totals().dane_debt)} ₽</b>")
        await app.sheets.sync()

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

    @router.message(allowed, F.text, ~F.text.startswith("/"))
    async def on_text(m: Message):
        # 1) ответ трек-номером на карточку заказа
        if m.reply_to_message:
            order = app.db.order_by_message(m.reply_to_message.message_id)
            track = re.sub(r"\s+", "", m.text)
            if order and TRACK_RE.fullmatch(track):
                await app.set_status(order["id"], "shipped", track)
                await m.reply(f"✅ #{order['order_id']} отправлен, трек <code>{track}</code>")
                return

        # 2) продажа: «шуба Л с капюшоном долями»
        sale = parse_sale(m.text, app.catalog)
        if sale and sale.confident:
            if not sale.price:
                await m.reply(f"Понял, что это {sale.product.name}, но не знаю цену. "
                              f"Напиши с суммой («{sale.product.name.lower()} 4500») "
                              "или добавь price в products.yaml.")
                return
            if app.db.exists(f"chat-{m.message_id}"):
                return
            dup = app.db.recent_tilda_order(sale.product.name, sale.size)
            if dup:
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="➕ Это отдельная продажа", callback_data="sale:add"),
                    InlineKeyboardButton(text="✖️ Это тот же", callback_data="po:no"),
                ]])
                await m.reply(f"Похоже, это заказ с Тильды #{dup['order_id']} ({dup['name']}), "
                              "он уже посчитан. Записать ещё раз?", reply_markup=kb)
                return
            await app.chat_sale(sale, m.message_id, m.from_user.full_name if m.from_user else "", m.text)
            return

        # 3) выплата Дане: «Наликом 30к✅», «скинул 12к»
        amount = parse_payout(m.text)
        if amount:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=f"✅ Дане отдано {money(amount)}", callback_data=f"po:{amount:g}"),
                InlineKeyboardButton(text="✖️ Нет", callback_data="po:no"),
            ]])
            await m.reply(f"Записать как выплату Дане {money(amount)} ₽?", reply_markup=kb)

    @router.callback_query(F.data == "sale:add")
    async def on_sale_add(c: CallbackQuery):
        src = c.message.reply_to_message
        sale = parse_sale(src.text, app.catalog) if src and src.text else None
        if c.message.chat.id != app.settings.chat_id or not sale or app.db.exists(f"chat-{src.message_id}"):
            await c.answer("Уже записано или не нашёл исходное сообщение")
            return
        await c.message.delete()
        await app.chat_sale(sale, src.message_id, src.from_user.full_name if src.from_user else "", src.text)
        await c.answer("Записал")

    @router.callback_query(F.data.regexp(r"^po:"))
    async def on_payout(c: CallbackQuery):
        if c.message.chat.id != app.settings.chat_id:
            await c.answer()
            return
        value = c.data.split(":", 1)[1]
        if value == "no":
            await c.message.delete()
            await c.answer()
            return
        if value.startswith("del"):
            app.db.delete_payout(int(value[3:]))
            await c.message.edit_text(f"Выплату убрал. Должен Дане: <b>{money(app.db.totals().dane_debt)} ₽</b>")
        else:
            src = c.message.reply_to_message
            note = (src.text or "") if src else ""
            payout_id = app.db.add_payout(float(value), note)
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="↩️ Отменить", callback_data=f"po:del{payout_id}")]])
            await c.message.edit_text(f"✅ Дане отдано {money(float(value))} ₽\n"
                                      f"Должен Дане: <b>{money(app.db.totals().dane_debt)} ₽</b>", reply_markup=kb)
        await c.answer()
        await app.sheets.sync()

    @router.callback_query(F.data.regexp(r"^(ship|cancel|undo|restore):\d+$"))
    async def on_button(c: CallbackQuery):
        if c.message.chat.id != app.settings.chat_id:
            await c.answer()
            return
        action, pk = c.data.split(":")
        status = {"ship": "shipped", "cancel": "cancelled", "undo": "new", "restore": "shipped"}[action]
        await app.set_status(int(pk), status)
        await c.answer({"ship": "Отмечено отправленным", "cancel": "Отменено",
                        "undo": "Вернул в не отправленные", "restore": "Продажа возвращена"}[action])

    return router
