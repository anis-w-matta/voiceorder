from app.db import session_scope
from app.models import Customer, Item, ItemAlias

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
]

with session_scope() as s:
    s.add(Customer(customer_number="C001", customer_name="Test Trading",
                   email="orders+c001@testtrading.example",
                   telephone="03123456", phone_e164="+9613123456",
                   city="Beirut", address1="Hamra"))
    for nb, desc, cat, price, aliases in ITEMS:
        s.add(Item(item_number=nb, item_desc=desc, category=cat,
                   unit_price=price))
        for alias, lang in aliases:
            s.add(ItemAlias(item_number=nb, alias=alias, lang=lang))
print("seeded")
