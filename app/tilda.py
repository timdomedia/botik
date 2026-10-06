"""Разбор вебхука Тильды (form-urlencoded или JSON) в Order."""
import json
import re
from dataclasses import dataclass, field

SIZE_OPTION_RE = re.compile(r"размер|size|рост", re.I)
SIZE_IN_NAME_RE = re.compile(r"\b(XXS|XS|S|M|L|XL|XXL|XXXL|2XL|3XL|4XL|[4-6]\d)\b", re.I)
# Старый формат Тильды: "Куртка - 1x5000 = 5000"
LEGACY_LINE_RE = re.compile(r"^(?P<name>.+?)\s*-\s*(?P<qty>\d+)\s*x\s*(?P<price>[\d.]+)\s*=\s*(?P<amount>[\d.]+)")


@dataclass
class Item:
    name: str
    qty: int
    price: float
    amount: float
    size: str = ""
    sku: str = ""
    options: str = ""


@dataclass
class Order:
    order_id: str
    name: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    delivery: str = ""
    delivery_price: float = 0
    promocode: str = ""
    discount: float = 0
    total: float = 0
    comment: str = ""
    payment_system: str = ""   # paymentsystem из Тильды или способ оплаты из чата
    items: list[Item] = field(default_factory=list)


def _num(value) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.,-]", "", str(value)).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _pick(data: dict, *keys: str) -> str:
    lower = {k.lower(): v for k, v in data.items()}
    for key in keys:
        value = lower.get(key.lower())
        if value:
            return str(value).strip()
    return ""


def _parse_item(raw) -> Item | None:
    if isinstance(raw, str):
        m = LEGACY_LINE_RE.match(raw.strip())
        if not m:
            return Item(name=raw.strip(), qty=1, price=0, amount=0, size=_size_from_name(raw))
        name = m["name"].strip()
        return Item(name=name, qty=int(m["qty"]), price=_num(m["price"]),
                    amount=_num(m["amount"]), size=_size_from_name(name))

    name = str(raw.get("name", "")).strip()
    qty = int(_num(raw.get("quantity", 1)) or 1)
    price = _num(raw.get("price"))
    amount = _num(raw.get("amount")) or price * qty
    size, opts = "", []
    for opt in raw.get("options") or []:
        option, variant = str(opt.get("option", "")), str(opt.get("variant", ""))
        opts.append(f"{option}: {variant}")
        if not size and SIZE_OPTION_RE.search(option):
            size = variant.strip().upper()
    return Item(name=name, qty=qty, price=price or (amount / qty if qty else 0), amount=amount,
                size=size or _size_from_name(name), sku=str(raw.get("sku", "") or ""),
                options="; ".join(opts))


def _size_from_name(name: str) -> str:
    m = SIZE_IN_NAME_RE.search(name or "")
    return m.group(1).upper() if m else ""


def unflatten(data: dict) -> dict:
    """payment[products][0][name]=X -> {"payment": {"products": [{"name": "X"}]}}."""
    result: dict = {}
    for key, value in data.items():
        if "[" not in key:
            result[key] = value
            continue
        parts = [key.split("[", 1)[0]] + re.findall(r"\[([^\]]*)\]", key)
        node = result
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        if isinstance(node, dict):
            node[parts[-1]] = value

    def lists(node):
        if isinstance(node, dict):
            node = {k: lists(v) for k, v in node.items()}
            if node and all(k.isdigit() for k in node):
                return [node[k] for k in sorted(node, key=int)]
        return node

    return lists(result)


def parse(data: dict) -> Order:
    if any("[" in k for k in data):
        data = unflatten(data)
    payment = data.get("payment") or {}
    if isinstance(payment, str):
        try:
            payment = json.loads(payment)
        except json.JSONDecodeError:
            payment = {}

    order_id = str(payment.get("orderid") or data.get("tranid") or data.get("orderid") or "").strip()
    items = [i for i in (_parse_item(p) for p in payment.get("products") or []) if i]

    return Order(
        order_id=order_id,
        name=_pick(data, "Name", "name", "ФИО", "Имя"),
        phone=_pick(data, "Phone", "phone", "Телефон"),
        email=_pick(data, "Email", "email"),
        address=_pick(data, "Address", "address", "Адрес") or str(payment.get("delivery_address", "") or ""),
        delivery=str(payment.get("delivery", "") or ""),
        delivery_price=_num(payment.get("delivery_price")),
        promocode=str(payment.get("promocode", "") or ""),
        discount=_num(payment.get("discount")) or _num(payment.get("discountvalue")),
        total=_num(payment.get("amount")),
        comment=_pick(data, "Comment", "comment", "Комментарий"),
        payment_system=_pick(data, "paymentsystem", "payment_system")
        or str(payment.get("sys") or payment.get("paymentsystem") or ""),
        items=items,
    )
