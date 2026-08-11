from decimal import Decimal

from sqlalchemy import select

from app.models import Item, Lead, PendingLine, PendingRequest
from app.schemas.enums import Intent, MatchMethod
from app.services.activity_log import log as log_activity
from app.services.item_classifier import classify_line

PRIORITY = [Intent.cancel_order, Intent.update_order,
            Intent.repeat_order_adjusted, Intent.repeat_order,
            Intent.add_order, Intent.get_invoice, Intent.get_bill,
            Intent.catalogue_request, Intent.other]

TARGET_INTENTS = {Intent.cancel_order, Intent.update_order,
                  Intent.repeat_order, Intent.repeat_order_adjusted}


def primary_of(intents) -> str:
    for i in PRIORITY:
        if i in intents:
            return i.value
    return Intent.other.value


class DraftBuilder:
    def __init__(self, session, resolver, prior, flagger, catalogue):
        self.s = session
        self.resolver = resolver
        self.prior = prior
        self.flagger = flagger
        self.catalogue = catalogue

    def build(self, voice, transcript, extraction, customer):
        intents = extraction.intents or [Intent.other]
        cust_nb = customer.customer_number if customer else None

        target, ambiguity = None, None
        reorder = {Intent.repeat_order, Intent.repeat_order_adjusted} & set(intents)
        if cust_nb and (set(intents) & TARGET_INTENTS):
            target, ambiguity = self.prior.resolve_target(
                cust_nb, extraction.order_reference)
            if reorder:
                log_activity(
                    self.s, "reorder_resolved",
                    f"reorder for {cust_nb} resolved to order "
                    f"{target.order_nb}" if target else
                    f"reorder for {cust_nb} could not resolve a target "
                    f"order ({ambiguity})",
                    level="info" if target else "warn", cust_nb=cust_nb,
                    voice_message_id=voice.id,
                    details={"ambiguity": ambiguity} if ambiguity else {})

        lines = []
        if {Intent.repeat_order, Intent.repeat_order_adjusted} & set(intents):
            lines = self._from_prior(target)
        if {Intent.add_order, Intent.repeat_order_adjusted} & set(intents):
            lines += self._from_extraction(extraction, cust_nb,
                                           start=len(lines) + 1)

        flags = self.flagger.compute(transcript=transcript,
                                     extraction=extraction, lines=lines,
                                     customer=customer, ambiguity=ambiguity)

        req = PendingRequest(
            voice_message_id=voice.id, cust_nb=cust_nb,
            intents=[i.value for i in intents],
            primary_intent=primary_of(intents),
            target_order_nb=target.order_nb if target else None,
            target_order_type=target.order_type if target else None,
            raw_model_output=extraction.model_dump(mode="json"),
            flags=flags, status="new")
        req.lines = lines
        self.s.add(req)
        self.s.flush()
        if lines:
            log_activity(self.s, "item_classified",
                        f"classified {len(lines)} line(s) for request {req.id}",
                        request_id=req.id, cust_nb=cust_nb,
                        details={"lines": [{"line_nb": l.line_nb,
                                           "category": l.category}
                                          for l in lines]})
        return req

    def _from_prior(self, target):
        if not target:
            return []
        prior_lines = self.prior.lines_of(target)
        # Look up each item's *current* category rather than trusting the
        # historical order_details snapshot, which may predate this column
        # (NULL) or be stale relative to a catalogue re-categorisation.
        items = {i.item_number: i for i in self.s.scalars(select(Item).where(
            Item.item_number.in_({d.item_nb for d in prior_lines})))} \
            if prior_lines else {}
        return [PendingLine(
            line_nb=i, raw_text=f"[from order {target.order_nb}]",
            item_nb=d.item_nb, item_desc=d.item_desc, qty=d.qty, uom=d.uom,
            match_confidence=1.0, match_method=MatchMethod.prior_order.value,
            category=classify_line(
                matched_category=items[d.item_nb].category
                if d.item_nb in items else None,
                raw_text=d.item_desc, known_categories=[]))
            for i, d in enumerate(prior_lines, start=1)]

    def _from_extraction(self, extraction, cust_nb, start=1):
        out = []
        n = start
        known_categories = self.catalogue.all_categories()
        for el in extraction.lines:
            # Each fallback attempt used to overwrite `cands` outright, so
            # a decent sub-threshold suggestion from an earlier attempt
            # (e.g. an alias fuzzy-matched at 0.6-0.8) vanished the moment
            # a later attempt ran, even when that later attempt found
            # nothing at all. Pool everything every attempt turns up and
            # only keep the best score per item, so the reviewer always
            # sees every candidate any method considered.
            pool: dict[str, object] = {}

            def merge(cand_list):
                for c in cand_list:
                    if c.item_nb not in pool or c.score > pool[c.item_nb].score:
                        pool[c.item_nb] = c

            match = None
            if el.product:
                match, cands = self.resolver.resolve(el.product, cust_nb)
                merge(cands)
            if match is None and el.raw_text:
                # retry on raw_text: Arabic often lands there when the
                # model leaves `product` null
                match, cands = self.resolver.resolve(el.raw_text, cust_nb)
                merge(cands)

            found = []
            if match is None and el.raw_text:
                # last resort: known aliases/descriptions are a closed
                # vocabulary, so look for them as literal (or close fuzzy)
                # matches inside the sentence - catches cases resolve()'s
                # trigram search misses because a short alias against a
                # long sentence scores below its similarity threshold.
                # Can surface more than one product when the extractor
                # merged several into one line.
                found = self.resolver.find_in_text(el.raw_text)
                merge(found)
                if found:
                    match = found[0]

            cands = sorted(pool.values(), key=lambda c: c.score,
                           reverse=True)[:5]

            qty = Decimal(str(el.qty)) if el.qty is not None else None
            out.append(PendingLine(
                line_nb=n, raw_text=el.raw_text, raw_lang=el.raw_lang,
                item_nb=match.item_nb if match else None,
                item_desc=match.item_desc if match else None,
                qty=qty, uom=el.uom,
                match_confidence=match.score if match else None,
                match_method=match.method if match else None,
                candidates=[c.dict() for c in cands],
                category=classify_line(
                    matched_category=match.category if match else None,
                    raw_text=el.raw_text, known_categories=known_categories)))
            n += 1

            for extra in found[1:]:
                # qty/uom deliberately left unset: the extractor gave one
                # quantity for a line that turned out to name several
                # products, and there is no way to tell which number belongs
                # to which. A blank raises missing_qty for the reviewer,
                # where copying the first line's quantity would look
                # confident and be wrong.
                out.append(PendingLine(
                    line_nb=n, raw_text=el.raw_text, raw_lang=el.raw_lang,
                    item_nb=extra.item_nb, item_desc=extra.item_desc,
                    qty=None, uom=None,
                    match_confidence=extra.score, match_method=extra.method,
                    candidates=[extra.dict()],
                    category=classify_line(
                        matched_category=extra.category, raw_text=el.raw_text,
                        known_categories=known_categories)))
                n += 1
        return out

    def build_lead(self, voice, extraction):
        products = [l.product or l.raw_text for l in extraction.lines]
        cats = self.catalogue.categories_for(
            products, extraction.categories_mentioned)
        lead = Lead(voice_message_id=voice.id, phone_e164=voice.phone_e164,
                    categories_sent=cats, products_mentioned=products)
        self.s.add(lead)
        self.s.flush()
        return lead
