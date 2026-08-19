"""Seed the isolated voiceorder_test schema with richer dummy data.

Run against the test schema only - never against the dev/prod database:

    DATABASE_URL='postgresql+psycopg://voiceorder:changeme@localhost/voiceorder?options=-c%20search_path%3Dvoiceorder_test%2Cpublic' \
        .venv/Scripts/python seed_test.py

Deliberately includes a few edge cases so tests have real fixtures for them:
  - I999 has no unit_price
  - C003 has no email on file
  - order 990000003 has zero lines
  - order 990000004 belongs to C002, used to exercise the "wrong customer"
    mismatch path against C001
"""
from app.db import session_scope
from app.models import Customer, Item, ItemAlias, OrderDetail, OrderHeader

ITEMS = [
    ("A100", "Blue Paint 5L", "Paint", 19.50,
     [("دهان أزرق", "ar"), ("dhan azraq", "ar-latn"), ("blue color", "en")]),
    ("A101", "White Paint 5L", "Paint", 19.50,
     [("دهان أبيض", "ar"), ("dhan abyad", "ar-latn")]),
    ("B200", "Paint Brush 2 inch", "Tools", 3.25,
     [("فرشاة", "ar"), ("firshaye", "ar-latn"), ("brush", "en")]),
    ("B201", "Paint Roller Large", "Tools", 8.75,
     [("رولة", "ar"), ("roleh", "ar-latn")]),
    ("C300", "Masking Tape 50m", "Consumables", 2.50,
     [("لاصق", "ar"), ("lasse2", "ar-latn"), ("tape", "en")]),
    ("I999", "Discontinued Sample Item", "Misc", None, []),
]

CUSTOMERS = [
    ("C001", "Test Trading", "orders+c001@testtrading.example",
     "03123456", "+9613123456", "Beirut", "Hamra"),
    ("C002", "Beirut Hardware Co", "purchasing@beiruthardware.example",
     "01654321", "+9611654321", "Beirut", "Verdun"),
    ("C003", "Zahle Paint Supply", None,
     "08765432", "+9618765432", "Zahle", "Main Street"),
]

with session_scope() as s:
    for nb, name, email, tel, e164, city, addr in CUSTOMERS:
        s.add(Customer(customer_number=nb, customer_name=name, email=email,
                       telephone=tel, phone_e164=e164, city=city,
                       address1=addr))

    for nb, desc, cat, price, aliases in ITEMS:
        s.add(Item(item_number=nb, item_desc=desc, category=cat,
                   unit_price=price))
        for alias, lang in aliases:
            s.add(ItemAlias(item_number=nb, alias=alias, lang=lang))

    s.add(OrderHeader(order_nb="990000001", order_type="SO", cust_nb="C001",
                      status="open", source="voice"))
    s.add(OrderDetail(order_nb="990000001", order_type="SO", line_nb=1,
                      item_nb="A100", item_desc="Blue Paint 5L", qty=3,
                      uom="PCS", unit_price=19.50))
    s.add(OrderDetail(order_nb="990000001", order_type="SO", line_nb=2,
                      item_nb="B200", item_desc="Paint Brush 2 inch", qty=2,
                      uom="PCS", unit_price=3.25))

    s.add(OrderHeader(order_nb="990000002", order_type="SO", cust_nb="C001",
                      status="open", source="voice"))
    s.add(OrderDetail(order_nb="990000002", order_type="SO", line_nb=1,
                      item_nb="I999", item_desc="Discontinued Sample Item",
                      qty=1, uom="PCS", unit_price=None))

    s.add(OrderHeader(order_nb="990000003", order_type="SO", cust_nb="C001",
                      status="open", source="manual"))

    s.add(OrderHeader(order_nb="990000004", order_type="SO", cust_nb="C002",
                      status="open", source="voice"))
    s.add(OrderDetail(order_nb="990000004", order_type="SO", line_nb=1,
                      item_nb="C300", item_desc="Masking Tape 50m", qty=5,
                      uom="PCS", unit_price=2.50))

print("seeded voiceorder_test")
