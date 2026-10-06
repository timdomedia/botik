import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .catalog import Catalog
from .tilda import Order

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL,
    name TEXT, phone TEXT, email TEXT, address TEXT,
    delivery TEXT, delivery_price REAL DEFAULT 0,
    promocode TEXT, discount REAL DEFAULT 0, total REAL DEFAULT 0, comment TEXT,
    status TEXT NOT NULL DEFAULT 'new',          -- new | shipped | cancelled
    track TEXT DEFAULT '',
    shipped_at TEXT DEFAULT '',
    message_id INTEGER,
    source TEXT NOT NULL DEFAULT 'tilda',        -- tilda | chat
    payment TEXT DEFAULT '',                     -- способ оплаты: долями, переводом…
    note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_pk INTEGER NOT NULL REFERENCES orders(id),
    product TEXT NOT NULL,      -- имя из каталога или сырое имя из Тильды
    raw_name TEXT NOT NULL,
    known INTEGER NOT NULL,     -- 1 = нашли в каталоге
    outerwear INTEGER NOT NULL,
    size TEXT DEFAULT '',
    options TEXT DEFAULT '',
    qty INTEGER NOT NULL,
    revenue REAL NOT NULL,      -- после скидки, без доставки
    fee REAL NOT NULL,          -- эквайринг
    cost REAL NOT NULL,         -- себестоимость
    brand_profit REAL NOT NULL, -- прибыль бренда = выручка − эквайринг − себестоимость
    dane REAL NOT NULL,         -- доля Дани (DANE_SHARE_PERCENT от прибыли бренда)
    profit REAL NOT NULL        -- моя доля
);
CREATE TABLE IF NOT EXISTS payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT DEFAULT ''
);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


@dataclass
class Totals:
    orders: int
    revenue: float
    fee: float
    cost: float
    brand_profit: float
    dane: float
    profit: float
    paid_to_dane: float
    shipped: int
    not_shipped: int
    cancelled: int

    @property
    def dane_debt(self) -> float:
        return self.dane - self.paid_to_dane


