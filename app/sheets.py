"""Онлайн-таблица в Google Sheets. Источник правды — SQLite, листы целиком перерисовываются."""
import asyncio
import logging
from datetime import datetime, timedelta

from .catalog import Catalog
from .db import DB
from .formatting import STATUS, size_key

log = logging.getLogger(__name__)


class Sheets:
    def __init__(self, sheet_id: str, service_account_file: str, db: DB, catalog: Catalog,
                 ship_deadline_days: int):
        self.db, self.catalog, self.deadline = db, catalog, ship_deadline_days
        self.enabled = bool(sheet_id)
        self._lock = asyncio.Lock()
        self._dirty = False
        if self.enabled:
            import gspread
            self.book = gspread.service_account(filename=service_account_file).open_by_key(sheet_id)

    async def sync(self) -> None:
        """Перерисовать таблицу. Параллельные вызовы склеиваются в один."""
        if not self.enabled:
            return
        self._dirty = True
        if self._lock.locked():
            return
        async with self._lock:
            while self._dirty:
                self._dirty = False
                try:
                    await asyncio.to_thread(self._sync_blocking)
                except Exception:
                    log.exception("Google Sheets sync failed")

    def _sheet(self, title: str, rows: int = 100, cols: int = 20):
        import gspread
        try:
            return self.book.worksheet(title)
        except gspread.WorksheetNotFound:
            return self.book.add_worksheet(title=title, rows=rows, cols=cols)

    def _write(self, title: str, values: list[list]) -> None:
        ws = self._sheet(title, rows=max(len(values) + 10, 100))
        ws.clear()
        if values:
            ws.update(values, "A1", value_input_option="USER_ENTERED")
            ws.format("1:1", {"textFormat": {"bold": True}})
            ws.freeze(rows=1)

    def _sync_blocking(self) -> None:
        self._write("Сводка", self._summary())
        self._write("Заказы", self._orders())
        self._write("Размеры", self._sizes())
        self._write("Отправка", self._shipping())
        self._write("Выплаты Дане", self._payouts())

    def _orders(self) -> list[list]:
        rows = [["Дата", "Заказ", "Клиент", "Товар", "Размер", "Кол-во", "Выручка", "Эквайринг",
                 "Себестоимость", "Прибыль бренда", "Дане", "Мне", "Статус", "Трек", "Отправлен"]]
        for it in self.db.all_items():
            rows.append([it["created_at"], it["order_id"], it["client"], it["product"], it["size"],
                         it["qty"], it["revenue"], it["fee"], it["cost"], it["brand_profit"], it["dane"], it["profit"],
                         STATUS.get(it["status"], it["status"]), it["track"], it["shipped_at"]])
        return rows

    def _sizes(self) -> list[list]:
        matrix = self.db.size_matrix(outerwear_only=True)
        # добавляем товары с остатками, даже если ещё не продавались
        for p in self.catalog.products:
            if p.outerwear:
                matrix.setdefault(p.name, {})
        sizes = sorted({s for m in matrix.values() for s in m}
                       | {s for p in self.catalog.products if p.outerwear for s in p.stock}, key=size_key)
        rows = [["Товар", "Что"] + sizes + ["Всего"]]
        for product in sorted(matrix):
            sold = matrix[product]
            rows.append([product, "Продано"] + [sold.get(s, 0) for s in sizes] + [sum(sold.values())])
            p = self.catalog.by_name(product)
            if p and p.stock:
                rows.append([product, "Было"] + [p.stock.get(s, "") for s in sizes] + [sum(p.stock.values())])
                left = [p.stock[s] - sold.get(s, 0) if s in p.stock else "" for s in sizes]
                rows.append([product, "Остаток"] + left + [sum(v for v in left if v != "")])
        return rows

    def _shipping(self) -> list[list]:
        """Автосверка: что отправлено, что нет, что просрочено."""
        rows = [["Заказ", "Дата", "Клиент", "Товары", "Статус", "Трек", "Отправлен", "Дней ждёт", "Просрочен"]]
        now = datetime.now()
        orders = sorted(self.db.all_orders(), key=lambda o: ({"new": 0, "shipped": 1}.get(o["status"], 2), o["created_at"]))
        for o in orders:
            items = ", ".join(f"{i['product']} {i['size']}".strip() + (f" ×{i['qty']}" if i["qty"] > 1 else "")
                              for i in self.db.items(o["id"]))
            created = datetime.strptime(o["created_at"], "%Y-%m-%d %H:%M")
            waiting = (now - created).days if o["status"] == "new" else ""
            late = "🔴 ДА" if o["status"] == "new" and now - created > timedelta(days=self.deadline) else ""
            rows.append([o["order_id"], o["created_at"], o["name"], items, STATUS.get(o["status"], o["status"]),
                         o["track"], o["shipped_at"], waiting, late])
        return rows

    def _summary(self) -> list[list]:
        t = self.db.totals()
        return [
            ["Показатель", "Значение"],
            ["Заказов (без отмен)", t.orders],
            ["✅ Отправлено", t.shipped],
            ["⏳ Не отправлено", t.not_shipped],
            ["❌ Отменено", t.cancelled],
            ["Выручка", t.revenue],
            ["Эквайринг", t.fee],
            ["Себестоимость", t.cost],
            ["Прибыль бренда", t.brand_profit],
            ["Доля Дани начислено", t.dane],
            ["Дане скинуто", t.paid_to_dane],
            ["Должны Дане", t.dane_debt],
            ["Моя доля", t.profit],
            ["Обновлено", datetime.now().strftime("%Y-%m-%d %H:%M")],
        ]

    def _payouts(self) -> list[list]:
        rows = [["Дата", "Сумма", "Комментарий"]]
        rows += [[p["created_at"], p["amount"], p["note"]] for p in self.db.payouts()]
        return rows
