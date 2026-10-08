import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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
    payment TEXT DEFAULT '',                     -- способ оплаты: долями, картой…
    dane_paid_at TEXT DEFAULT ''                 -- когда отдал Дане его долю (реакция на карточку)
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
    fee REAL NOT NULL,          -- комиссия способа оплаты
    tax REAL NOT NULL DEFAULT 0, -- налог с выручки
    cost REAL NOT NULL,         -- себестоимость
    brand_profit REAL NOT NULL, -- прибыль бренда = выручка − комиссия − налог − себестоимость
    dane REAL NOT NULL,         -- доля Дани (DANE_SHARE_PERCENT от прибыли бренда)
    profit REAL NOT NULL        -- моя доля
);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


@dataclass
class Totals:
    orders: int
    revenue: float
    fee: float
    tax: float
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
        for column, ddl in (("payment", "TEXT DEFAULT ''"), ("dane_paid_at", "TEXT DEFAULT ''")):
            if column not in have:
                self.conn.execute(f"ALTER TABLE orders ADD COLUMN {column} {ddl}")
        if "tax" not in {r["name"] for r in self.conn.execute("PRAGMA table_info(items)")}:
            self.conn.execute("ALTER TABLE items ADD COLUMN tax REAL NOT NULL DEFAULT 0")
        self.conn.commit()

    def exists(self, order_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM orders WHERE order_id=?", (order_id,)).fetchone() is not None

    def save_order(self, order: Order, catalog: Catalog, acquiring_percent: float,
                   dane_share_percent: float = 50, tax_percent: float = 0) -> int:
        """Сохраняет заказ и считает деньги по каждой позиции. Возвращает pk заказа.

        Комиссия берётся по способу оплаты (долями, СБП, перевод…), если он распознан,
        иначе acquiring_percent."""
        method = catalog.payment(order.payment_system)
        fee_percent = method.fee if method else acquiring_percent
        payment = method.key if method else order.payment_system
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO orders (order_id, created_at, name, phone, email, address, delivery,
                   delivery_price, promocode, discount, total, comment, payment)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (order.order_id, now(), order.name, order.phone, order.email, order.address,
                 order.delivery, order.delivery_price, order.promocode, order.discount,
                 order.total, order.comment, payment),
            )
            pk = cur.lastrowid
            gross = sum(i.amount for i in order.items) or 1
            for item in order.items:
                product = catalog.find(item.name, item.sku)
                discount_share = order.discount * item.amount / gross
                revenue = round(item.amount - discount_share, 2)
                fee = round(revenue * fee_percent / 100, 2)
                cost = product.cost_for(item.size) * item.qty if product else 0
                tax = round(revenue * tax_percent / 100, 2)
                brand_profit = round(revenue - fee - tax - cost, 2)
                dane = round(brand_profit * dane_share_percent / 100, 2)
                profit = round(brand_profit - dane, 2)
                self.conn.execute(
                    """INSERT INTO items (order_pk, product, raw_name, known, outerwear, size, options,
                       qty, revenue, fee, tax, cost, brand_profit, dane, profit)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pk, product.name if product else item.name, item.name, int(bool(product)),
                     int(bool(product and product.outerwear)), item.size, item.options, item.qty,
                     revenue, fee, tax, cost, brand_profit, dane, profit),
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

    def set_dane_paid(self, pk: int, paid: bool) -> None:
        with self.conn:
            self.conn.execute("UPDATE orders SET dane_paid_at=? WHERE id=?", (now() if paid else "", pk))

    def not_shipped(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM orders WHERE status='new' ORDER BY created_at").fetchall()

    def all_orders(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM orders ORDER BY created_at, id").fetchall()

    def all_items(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT i.*, o.order_id, o.created_at, o.name AS client, o.status, o.track, o.shipped_at,
                      o.payment, o.dane_paid_at
               FROM items i JOIN orders o ON o.id = i.order_pk ORDER BY o.created_at, o.id, i.id"""
        ).fetchall()

    def dane_by_order(self) -> list[sqlite3.Row]:
        """Доля Дани по каждому заказу и отметка, отдана ли (лист «Дане»)."""
        return self.conn.execute(
            """SELECT o.order_id, o.created_at, o.name, o.status, o.dane_paid_at,
                      GROUP_CONCAT(i.product || CASE WHEN i.size != '' THEN ' ' || i.size ELSE '' END, ', ')
                          AS products,
                      SUM(i.dane) AS dane
               FROM orders o JOIN items i ON i.order_pk = o.id
               WHERE o.status != 'cancelled'
               GROUP BY o.id ORDER BY o.dane_paid_at != '', o.created_at"""
        ).fetchall()

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
                      COALESCE(SUM(i.fee),0) AS fee, COALESCE(SUM(i.tax),0) AS tax,
                      COALESCE(SUM(i.dane),0) AS dane,
                      COALESCE(SUM(i.cost),0) AS cost, COALESCE(SUM(i.brand_profit),0) AS brand_profit,
                      COALESCE(SUM(i.profit),0) AS profit
               FROM orders o LEFT JOIN items i ON i.order_pk = o.id WHERE o.status != 'cancelled'"""
        ).fetchone()
        status = dict(self.conn.execute(
            "SELECT status, COUNT(*) FROM orders GROUP BY status").fetchall())
        paid = self.conn.execute(
            """SELECT COALESCE(SUM(i.dane),0) FROM orders o JOIN items i ON i.order_pk = o.id
               WHERE o.status != 'cancelled' AND o.dane_paid_at != ''""").fetchone()[0]
        return Totals(
            orders=money["orders"], revenue=money["revenue"], fee=money["fee"], tax=money["tax"], dane=money["dane"],
            cost=money["cost"], brand_profit=money["brand_profit"], profit=money["profit"], paid_to_dane=paid,
            shipped=status.get("shipped", 0), not_shipped=status.get("new", 0),
            cancelled=status.get("cancelled", 0),
        )
