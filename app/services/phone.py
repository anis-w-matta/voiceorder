import phonenumbers

from app.config import settings


class PhoneNormaliser:
    def __init__(self, default_region: str | None = None):
        self.region = default_region or settings.default_phone_region

    def to_e164(self, raw: str | None) -> str | None:
        if not raw:
            return None
        try:
            n = phonenumbers.parse(raw, self.region)
        except phonenumbers.NumberParseException:
            return None
        if not phonenumbers.is_valid_number(n):
            return None
        return phonenumbers.format_number(
            n, phonenumbers.PhoneNumberFormat.E164)
