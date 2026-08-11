from enum import Enum


class Intent(str, Enum):
    add_order = "add_order"
    repeat_order = "repeat_order"
    repeat_order_adjusted = "repeat_order_adjusted"
    update_order = "update_order"
    cancel_order = "cancel_order"
    get_invoice = "get_invoice"
    get_bill = "get_bill"
    catalogue_request = "catalogue_request"
    other = "other"


class ChangeType(str, Enum):
    add = "add"
    remove = "remove"
    increase = "increase"
    decrease = "decrease"


class RequestStatus(str, Enum):
    new = "new"
    in_review = "in_review"
    callback = "callback"
    rejected = "rejected"
    committed = "committed"


class MatchMethod(str, Enum):
    exact = "exact"
    alias = "alias"
    fuzzy = "fuzzy"
    substring = "substring"
    prior_order = "prior_order"
    manual = "manual"
