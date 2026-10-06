from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Product:
    name: str
    match: list[str]
    dane: dict[str, float]  # размер -> выплата, ключ "default" для остальных
    cost: float = 0
    outerwear: bool = False
    stock: dict[str, int] = field(default_factory=dict)

    def dane_for(self, size: str | None) -> float:
        if size and size.upper() in self.dane:
            return self.dane[size.upper()]
        return self.dane.get("default", 0)


class Catalog:
    def __init__(self, products: list[Product]):
        self.products = products

    @classmethod
    def load(cls, path: str) -> "Catalog":
        p = Path(path)
        if not p.exists():
            return cls([])
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        products = []
        for item in raw.get("products", []):
            dane = item.get("dane", 0)
            if isinstance(dane, dict):
                dane = {str(k).upper() if k != "default" else "default": float(v) for k, v in dane.items()}
            else:
                dane = {"default": float(dane)}
            match = item.get("match") or [item["name"]]
            if isinstance(match, str):
                match = [match]
            products.append(Product(
                name=item["name"],
                match=[str(m).lower() for m in match],
                dane=dane,
                cost=float(item.get("cost", 0)),
                outerwear=bool(item.get("outerwear", False)),
                stock={str(k).upper(): int(v) for k, v in (item.get("stock") or {}).items()},
            ))
        return cls(products)

    def find(self, name: str, sku: str = "") -> Product | None:
        name_l, sku_l = (name or "").lower(), (sku or "").lower()
        for product in self.products:
            for m in product.match:
                if (sku_l and m == sku_l) or m in name_l:
                    return product
        return None

    def by_name(self, name: str) -> Product | None:
        return next((p for p in self.products if p.name == name), None)
