from app.services import catalog_client


class CatalogueService:
    def __init__(self, session=None):
        self.s = session

    def all_categories(self) -> list[str]:
        return catalog_client.all_item_categories()
