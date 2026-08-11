from sqlalchemy import select

from app.models import Item


class CatalogueService:
    def __init__(self, session, resolver):
        self.s = session
        self.resolver = resolver

    def all_categories(self) -> list[str]:
        return sorted(self.s.scalars(select(Item.category).distinct()).all())

    def categories_for(self, products, categories) -> list[str]:
        valid = set(self.all_categories())
        cats = {c for c in categories if c in valid}
        for p in products:
            match, _ = self.resolver.resolve(p)
            if match:
                cats.add(match.category)
        return sorted(cats) if cats else self.all_categories()
