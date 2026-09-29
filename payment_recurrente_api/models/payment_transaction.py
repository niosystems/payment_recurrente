from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlparse

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import urls

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_recurrente_api import const
from odoo.addons.payment_recurrente_api.controllers.main import RecurrenteApiController

_logger = get_payment_logger(__name__)


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    def _get_specific_rendering_values(self, processing_values):
        """Override of `payment` to return Recurrente-specific rendering values.

        Note: self.ensure_one() from `_get_processing_values`

        :param dict processing_values: The generic and specific processing values of the transaction
        :return: The dict of provider-specific rendering values
        :rtype: dict
        """
        if self.provider_code != const.PROVIDER_CODE:
            return super()._get_specific_rendering_values(processing_values)

        try:
            checkout = self._send_api_request(
                "POST", "/checkouts", json=self._recurrente_api_prepare_checkout_payload()
            )
        except ValidationError as error:
            self._set_error(str(error))
            return {}

        # The provider reference is set now to allow fetching the checkout after redirection.
        self.provider_reference = checkout["id"]

        checkout_url = checkout["checkout_url"]
        return {
            "api_url": checkout_url,
            "url_params": dict(parse_qsl(urlparse(checkout_url).query)),
        }

    def _recurrente_api_prepare_checkout_payload(self):
        """Create the payload of the checkout creation request from the transaction values.

        Recurrente does not give back the transaction reference when redirecting the customer, so
        it is embedded in the return URLs. The cancel URL is additionally protected by an access
        token so that nobody but the customer can cancel a transaction through it.

        :return: The request payload
        :rtype: dict
        """
        base_url = self.provider_id.get_base_url()
        return_url = urls.urljoin(base_url, RecurrenteApiController._return_url)
        cancel_url = urls.urljoin(base_url, RecurrenteApiController._cancel_url)
        access_token = payment_utils.generate_access_token(self.reference, env=self.env)
        return {
            "items": [
                {**item, "charge_type": "one_time"} for item in self._recurrente_api_prepare_items()
            ],
            "success_url": f"{return_url}?{urlencode({'ref': self.reference})}",
            "cancel_url": (
                f"{cancel_url}?{urlencode({'ref': self.reference, 'access_token': access_token})}"
            ),
            "metadata": {"odoo_reference": self.reference},
        }

    def _recurrente_api_prepare_items(self):
        """Return the items to charge, for both hosted checkouts and saved card charges.

        :rtype: list[dict]
        """
        return [
            {
                "name": f"{self.company_id.name} - {self.reference}",
                "amount_in_cents": payment_utils.to_minor_currency_units(
                    self.amount, self.currency_id
                ),
                "currency": self.currency_id.name,
                "quantity": 1,
            }
        ]

    @api.model
    def _extract_reference(self, provider_code, payment_data):
        """Override of `payment` to extract the reference from the payment data.

        The reference is either embedded in the return URLs (`ref`) or, for webhook notifications,
        deduced from the checkout, refund or intent id that was stored as the provider reference.
        """
        if provider_code != const.PROVIDER_CODE:
            return super()._extract_reference(provider_code, payment_data)

        if reference := payment_data.get("ref"):
            return reference
        candidates = [
            (payment_data.get("checkout") or {}).get("id"),
            (payment_data.get("refund") or {}).get("id"),
        ]
        if str(payment_data.get("event_type", "")).startswith(const.WEBHOOK_EVENT_PREFIX):
            candidates.append(payment_data.get("id"))  # The intent of a saved card payment.
        if not (candidates := [candidate for candidate in candidates if candidate]):
            return None
        return self.search(
            [
                ("provider_code", "=", provider_code),
                ("provider_reference", "in", candidates),
            ],
            limit=1,
        ).reference

    def _apply_updates(self, payment_data):
        """Override of `payment` to update the transaction based on the checkout, refund or intent.

        The payment data is always the checkout or refund as fetched from the API, never a raw
        notification.
        """
        if self.provider_code != const.PROVIDER_CODE:
            super()._apply_updates(payment_data)
            return

        if self.operation == "refund":
            self._recurrente_api_apply_refund_updates(payment_data)
            return

        kind = self._recurrente_api_get_data_kind(payment_data)
        if kind == "intent":
            self._recurrente_api_apply_intent_updates(payment_data)
            return
        if kind == "one_time_payment":
            self._recurrente_api_apply_one_time_payment_updates(payment_data)
            return

        status = payment_data.get("status")
        if status == const.CHECKOUT_STATUS_PAID:
            # A customer may pay through the checkout link even after having canceled.
            self._set_done(extra_allowed_states=("cancel",))
        elif status == const.CHECKOUT_STATUS_IN_PROGRESS:
            self._set_pending()
        elif status == const.CHECKOUT_STATUS_EXPIRED:
            self._set_canceled(state_message=self.env._("The Recurrente checkout has expired."))
        elif status == const.CHECKOUT_STATUS_UNPAID:
            if payment_data.get(const.RETURN_CANCEL_FLAG):
                self._set_canceled(
                    state_message=self.env._("The customer canceled the payment on Recurrente.")
                )
        else:
            _logger.warning(
                "Received data with unknown checkout status %s for transaction %s.",
                status,
                self.reference,
            )
            self._set_error(self.env._("Received data with unknown checkout status: %s.", status))

    def _extract_amount_data(self, payment_data):
        """Override of `payment` to extract the amount and currency from the checkout."""
        if self.provider_code != const.PROVIDER_CODE:
            return super()._extract_amount_data(payment_data)

        if self.operation == "refund":
            amount_in_cents = payment_data.get("customer_refunded_amount_in_cents")
            if amount_in_cents is None:
                return None  # Nothing to compare with; refunds are initiated by the merchant.
            return {
                "amount": payment_utils.to_major_currency_units(amount_in_cents, self.currency_id),
                "currency_code": payment_data.get("currency"),
            }

        kind = self._recurrente_api_get_data_kind(payment_data)
        if kind == "one_time_payment":
            return None  # The response of the charge carries no amount; it was chosen by Odoo.
        amount_in_cents = payment_data.get(
            "amount_in_cents" if kind == "intent" else "total_in_cents"
        )
        if amount_in_cents is None:
            # A missing amount makes the validation fail instead of skipping it.
            return {"amount": None, "currency_code": payment_data.get("currency")}
        return {
            "amount": payment_utils.to_major_currency_units(amount_in_cents, self.currency_id),
            "currency_code": payment_data.get("currency"),
        }

    def _send_refund_request(self):
        """Override of `payment` to send a refund request to Recurrente.

        Recurrente refunds an intent, whose id is read from the source transaction's checkout. The
        amount is only sent for partial refunds: without it, Recurrente refunds the whole
        outstanding balance, which is the only thing some card processors accept.
        """
        if self.provider_code != const.PROVIDER_CODE:
            return super()._send_refund_request()

        payload = {"intent_id": self._recurrente_api_get_source_intent_id()}
        if not self._recurrente_api_is_full_refund():
            payload["amount_in_cents"] = payment_utils.to_minor_currency_units(
                -self.amount,  # Refund transactions' amount is negative, inverse it.
                self.currency_id,
            )
        refund = self._send_api_request(
            "POST",
            "/refunds",
            json=payload,
            idempotency_key=payment_utils.generate_idempotency_key(self, scope="refund"),
        )

        self._process(const.PROVIDER_CODE, refund)

    def _recurrente_api_get_source_intent_id(self):
        """Return the id of the Recurrente intent that the refund's source transaction paid.

        :rtype: str
        :raise ValidationError: If the intent cannot be determined.
        """
        source_reference = self.source_transaction_id.provider_reference or ""
        if source_reference.startswith(const.INTENT_ID_PREFIX):
            return source_reference
        if source_reference.startswith(const.ONE_TIME_PAYMENT_ID_PREFIX):
            raise ValidationError(
                self.env._(
                    "This payment could not be linked to its Recurrente intent, so it cannot be"
                    " refunded from Odoo. Refund it from Recurrente's dashboard."
                )
            )

        checkout = self._send_api_request("GET", f"/checkouts/{source_reference}")
        intent_id = (checkout.get("latest_intent") or {}).get("id")
        if not intent_id:
            raise ValidationError(
                self.env._("The payment to refund could not be found on Recurrente.")
            )
        return intent_id

    def _recurrente_api_is_full_refund(self):
        """Return whether the refund covers everything not yet refunded of its source transaction.

        :rtype: bool
        """
        self.ensure_one()

        source_tx = self.source_transaction_id
        already_refunded = -sum(
            source_tx.child_transaction_ids.filtered(
                lambda tx: (
                    tx != self and tx.operation == "refund" and tx.state in ("pending", "done")
                )
            ).mapped("amount")
        )
        outstanding_amount = source_tx.amount - already_refunded
        return self.currency_id.compare_amounts(-self.amount, outstanding_amount) >= 0

    def _recurrente_api_apply_refund_updates(self, refund):
        """Update the refund transaction based on the refund fetched from the API.

        :param dict refund: The refund, as returned by `/refunds`.
        """
        if refund_id := refund.get("id"):
            self.provider_reference = refund_id

        status = refund.get("status")
        if status == const.REFUND_STATUS_SUCCEEDED:
            self._set_done()
        elif status == const.REFUND_STATUS_PENDING:
            self._set_pending()
        elif status == const.REFUND_STATUS_FAILED:
            self._set_error(
                self.env._(
                    "The refund failed on Recurrente: %s",
                    refund.get("failure_reason") or self.env._("no reason given"),
                )
            )
        elif status == const.REFUND_STATUS_VOIDED:
            self._set_canceled(state_message=self.env._("The refund was voided on Recurrente."))
        else:
            _logger.warning(
                "Received data with unknown refund status %s for transaction %s.",
                status,
                self.reference,
            )
            self._set_error(self.env._("Received data with unknown refund status: %s.", status))

    def _extract_token_values(self, payment_data):
        """Override of `payment` to extract the saved card from the paid checkout.

        The `payment_method` of a paid checkout can be charged again later with
        `POST /one_time_payments`, so it is what the token keeps as its reference.
        """
        if self.provider_code != const.PROVIDER_CODE:
            return super()._extract_token_values(payment_data)

        payment_method = payment_data.get("payment_method") or {}
        if not payment_method.get("id") or payment_method.get("type") != "card":
            _logger.warning(
                "Transaction %s cannot be tokenized: the payment method is not a card.",
                self.reference,
            )
            return {}

        token_values = {"provider_ref": payment_method["id"]}
        if last4 := (payment_method.get("card") or {}).get("last4"):
            token_values["payment_details"] = last4
        return token_values

    def _send_payment_request(self):
        """Override of `payment` to charge a saved card through Recurrente.

        The charge response only holds the id and status of the one-time payment. The unified
        intent behind it is looked up to get the amount, the normalized status and an identifier
        that refunds and notifications can use. Without it, the plain response is processed.
        """
        if self.provider_code != const.PROVIDER_CODE:
            return super()._send_payment_request()

        payment = self._send_api_request(
            "POST",
            "/one_time_payments",
            json={
                "payment_method_id": self.token_id.provider_ref,
                "items": self._recurrente_api_prepare_items(),
            },
            idempotency_key=payment_utils.generate_idempotency_key(self, scope="one_time_payment"),
        )

        intent = self._recurrente_api_find_intent(payment["id"])
        # The provider reference is set when the data is processed: transactions cannot be
        # written directly here, and the charge cannot be rolled back.
        self._process(const.PROVIDER_CODE, intent or payment)

    def _recurrente_api_find_intent(self, one_time_payment_id):
        """Find the unified intent of a one-time payment in the most recent intents.

        :param str one_time_payment_id: The id (`on_...`) returned when charging the saved card.
        :return: The intent, or `None` if it could not be found.
        :rtype: dict|None
        """
        now = fields.Datetime.now()
        window = timedelta(minutes=const.INTENT_LOOKUP_MINUTES)
        try:
            intents = self._send_api_request(
                "GET",
                "/intents",
                params={
                    "from_time": (now - window).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "until_time": (now + window).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
        except ValidationError:
            _logger.warning("Unable to look up the intent of payment %s.", one_time_payment_id)
            return None

        for intent in intents if isinstance(intents, list) else []:
            paymentable = (intent.get("payment") or {}).get("paymentable") or {}
            if paymentable.get("id") == one_time_payment_id:
                return intent
        return None

    @staticmethod
    def _recurrente_api_get_data_kind(payment_data):
        """Return which Recurrente object the payment data is: an intent, a one-time payment or a
        checkout.

        :rtype: str
        """
        if payment_data.get("object") == "one_time_payment":
            return "one_time_payment"
        if "raw_status" in payment_data:
            return "intent"
        return "checkout"

    def _recurrente_api_apply_intent_updates(self, intent):
        """Update the transaction based on the unified intent of a payment made with a saved card.

        :param dict intent: The intent, as returned by `/intents`.
        """
        if intent_id := intent.get("id"):
            self.provider_reference = intent_id

        status = intent.get("status")
        if status == const.INTENT_STATUS_SUCCEEDED:
            self._set_done()
        elif status == const.INTENT_STATUS_PENDING:
            self._set_pending()
        elif status == const.INTENT_STATUS_CANCELED:
            self._set_canceled(state_message=self.env._("The payment was canceled on Recurrente."))
        elif status == const.INTENT_STATUS_FAILED:
            failure_reason = (intent.get("details") or {}).get("failure_reason")
            self._set_error(
                self.env._(
                    "The payment failed on Recurrente: %s",
                    failure_reason or self.env._("no reason given"),
                )
            )
        else:
            _logger.warning(
                "Received data with unknown intent status %s for transaction %s.",
                status,
                self.reference,
            )
            self._set_error(self.env._("Received data with unknown intent status: %s.", status))

    def _recurrente_api_apply_one_time_payment_updates(self, payment):
        """Update the transaction based on the plain response of a saved card charge.

        :param dict payment: The response of `POST /one_time_payments`.
        """
        if payment_id := payment.get("id"):
            self.provider_reference = payment_id

        status = payment.get("status")
        if status == const.ONE_TIME_PAYMENT_STATUS_PAID:
            self._set_done()
        else:
            _logger.warning(
                "Received data with unknown one-time payment status %s for transaction %s.",
                status,
                self.reference,
            )
            self._set_error(self.env._("Received data with unknown payment status: %s.", status))
