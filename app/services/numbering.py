from datetime import datetime

from sqlalchemy import text


class OrderNumberService:
    def __init__(self, session):
        self.s = session

    def next(self) -> str:
        # One global sequence for every order type - order_type is part of the
        # order's primary key, so numbers never collide across types. The
        # parameter this used to take was never read.
        n = self.s.execute(text("SELECT nextval('order_nb_seq')")).scalar()
        return f"{datetime.now().year % 100}{n:07d}"
