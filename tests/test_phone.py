from app.services.phone import PhoneNormaliser


def test_none_returns_none():
    assert PhoneNormaliser("LB").to_e164(None) is None


def test_empty_string_returns_none():
    assert PhoneNormaliser("LB").to_e164("") is None


def test_garbage_returns_none():
    assert PhoneNormaliser("LB").to_e164("not a phone number") is None


def test_local_lebanese_number_normalises_to_e164():
    assert PhoneNormaliser("LB").to_e164("03123456") == "+9613123456"


def test_already_e164_stays_equivalent():
    assert PhoneNormaliser("LB").to_e164("+9613123456") == "+9613123456"


def test_too_short_number_returns_none():
    # A handful of stray digits should not parse as valid even though
    # phonenumbers.parse() itself won't raise on it.
    assert PhoneNormaliser("LB").to_e164("123") is None


def test_wrong_region_still_parses_explicit_country_code():
    # A number with its own leading '+' carries its own country code, so
    # the configured default region should not override it.
    assert PhoneNormaliser("LB").to_e164("+14155552671") == "+14155552671"
