from sqlalchemy import select

from app.db import session_scope
from app.models import Customer
from app.services.phone import PhoneNormaliser

n = PhoneNormaliser()
with session_scope() as s:
    ok = bad = 0
    for c in s.scalars(select(Customer)):
        if not c.telephone:
            continue
        e = n.to_e164(c.telephone)
        if e:
            c.phone_e164 = e
            ok += 1
        else:
            bad += 1
            print(f"UNPARSEABLE {c.customer_number}: {c.telephone!r}")
    print(f"normalised={ok} failed={bad}")
