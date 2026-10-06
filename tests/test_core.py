import json

from app.catalog import Catalog
from app.db import DB
from app.formatting import order_card, sizes_text
from app.tilda import parse

CATALOG = """
products:
  - name: Пуховик Arctic
    match: ["ARCTIC"]
    cost: {default: 5000, XL: 6000}
    outerwear: true
    stock: {M: 3, L: 2, XL: 1}
  - name: Худи
    match: ["худи"]
    cost: 1000
"""


def catalog(tmp_path):
    p = tmp_path / "products.yaml"
    p.write_text(CATALOG, encoding="utf-8")
    return Catalog.load(str(p))


def tilda_payload():
    return {
        "Name": "Иван", "Phone": "+79990000000", "tranid": "123:456",
        "payment": json.dumps({
            "orderid": "1001", "amount": 16400, "delivery": "СДЭК", "delivery_price": 400,
            "promocode": "SALE", "discount": 1000,
            "products": [
                {"name": "Пуховик Arctic", "sku": "ARCTIC", "quantity": 1, "amount": 12000, "price": 12000,
                 "options": [{"option": "Размер", "variant": "xl"}]},
                {"name": "Худи черное", "quantity": 2, "amount": 5000, "price": 2500,
                 "options": [{"option": "Размер", "variant": "M"}]},
            ],
        }),
    }


def test_parse_json_payment():
    o = parse(tilda_payload())
    assert o.order_id == "1001"
    assert [(i.name, i.size, i.qty) for i in o.items] == [("Пуховик Arctic", "XL", 1), ("Худи черное", "M", 2)]
    assert o.discount == 1000 and o.delivery_price == 400


def test_parse_flat_form_and_legacy_lines():
    o = parse({"Name": "A", "payment[orderid]": "7", "payment[amount]": "5000",
               "payment[products][0]": "Пуховик Arctic L - 1x5000 = 5000"})
    assert o.order_id == "7"
    assert (o.items[0].name, o.items[0].size, o.items[0].amount) == ("Пуховик Arctic L", "L", 5000)


def test_profit_and_dane(tmp_path):
    cat = catalog(tmp_path)
    db = DB(str(tmp_path / "t.db"))
    pk = db.save_order(parse(tilda_payload()), cat, acquiring_percent=0)
    items = db.items(pk)
    jacket, hoodie = items
    # скидка 1000 делится пропорционально 12000:5000
    assert round(jacket["revenue"]) == 11294 and round(hoodie["revenue"]) == 4706
    # прибыль бренда = выручка − себестоимость, Дане и мне по 50%
    assert jacket["brand_profit"] == round(jacket["revenue"] - 6000, 2)
    assert hoodie["brand_profit"] == round(hoodie["revenue"] - 2000, 2)
    for it in items:
        assert it["dane"] + it["profit"] == it["brand_profit"]
        assert abs(it["dane"] - it["brand_profit"] / 2) < 0.01
    card = order_card(db.order(pk), items)
    assert "Пуховик Arctic XL — 2 647.06" in card and "Худи M ×2 — 1 352.94" in card
    assert "Итого: 4 000" in card and "Дане (50%)" in card and "Мне: <b>4 000 ₽" in card

    t = db.totals()
    assert t.brand_profit == 8000 and t.dane == 4000 and t.not_shipped == 1
    db.add_payout(3000)
    assert db.totals().dane_debt == 1000
    db.set_status(pk, "shipped", "RA123456789RU")
    assert db.totals().shipped == 1 and db.not_shipped() == []

    assert db.size_matrix() == {"Пуховик Arctic": {"XL": 1}}
    assert "XL: 1/0" in sizes_text(db.size_matrix(), cat) and "M: 0/3" in sizes_text(db.size_matrix(), cat)

    db.set_status(pk, "cancelled")
    assert db.totals().dane == 0 and db.size_matrix() == {}


def test_unknown_product(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    pk = db.save_order(parse({"payment": {"orderid": "9", "products": [{"name": "Что-то", "amount": 100}]}}),
                       Catalog([]), 0)
    assert "нет в каталоге" in order_card(db.order(pk), db.items(pk))
