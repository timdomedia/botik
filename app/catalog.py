import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def _by_size(value, cast=float) -> dict:
    """5000 -> {"default": 5000}; {default: 5000, XL: 5400} -> {"default": 5000, "XL": 5400}."""
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return {("default" if k == "default" else str(k).upper()): cast(v) for k, v in value.items()}
    return {"default": cast(value)}


def _for_size(table: dict, size: str | None) -> float:
    if size and size.upper() in table:
        return table[size.upper()]
    return table.get("default", 0)


@dataclass
class Product:
    name: str
    match: list[str]               # как товар называется в Тильде и в чате (без регистра)
    cost: dict[str, float]         # себестоимость 1 шт. по размерам, "default" для остальных
    price: dict[str, float] = field(default_factory=dict)  # розничная цена (для продаж из чата)
    outerwear: bool = False
    stock: dict[str, int] = field(default_factory=dict)

    def cost_for(self, size: str | None) -> float:
        return _for_size(self.cost, size)

    def price_for(self, size: str | None) -> float:
        return _for_size(self.price, size)


@dataclass
class PaymentMethod:
    key: str              # как пишем в чате: «долями», «переводом»…
    fee: float            # комиссия, %
    aliases: list[str]    # подстроки для распознавания (в чате и в paymentsystem Тильды)


DEFAULT_PAYMENTS = [
    PaymentMethod("долями", 7, ["долями", "долям", "dolyame"]),
    PaymentMethod("сбп", 0.7, ["сбп", "sbp"]),
    PaymentMethod("переводом", 0, ["перевод"]),
    PaymentMethod("налом", 0, ["нал", "cash"]),
    PaymentMethod("картой", 3.5, ["карт", "tinkoff", "тинькофф", "cloudpayments", "yookassa", "юкасс"]),
]


class Catalog:
    def __init__(self, products: list[Product], payments: list[PaymentMethod] | None = None):
        self.products = products
        self.payments = payments if payments is not None else list(DEFAULT_PAYMENTS)

    @classmethod
    def load(cls, path: str) -> "Catalog":
        p = Path(path)
        if not p.exists():
            return cls([])
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        products = []
        for item in raw.get("products", []):
            match = item.get("match") or []
            if isinstance(match, str):
                match = [match]
            match = [str(m).lower() for m in match]
            if item["name"].lower() not in match:
                match.append(item["name"].lower())
            products.append(Product(
                name=item["name"],
                match=match,
                cost=_by_size(item.get("cost", 0)),
                price=_by_size(item.get("price")),
                outerwear=bool(item.get("outerwear", False)),
                stock=_by_size(item.get("stock"), int),
            ))
        payments = None
        if raw.get("payments"):
            payments = [PaymentMethod(key, float(cfg.get("fee", 0)),
                                      [str(a).lower() for a in cfg.get("aliases") or [key]])
                        for key, cfg in raw["payments"].items()]
        return cls(products, payments)

    def find(self, name: str, sku: str = "") -> Product | None:
        """Товар по артикулу или по самому длинному совпавшему названию («зипка тайно» > «тайно»)."""
        name_l, sku_l = (name or "").lower(), (sku or "").lower()
        best, best_len = None, 0
        for product in self.products:
            for m in product.match:
                if sku_l and m == sku_l:
                    return product
                if m in name_l and len(m) > best_len:
                    best, best_len = product, len(m)
        return best

    def by_name(self, name: str) -> Product | None:
        return next((p for p in self.products if p.name == name), None)

    def payment(self, text: str) -> PaymentMethod | None:
        text = (text or "").lower()
        # алиас должен начинать слово: «наликом» — нал, а «оригинал» — нет
        hits = [(len(a), pm) for pm in self.payments for a in pm.aliases
                if re.search(r"(?<![\w])" + re.escape(a), text)]
        return max(hits, key=lambda h: h[0])[1] if hits else None
