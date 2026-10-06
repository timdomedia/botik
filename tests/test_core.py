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
    card = order_card(db.order(pk), items, debt=4000)
    assert "Пуховик Arctic XL — 11 294" in card and "Худи M ×2 — 4 706" in card
    assert "Дане: 2 647 + 1 353 = 4 000" in card and "должен Дане всего: 4 000" in card

    t = db.totals()
    assert t.brand_profit == 8000 and t.dane == 4000 and t.not_shipped == 1
    db.set_dane_paid(pk, True)
    assert db.totals().dane_debt == 0 and db.dane_by_order()[0]["dane_paid_at"]
    assert "✅ отдано" in order_card(db.order(pk), items)
    db.set_dane_paid(pk, False)
    assert db.totals().dane_debt == 4000
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


PAY_CATALOG = """
products:
  - name: Шуба
    cost: 9000
  - name: Кружево
    cost: 800
"""


def test_payment_fee(tmp_path):
    p = tmp_path / "products.yaml"
    p.write_text(PAY_CATALOG, encoding="utf-8")
    cat = Catalog.load(str(p))
    db = DB(str(tmp_path / "t.db"))
    pk = db.save_order(parse({"paymentsystem": "dolyame", "payment": {"orderid": "d1", "products": [
        {"name": "Шуба", "amount": 25000, "options": [{"option": "Размер", "variant": "L"}]}]}}),
        cat, acquiring_percent=3.5)
    it = db.items(pk)[0]
    assert it["fee"] == 1750  # 7% долями
    assert it["brand_profit"] == 25000 - 1750 - 9000 and it["dane"] == 7125
    assert db.order(pk)["payment"] == "долями"
    # Тильда с картой
    pk = db.save_order(parse({"paymentsystem": "tinkoff", "payment": {"orderid": "t1", "products": [
        {"name": "Кружево", "amount": 3000, "quantity": 1}]}}), cat, acquiring_percent=3.5)
    assert db.items(pk)[0]["fee"] == 105 and db.order(pk)["payment"] == "картой"


def test_reaction_marks_dane_paid(tmp_path):
    import asyncio
    import types

    from app.bot import App, build_router
    from app.sheets import Sheets

    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, **kw):
            self.sent.append(text)
            return types.SimpleNamespace(message_id=100 + len(self.sent))

        async def edit_message_text(self, text, **kw):
            self.sent.append(text)

    settings = types.SimpleNamespace(chat_id=1, acquiring_percent=0, dane_share_percent=50,
                                     ship_deadline_days=3, payer_ids=frozenset({42}))
    db = DB(str(tmp_path / "t.db"))
    app = App(FakeBot(), db, Catalog([]), Sheets("", "", db, Catalog([]), 3), settings)
    handler = build_router(app).message_reaction.handlers[0].callback

    def react(uid, emojis):
        return types.SimpleNamespace(user=types.SimpleNamespace(id=uid), message_id=101, new_reaction=emojis)

    async def run():
        await app.new_order(parse({"payment": {"orderid": "1", "products": [{"name": "X", "amount": 1000}]}}))
        await handler(react(7, ["🔥"]))   # реакция Дани не считается
        assert db.totals().dane_debt == 500
        await handler(react(42, ["👍"]))  # моя реакция — отдал
        assert db.totals().dane_debt == 0 and "✅ отдано" in app.bot.sent[-1]
        assert "Дане всё отдано" in app.dolg_text()
        await handler(react(42, []))      # снял реакцию — снова долг
        assert db.totals().dane_debt == 500 and "#1" in app.dolg_text()

    asyncio.run(run())