class DB:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(orders)")}
        for column, ddl in (("source", "TEXT NOT NULL DEFAULT 'tilda'"), ("payment", "TEXT DEFAULT ''"),
                            ("note", "TEXT DEFAULT ''")):
            if column not in have:
                self.conn.execute(f"ALTER TABLE orders ADD COLUMN {column} {ddl}")
        self.conn.commit()

    def exists(self, order_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM orders WHERE order_id=?", (order_id,)).fetchone() is not None

    def save_order(self, order: Order, catalog: Catalog, acquiring_percent: float,
                   dane_share_percent: float = 50, source: str = "tilda", status: str = "new",
                   note: str = "") -> int:
        """Сохраняет заказ и считает деньги по каждой позиции. Возвращает pk заказа.

        Комиссия берётся по способу оплаты (долями, СБП, перевод…), если он распознан,
        иначе acquiring_percent."""
        method = catalog.payment(order.payment_system)
        fee_percent = method.fee if method else acquiring_percent
        payment = method.key if method else order.payment_system
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO orders (order_id, created_at, name, phone, email, address, delivery,
                   delivery_price, promocode, discount, total, comment, source, payment, note,
                   status, shipped_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (order.order_id, now(), order.name, order.phone, order.email, order.address,
                 order.delivery, order.delivery_price, order.promocode, order.discount,
                 order.total, order.comment, source, payment, note,
                 status, now() if status == "shipped" else ""),
            )
            pk = cur.lastrowid
            gross = sum(i.amount for i in order.items) or 1
            for item in order.items:
                product = catalog.find(item.name, item.sku)
                discount_share = order.discount * item.amount / gross
                revenue = round(item.amount - discount_share, 2)
                fee = round(revenue * fee_percent / 100, 2)
                cost = product.cost_for(item.size) * item.qty if product else 0
                brand_profit = round(revenue - fee - cost, 2)
                dane = round(brand_profit * dane_share_percent / 100, 2)
                profit = round(brand_profit - dane, 2)
                self.conn.execute(
                    """INSERT INTO items (order_pk, product, raw_name, known, outerwear, size, options,
                       qty, revenue, fee, cost, brand_profit, dane, profit)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pk, product.name if product else item.name, item.name, int(bool(product)),
                     int(bool(product and product.outerwear)), item.size, item.options, item.qty,
                     revenue, fee, cost, brand_profit, dane, profit),
                )
        return pk

    def set_message_id(self, pk: int, message_id: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE orders SET message_id=? WHERE id=?", (message_id, pk))

    def order(self, pk: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM orders WHERE id=?", (pk,)).fetchone()

    def order_by_message(self, message_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM orders WHERE message_id=?", (message_id,)).fetchone()

    def order_by_number(self, order_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()

    def items(self, pk: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM items WHERE order_pk=? ORDER BY id", (pk,)).fetchall()

    def set_status(self, pk: int, status: str, track: str | None = None) -> None:
        shipped_at = now() if status == "shipped" else ""
        with self.conn:
            if track is not None:
                self.conn.execute("UPDATE orders SET status=?, shipped_at=?, track=? WHERE id=?",
                                  (status, shipped_at, track, pk))
            else:
                self.conn.execute("UPDATE orders SET status=?, shipped_at=? WHERE id=?",
                                  (status, shipped_at, pk))

    def recent_tilda_order(self, product: str, size: str, hours: int = 72) -> sqlite3.Row | None:
        """Заказ с Тильды с тем же товаром (и размером) за последние часы — чтобы не задвоить продажу."""
        since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
        return self.conn.execute(
            """SELECT o.* FROM orders o JOIN items i ON i.order_pk = o.id
               WHERE o.source = 'tilda' AND o.status != 'cancelled' AND o.created_at >= ?
                 AND i.product = ? AND (? = '' OR i.size = ?)
               ORDER BY o.created_at DESC LIMIT 1""",
            (since, product, size, size),
        ).fetchone()

    def not_shipped(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM orders WHERE status='new' ORDER BY created_at").fetchall()

    def all_orders(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM orders ORDER BY created_at, id").fetchall()

    def all_items(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT i.*, o.order_id, o.created_at, o.name AS client, o.status, o.track, o.shipped_at,
                      o.source, o.payment
               FROM items i JOIN orders o ON o.id = i.order_pk ORDER BY o.created_at, o.id, i.id"""
        ).fetchall()

    def delete_payout(self, payout_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM payouts WHERE id=?", (payout_id,))

    def add_payout(self, amount: float, note: str = "") -> int:
        with self.conn:
            return self.conn.execute("INSERT INTO payouts (created_at, amount, note) VALUES (?,?,?)",
                                     (now(), amount, note)).lastrowid

    def payouts(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM payouts ORDER BY id").fetchall()

    def size_matrix(self, outerwear_only: bool = True) -> dict[str, dict[str, int]]:
        """{товар: {размер: продано шт.}} без отменённых заказов."""
        rows = self.conn.execute(
            f"""SELECT i.product, COALESCE(NULLIF(i.size,''),'?') AS size, SUM(i.qty) AS qty
                FROM items i JOIN orders o ON o.id = i.order_pk
                WHERE o.status != 'cancelled' {"AND i.outerwear = 1" if outerwear_only else ""}
                GROUP BY i.product, size"""
        ).fetchall()
        result: dict[str, dict[str, int]] = {}
        for r in rows:
            result.setdefault(r["product"], {})[r["size"]] = r["qty"]
        return result

    def totals(self) -> Totals:
        money = self.conn.execute(
            """SELECT COUNT(DISTINCT o.id) AS orders, COALESCE(SUM(i.revenue),0) AS revenue,
                      COALESCE(SUM(i.fee),0) AS fee, COALESCE(SUM(i.dane),0) AS dane,
                      COALESCE(SUM(i.cost),0) AS cost, COALESCE(SUM(i.brand_profit),0) AS brand_profit,
                      COALESCE(SUM(i.profit),0) AS profit
               FROM orders o LEFT JOIN items i ON i.order_pk = o.id WHERE o.status != 'cancelled'"""
        ).fetchone()
        status = dict(self.conn.execute(
            "SELECT status, COUNT(*) FROM orders GROUP BY status").fetchall())
        paid = self.conn.execute("SELECT COALESCE(SUM(amount),0) FROM payouts").fetchone()[0]
        return Totals(
            orders=money["orders"], revenue=money["revenue"], fee=money["fee"], dane=money["dane"],
            cost=money["cost"], brand_profit=money["brand_profit"], profit=money["profit"], paid_to_dane=paid,
            shipped=status.get("shipped", 0), not_shipped=status.get("new", 0),
            cancelled=status.get("cancelled", 0),
        )
