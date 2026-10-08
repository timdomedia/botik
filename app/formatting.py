"""Тексты сообщений в чат — коротко, как в беседе: «Шуба L долями — 25 000», «Дане: 9 650»."""
from html import escape

from .catalog import Catalog
from .db import Totals

STATUS = {"new": "⏳ Не отправлен", "shipped": "📦 Отправлен", "cancelled": "❌ Отменён"}


def money(value: float) -> str:
    """Целые рубли с пробелами: 12 345."""
    return f"{round(value):,}".replace(",", " ")


def _item_title(it) -> str:
    title = it["product"]
    if it["size"]:
        title += f" {it['size']}"
    if it["qty"] > 1:
        title += f" ×{it['qty']}"
    return title


def dane_line(items) -> str:
    """«Дане: 3 290» или «Дане: 3 290 + 1 039 = 4 329» — по слагаемому на товар."""
    total = money(sum(it["dane"] for it in items))
    if len(items) == 1:
        return f"Дане: {total}"
    return f"Дане: {' + '.join(money(it['dane']) for it in items)} = {total}"


def order_card(order, items, debt: float | None = None) -> str:
    pay = f" {order['payment']}" if order["payment"] else ""
    parts = []
    for it in items:
        warn = "  ⚠️ нет в каталоге" if not it["known"] else ""
        opts = f" ({escape(it['options'])})" if it["options"] and not it["size"] else ""
        parts.append(f"{escape(_item_title(it))}{opts}{escape(pay)} — {money(it['revenue'])}{warn}")
    paid = f"  ✅ отдано {order['dane_paid_at'][8:10]}.{order['dane_paid_at'][5:7]}" if order["dane_paid_at"] else ""
    parts.append(f"<b>{dane_line(items)}</b>{paid}")
    if debt is not None:
        parts.append(f"должен Дане всего: {money(debt)}")

    parts.append("")
    head = [f"🛒 Тильда #{escape(order['order_id'])}"]
    head += [escape(x) for x in (order["name"], order["phone"]) if x]
    parts.append(" · ".join(head))
    if order["delivery"] or order["address"]:
        parts.append(f"🚚 {escape(order['delivery'] or '')} {escape(order['address'] or '')}".strip())
    if order["comment"]:
        parts.append(f"💬 {escape(order['comment'])}")

    details = []
    if order["discount"]:
        promo = f" {order['promocode']}" if order["promocode"] else ""
        details.append(f"промо{escape(promo)} −{money(order['discount'])}")
    details.append(f"себес {money(sum(it['cost'] for it in items))}")
    fee = sum(it["fee"] for it in items)
    if fee:
        details.append(f"комиссия {money(fee)}")
    tax = sum(it["tax"] for it in items)
    if tax:
        details.append(f"налог {money(tax)}")
    details.append(f"прибыль {money(sum(it['brand_profit'] for it in items))}")
    details.append(f"мне {money(sum(it['profit'] for it in items))}")
    parts.append(f"<i>{' · '.join(details)}</i>")

    status = STATUS.get(order["status"], order["status"])
    if order["status"] == "shipped":
        status += f" {order['shipped_at']}"
        if order["track"]:
            status += f"\n📮 Трек: <code>{escape(order['track'])}</code>"
    parts.append(status)
    return "\n".join(parts)


def totals_text(t: Totals) -> str:
    return "\n".join([
        "📊 <b>Сводка</b>",
        f"Заказов: {t.orders}  (✅ {t.shipped} · ⏳ {t.not_shipped} · ❌ {t.cancelled})",
        f"Выручка: {money(t.revenue)} ₽",
        f"Себестоимость: {money(t.cost)} ₽ · Комиссии: {money(t.fee)} ₽ · Налог: {money(t.tax)} ₽",
        f"📈 <b>Прибыль бренда: {money(t.brand_profit)} ₽</b>",
        f"💰 Моя доля: {money(t.profit)} ₽",
        f"💸 Доля Дани: {money(t.dane)} ₽, отдано {money(t.paid_to_dane)} ₽",
        f"<b>Должны Дане: {money(t.dane_debt)} ₽</b>",
    ])


def sizes_text(matrix: dict[str, dict[str, int]], catalog: Catalog) -> str:
    if not matrix:
        return "По верхней одежде продаж пока нет."
    lines = ["🧥 <b>Верхняя одежда по размерам</b> (продано / остаток)"]
    for product, sizes in sorted(matrix.items()):
        stock = (p.stock if (p := catalog.by_name(product)) else {}) or {}
        cells = []
        for size in sorted(set(sizes) | set(stock), key=size_key):
            sold = sizes.get(size, 0)
            cells.append(f"{size}: {sold}" + (f"/{stock[size] - sold}" if size in stock else ""))
        lines.append(f"\n<b>{escape(product)}</b> — всего {sum(sizes.values())}\n" + "  ".join(cells))
    return "\n".join(lines)


SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "XXXL", "3XL", "4XL"]


def size_key(size: str):
    if size in SIZE_ORDER:
        return (0, SIZE_ORDER.index(size), "")
    if size.isdigit():
        return (1, int(size), "")
    return (2, 0, size)
