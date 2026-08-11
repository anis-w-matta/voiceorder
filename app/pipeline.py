from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db import session_scope
from app.errors import VoiceMessageNotFound
from app.models import Customer, VoiceMessage
from app.schemas.enums import Intent
from app.services.bill_request import maybe_send_bill_notification
from app.services.catalogue import CatalogueService
from app.services.draft_builder import DraftBuilder
from app.services.flagger import Flagger
from app.services.item_resolver import ItemResolver
from app.services.mailer import Mailer
from app.services.prior_order import PriorOrderService


class IntakePipeline:
    def __init__(self, stt, classifier, audio, mailer=None):
        self.stt = stt
        self.classifier = classifier
        self.audio = audio
        self.mailer = mailer or Mailer()

    def process(self, voice_message_id: int) -> None:
        with session_scope() as s:
            voice = s.get(VoiceMessage, voice_message_id)
            if voice is None:
                raise VoiceMessageNotFound(voice_message_id)

            tr = self.stt.transcribe(self.audio.absolute(voice.audio_path))
            voice.transcript = tr.text
            voice.transcript_conf = tr.confidence
            voice.language = tr.language
            voice.languages = tr.languages
            voice.segments = [sg.model_dump() for sg in tr.segments]
            voice.duration_sec = Decimal(str(round(tr.duration, 2)))
            voice.status = "transcribed"
            s.flush()

            customer = None
            if voice.phone_e164:
                customer = s.scalars(select(Customer).where(
                    Customer.phone_e164 == voice.phone_e164)).first()

            extraction = self.classifier.classify(tr.text)
            voice.status = "classified"
            s.flush()

            resolver = ItemResolver(s)
            builder = DraftBuilder(s, resolver, PriorOrderService(s),
                                   Flagger(), CatalogueService(s, resolver))

            if customer is None:
                builder.build_lead(voice, extraction)
            else:
                req = builder.build(voice, tr, extraction, customer)
                if Intent.get_bill in extraction.intents:
                    maybe_send_bill_notification(
                        s, self.mailer, cust_nb=customer.customer_number,
                        order_reference=extraction.order_reference,
                        voice_message_id=voice.id, request_id=req.id)

            voice.status = "drafted"
            voice.processed_at = datetime.now(timezone.utc)
