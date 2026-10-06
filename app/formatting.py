"""Тексты сообщений в чат. Блок «Дане» — по строке «товар размер — сумма» на каждый товар."""
from html import escape

from .catalog import Catalog
from .db import Totals

STATUS = {"new": "⏳ Не отправлен", "shipped": "✅ Отправлен", "cancelled": "❌ Отменён"}


def money(value: float) -> str:
    value = round(value, 2)
    text = f"{value:,.2f}".rstrip("0").rstrip(".") if value % 1 else f"{value:,.0f}"
    return text.replace(",", " ")


def _item_title(it) -> str:
    title = it["product"]
    if it["size"]:
        title += f" {it['size']}"
    if it["qty"] > 1:
        title += f" ×{it['qty']}"
    return title


def dane_lines(items) -> str:
    """Блок «сколько скинуть Дане»: по строке на товар + итог."""
    lines = [f"{escape(_item_title(it))} — {money(it['dane'])}" for it in items]
    total = sum(it["dane"] for it in items)
    if len(items) > 1:
        lines.append(f"<b>Итого: {money(total)}</b>")
    return "\n".join(lines)


def order_card(order, items, share: float = 50) -> str:
    parts = [f"🛒 <b>Заказ #{escape(order['order_id'])}</b>  ·  {order['created_at']}"]
    client = " · ".join(escape(x) for x in (order["name"], order["phone"]) if x)
    if client:
        parts.append(f"👤 {client}")
    if order["email"]:
        parts.append(f"✉️ {escape(order['email'])}")
    if order["delivery"] or order["address"]:
        parts.append(f"🚚 {escape(order['delivery'] or '')} {escape(order['address'] or '')}".rstrip())
    if order["comment"]:
        parts.append(f"💬 {escape(order['comment'])}")

    parts.append("")
    for it in items:
        warn = "" if it["known"] else "  ⚠️ нет в каталоге"
        opts = f" <i>({escape(it['options'])})</i>" if it["options"] and not it["size"] else ""
        parts.append(f"• {escape(_item_title(it))}{opts} — {money(it['revenue'])} ₽{warn}")
    if order["promocode"]:
        parts.append(f"🏷 Промокод {escape(order['promocode'])}, скидка {money(order['discount'])} ₽")
    if order["delivery_price"]:
        parts.append(f"Доставка: {money(order['delivery_price'])} ₽")
    parts.append(f"Оплачено: <b>{money(order['total'])} ₽</b>")

    fee = sum(it["fee"] for it in items)
    cost = sum(it["cost"] for it in items)
    extra = [f"себестоимость {money(cost)}"]
    if fee:
        extra.append(f"эквайринг {money(fee)}")
    parts.append("")
    parts.append(f"📈 Прибыль бренда: <b>{money(sum(it['brand_profit'] for it in items))} ₽</b>"
                 f"  <i>({', '.join(extra)})</i>")
    parts.append("")
    parts.append(f"💸 <b>Дане ({money(share)}%):</b>")
    parts.append(dane_lines(items))
    parts.append("")
    parts.append(f"💰 Мне: <b>{money(sum(it['profit'] for it in items))} ₽</b>")

    status = STATUS.get(order["status"], order["status"])
    if order["status"] == "shipped":
        status += f" {order['shipped_at']}"
        if order["track"]:
            status += f"\n📮 Трек: <code>{escape(order['track'])}</code>"
    parts.append("")
    parts.append(status)
    return "\n".join(parts)


def totals_text(t: Totals) -> str:
    return "\n".join([
        "📊 <b>Сводка</b>",
        f"Заказов: {t.orders}  (✅ {t.shipped} · ⏳ {t.not_shipped} · ❌ {t.cancelled})",
        f"Выручка: {money(t.revenue)} ₽",
        f"Себестоимость: {money(t.cost)} ₽ · Эквайринг: {money(t.fee)} ₽",
        f"📈 <b>Прибыль бренда: {money(t.brand_profit)} ₽</b>",
        f"💰 Моя доля: {money(t.profit)} ₽",
        f"💸 Доля Дани: {money(t.dane)} ₽, скинуто {money(t.paid_to_dane)} ₽",
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
