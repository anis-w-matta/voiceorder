from sqlalchemy import select

from app.models import Item


class CatalogueService:
    def __init__(self, session):
        self.s = session

    def all_categories(self) -> list[str]:
        return sorted(self.s.scalars(select(Item.category).distinct()).all())
