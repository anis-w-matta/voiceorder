"""Unit tests for the dataclass-conversion logic in
gemini_command_extractor.py (_to_parsed/_to_items) - the deterministic part
of the module, tested directly against hand-built Gemini response shapes
rather than mocking the genai client itself (this codebase's existing
Gemini-calling classes, e.g. GeminiTranscriber, are likewise exercised via
their pure logic and the evaluate.py harness, not a mocked API response -
see PROGRESS notes on gemini_command_extractor's own accuracy check)."""
from app.services.gemini_command_extractor import (GeminiCommandExtractor,
                                                    _GeminiCommand,
                                                    _GeminiItem, _to_parsed)
from app.services.scripted.models import (ParsedPlaceOrder, ParsedReorder,
                                          ParsedReturnOrder, ParseFailure)


def test_place_order_converts_to_parsed_place_order():
    result = _GeminiCommand(
        command_type="place_order", customer_text="Test Trading",
        items=[_GeminiItem(item_text="blue paint", quantity_text="two",
                           uom_text="cartons")])
    parsed = _to_parsed(result, "raw transcript")
    assert isinstance(parsed, ParsedPlaceOrder)
    assert parsed.customer_text == "Test Trading"
    assert len(parsed.items) == 1
    assert parsed.items[0].item_text == "blue paint"
    assert parsed.items[0].quantity_text == "two"
    assert parsed.items[0].uom_text == "cartons"


def test_place_order_missing_customer_is_parse_failure():
    result = _GeminiCommand(command_type="place_order", customer_text="")
    parsed = _to_parsed(result, "raw transcript")
    assert isinstance(parsed, ParseFailure)


def test_return_order_converts_to_parsed_return_order():
    result = _GeminiCommand(command_type="return_order",
                            order_reference="260000021")
    parsed = _to_parsed(result, "raw transcript")
    assert isinstance(parsed, ParsedReturnOrder)
    assert parsed.order_reference == "260000021"
    assert parsed.items == []


def test_return_order_missing_reference_is_parse_failure():
    result = _GeminiCommand(command_type="return_order", order_reference="")
    parsed = _to_parsed(result, "raw transcript")
    assert isinstance(parsed, ParseFailure)


def test_reorder_last_mode_needs_no_reference():
    result = _GeminiCommand(command_type="reorder", customer_text="Test Trading",
                            reorder_mode="last")
    parsed = _to_parsed(result, "raw transcript")
    assert isinstance(parsed, ParsedReorder)
    assert parsed.mode == "last"
    assert parsed.reference is None


def test_reorder_order_nb_mode_requires_reference():
    result = _GeminiCommand(command_type="reorder", customer_text="Test Trading",
                            reorder_mode="order_nb", order_reference="")
    parsed = _to_parsed(result, "raw transcript")
    assert isinstance(parsed, ParseFailure)


def test_reorder_order_nb_mode_with_reference():
    result = _GeminiCommand(command_type="reorder", customer_text="Test Trading",
                            reorder_mode="order_nb", order_reference="260000021")
    parsed = _to_parsed(result, "raw transcript")
    assert isinstance(parsed, ParsedReorder)
    assert parsed.mode == "order_nb"
    assert parsed.reference == "260000021"


def test_reorder_missing_mode_is_parse_failure():
    result = _GeminiCommand(command_type="reorder", customer_text="Test Trading",
                            reorder_mode=None)
    parsed = _to_parsed(result, "raw transcript")
    assert isinstance(parsed, ParseFailure)


def test_none_command_type_is_parse_failure():
    result = _GeminiCommand(command_type="none")
    parsed = _to_parsed(result, "hello how are you")
    assert isinstance(parsed, ParseFailure)


def test_extract_short_circuits_on_empty_transcript():
    # No genai.Client call needed for this path - constructing the
    # extractor with a placeholder key is enough since extract() never
    # reaches the network for blank input.
    extractor = GeminiCommandExtractor(api_key="unused")
    parsed = extractor.extract("   ")
    assert isinstance(parsed, ParseFailure)
