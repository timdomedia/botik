"""Разбор сообщений из беседы: «шуба Л с капюшоном долями», «2 тайно переводом», «кружево 2000»,
«бомбер с промо 5%», «Наликом 30к✅»."""
import re
from dataclasses import dataclass

from .catalog import Catalog, PaymentMethod, Product

# Размер: латиница в любом регистре, кириллица только заглавными (иначе «с» = предлог).
SIZE_LAT_RE = re.compile(r"(?<![\w])(xxs|xs|s|m|l|xl|xxl|xxxl|2xl|3xl|4xl)(?![\w])", re.I)
SIZE_CYR_RE = re.compile(r"(?<![\w])(ХХС|ХС|С|М|Л|ХЛ|ХХЛ|ХХХЛ)(?![\w])")
SIZE_NUM_RE = re.compile(r"(?<![\w%])([4-6]\d)(?![\w%])")
CYR_TO_LAT = str.maketrans("ХСМЛ", "XSML")

PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
MONEY_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?: \d{3})+|\d+(?:[.,]\d+)?)\s*(к|k|тыс\.?)?(?![\w%])", re.I)
QTY_RE = re.compile(r"^\s*(\d{1,2})\s+(?=\D)|(?:[x×х]\s*(\d{1,2})|(\d{1,2})\s*шт)(?![\w])", re.I)
PAYOUT_WORDS_RE = re.compile(r"налик|налом|наличк|скинул|перев[её]л|отдал|долг|получил|кинул|занес|занёс", re.I)


@dataclass
class Sale:
    product: Product
    size: str
    qty: int
    price: float          # итог за позицию до промо
    discount: float       # промо в рублях
    payment: PaymentMethod | None
    note: str             # «витрина» и т.п.
    confident: bool       # есть признак продажи (оплата, цена, размер, промо…), а не просто упоминание

    @property
    def amount(self) -> float:
        return self.price - self.discount


def _money(m: re.Match) -> float:
    value = float(m.group(1).replace(" ", "").replace(",", "."))
    return value * 1000 if m.group(2) else value


def parse_size(text: str) -> str:
    for rx in (SIZE_LAT_RE, SIZE_CYR_RE):
        if m := rx.search(text):
            return m.group(1).upper().translate(CYR_TO_LAT)
    if m := SIZE_NUM_RE.search(text):
        return m.group(1)
    return ""


def parse_sale(text: str, catalog: Catalog) -> Sale | None:
    """Продажа из сообщения, если в нём есть товар из каталога. Иначе None."""
    product = catalog.find(text)
    if not product:
        return None
    # чтобы название («Тайно 535») не путалось с ценой/размером, вырезаем его
    rest = text
    for alias in sorted(product.match, key=len, reverse=True):
        rest = re.sub(re.escape(alias), " ", rest, flags=re.I)

    size = parse_size(rest)
    qty = 1
    if m := QTY_RE.search(rest):
        qty = int(next(g for g in m.groups() if g))
        rest = rest[:m.start()] + " " + rest[m.end():]

    percent = 0.0
    if m := PERCENT_RE.search(rest):
        percent = float(m.group(1).replace(",", "."))
        rest = rest[:m.start()] + " " + rest[m.end():]

    explicit = [_money(m) for m in MONEY_RE.finditer(rest)]
    explicit = [v for v in explicit if v >= 100 and not (40 <= v <= 60)]
    price = explicit[0] if explicit else product.price_for(size) * qty
    discount = round(price * percent / 100, 2)

    notes = [w for w in ("витрина", "витрины", "обмен", "подарок") if w in text.lower()]
    payment = catalog.payment(text)
    confident = not text.strip().endswith("?") and bool(
        payment or explicit or size or percent or notes or qty > 1)
    return Sale(product, size, qty, price, discount, payment, ", ".join(notes), confident)


def parse_payout(text: str) -> float | None:
    """«Наликом 30к✅», «5к в долг», «скинул 12000» -> сумма. Без ключевого слова не срабатывает."""
    if not PAYOUT_WORDS_RE.search(text):
        return None
    amounts = [_money(m) for m in MONEY_RE.finditer(text)]
    amounts = [a for a in amounts if a >= 100]
    return amounts[0] if amounts else None
