import json
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.payment.tests.http_common import PaymentHttpCommon
from odoo.addons.payment_recurrente_api import const, utils
from odoo.addons.payment_recurrente_api.controllers.main import RecurrenteApiController
from odoo.addons.payment_recurrente_api.tests.common import (
    WEBHOOK_SECRET,
    RecurrenteApiCommon,
    sign_webhook,
)

SEND_API_REQUEST = "odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request"


@tagged("post_install", "-at_install")
class RecurrenteApiModelTest(RecurrenteApiCommon):
    def test_checkout_payload(self):
        tx = self._create_transaction("redirect")

        payload = tx._recurrente_api_prepare_checkout_payload()

        item = payload["items"][0]
        self.assertEqual(item["amount_in_cents"], 111111)
        self.assertEqual(item["currency"], "USD")
        self.assertEqual(item["quantity"], 1)
        self.assertIn(tx.reference, item["name"])
        self.assertEqual(payload["metadata"], {"odoo_reference": tx.reference})

        success_query = parse_qs(urlparse(payload["success_url"]).query)
        self.assertEqual(success_query["ref"], [tx.reference])
        self.assertTrue(payload["success_url"].startswith(self.provider.get_base_url()))

        cancel_query = parse_qs(urlparse(payload["cancel_url"]).query)
        self.assertEqual(cancel_query["ref"], [tx.reference])
        self.assertTrue(
            self._generate_test_access_token(tx.reference) == cancel_query["access_token"][0]
        )

    def test_rendering_values_create_the_checkout(self):
        tx = self._create_transaction("redirect")
        checkout_url = f"https://app.recurrente.com/checkout-session/{self.checkout_id}"
        with patch(
            SEND_API_REQUEST, return_value={"id": self.checkout_id, "checkout_url": checkout_url}
        ) as request_mock:
            values = tx._get_specific_rendering_values({})

        self.assertEqual(request_mock.call_args.args[:2], ("POST", "/checkouts"))
        self.assertEqual(values["api_url"], checkout_url)
        self.assertEqual(tx.provider_reference, self.checkout_id)

    @mute_logger("odoo.addons.payment_recurrente_api.models.payment_transaction")
    def test_rendering_values_error_sets_the_transaction_in_error(self):
        tx = self._create_transaction("redirect")
        with patch(SEND_API_REQUEST, side_effect=ValidationError("rejected")):
            values = tx._get_specific_rendering_values({})

        self.assertEqual(values, {})
        self.assertEqual(tx.state, "error")

    def test_extract_reference_from_return_url(self):
        tx = self._create_transaction("redirect")
        reference = self.env["payment.transaction"]._extract_reference(
            const.PROVIDER_CODE, {"ref": tx.reference}
        )
        self.assertEqual(reference, tx.reference)

    def test_extract_reference_from_webhook_checkout(self):
        tx = self._create_transaction("redirect")
        self._update_transaction(tx, provider_reference=self.checkout_id)

        reference = self.env["payment.transaction"]._extract_reference(
            const.PROVIDER_CODE, {"event_type": "intent.succeeded", "checkout": self.checkout}
        )

        self.assertEqual(reference, tx.reference)

    def test_extract_reference_of_unknown_checkout(self):
        reference = self.env["payment.transaction"]._extract_reference(
            const.PROVIDER_CODE, {"checkout": {"id": "ch_unknown"}}
        )
        self.assertFalse(reference)

    def test_paid_checkout_confirms_the_transaction(self):
        tx = self._create_transaction("redirect")
        tx._apply_updates(self.checkout)
        self.assertEqual(tx.state, "done")

    def test_paid_checkout_confirms_a_canceled_transaction(self):
        tx = self._create_transaction("redirect")
        tx._set_canceled()
        tx._apply_updates(self.checkout)
        self.assertEqual(tx.state, "done")

    def test_payment_in_progress_checkout_sets_the_transaction_pending(self):
        tx = self._create_transaction("redirect")
        tx._apply_updates({"status": const.CHECKOUT_STATUS_IN_PROGRESS})
        self.assertEqual(tx.state, "pending")

    def test_expired_checkout_cancels_the_transaction(self):
        tx = self._create_transaction("redirect")
        tx._apply_updates({"status": const.CHECKOUT_STATUS_EXPIRED})
        self.assertEqual(tx.state, "cancel")

    def test_unpaid_checkout_leaves_the_transaction_untouched(self):
        tx = self._create_transaction("redirect")
        tx._apply_updates({"status": const.CHECKOUT_STATUS_UNPAID})
        self.assertEqual(tx.state, "draft")

    def test_unpaid_checkout_left_by_the_customer_cancels_the_transaction(self):
        tx = self._create_transaction("redirect")
        tx._apply_updates({"status": const.CHECKOUT_STATUS_UNPAID, const.RETURN_CANCEL_FLAG: True})
        self.assertEqual(tx.state, "cancel")

    @mute_logger("odoo.addons.payment_recurrente_api.models.payment_transaction")
    def test_unknown_checkout_status_sets_the_transaction_in_error(self):
        tx = self._create_transaction("redirect")
        tx._apply_updates({"status": "something_new"})
        self.assertEqual(tx.state, "error")

    def test_extract_amount_data(self):
        tx = self._create_transaction("redirect")
        self.assertEqual(
            tx._extract_amount_data(self.checkout),
            {"amount": 1111.11, "currency_code": "USD"},
        )

    def test_matching_amount_is_accepted(self):
        tx = self._create_transaction("redirect")
        tx._process(const.PROVIDER_CODE, self.checkout)
        self.assertEqual(tx.state, "done")

    def test_amount_mismatch_sets_the_transaction_in_error(self):
        tx = self._create_transaction("redirect")
        tx._process(const.PROVIDER_CODE, {**self.checkout, "total_in_cents": 100})
        self.assertEqual(tx.state, "error")

    def test_missing_amount_sets_the_transaction_in_error(self):
        tx = self._create_transaction("redirect")
        tx._process(
            const.PROVIDER_CODE,
            {"id": self.checkout_id, "status": const.CHECKOUT_STATUS_PAID},
        )
        self.assertEqual(tx.state, "error")

    def test_only_supported_currencies_are_available(self):
        currencies = self.provider._get_supported_currencies()
        self.assertEqual(set(currencies.mapped("name")) - set(const.SUPPORTED_CURRENCIES), set())

    def test_request_url_and_headers(self):
        self.assertEqual(
            self.provider._build_request_url("/checkouts"),
            "https://app.recurrente.com/api/checkouts",
        )
        headers = self.provider._build_request_headers("POST", "/checkouts", {})
        self.assertEqual(headers["X-SECRET-KEY"], "sk_test_dummy")

    def test_test_key_is_refused_when_enabled(self):
        with self.assertRaises(ValidationError):
            self.provider.write({"state": "enabled", "recurrente_api_secret_key": "sk_test_dummy"})

    def test_live_key_is_refused_in_test_mode(self):
        with self.assertRaises(ValidationError):
            self.provider.write({"state": "test", "recurrente_api_secret_key": "sk_live_dummy"})

    def test_live_key_is_accepted_when_enabled(self):
        self.provider.write({"state": "enabled", "recurrente_api_secret_key": "sk_live_dummy"})
        self.assertEqual(self.provider.state, "enabled")


@tagged("post_install", "-at_install")
class RecurrenteApiRefundTest(RecurrenteApiCommon):
    def setUp(self):
        super().setUp()
        self.source_tx = self._create_transaction("redirect")
        self._update_transaction(self.source_tx, provider_reference=self.checkout_id)
        self.source_tx._set_done()
        self.checkout_with_intent = {**self.checkout, "latest_intent": {"id": "in_8c3a1f20"}}
        self.refund = {
            "id": "re_3jfrywsf",
            "status": const.REFUND_STATUS_SUCCEEDED,
            "customer_refunded_amount_in_cents": 111111,
            "currency": "USD",
        }

    def _send_refund(self, amount, refund=None, checkout=None):
        refund_tx = self.source_tx._create_child_transaction(amount, is_refund=True)
        refund = refund or {
            **self.refund,
            "customer_refunded_amount_in_cents": round(amount * 100),
        }
        responses = [checkout or self.checkout_with_intent, refund]
        with patch(SEND_API_REQUEST, side_effect=responses) as request_mock:
            refund_tx.with_context(payment_safe_write=False)._send_refund_request()
        return refund_tx, request_mock

    def test_provider_supports_refunds(self):
        self.assertEqual(self.provider.support_refund, "partial")
        self.assertEqual(self.payment_method.support_refund, "partial")

    def test_full_refund_omits_the_amount(self):
        refund_tx, request_mock = self._send_refund(self.source_tx.amount)

        get_call, post_call = request_mock.call_args_list
        self.assertEqual(get_call.args[:2], ("GET", f"/checkouts/{self.checkout_id}"))
        self.assertEqual(post_call.args[:2], ("POST", "/refunds"))
        self.assertEqual(post_call.kwargs["json"], {"intent_id": "in_8c3a1f20"})
        self.assertTrue(post_call.kwargs["idempotency_key"])
        self.assertEqual(refund_tx.state, "done")

    def test_partial_refund_sends_the_amount(self):
        _refund_tx, request_mock = self._send_refund(100.0)

        payload = request_mock.call_args_list[1].kwargs["json"]
        self.assertEqual(payload, {"intent_id": "in_8c3a1f20", "amount_in_cents": 10000})

    def test_refund_of_the_remaining_balance_omits_the_amount(self):
        self._send_refund(100.0)

        _refund_tx, request_mock = self._send_refund(self.source_tx.amount - 100.0)

        self.assertEqual(
            request_mock.call_args_list[1].kwargs["json"], {"intent_id": "in_8c3a1f20"}
        )

    def test_refund_without_intent_is_refused(self):
        refund_tx = self.source_tx._create_child_transaction(100.0, is_refund=True)
        with (
            patch(SEND_API_REQUEST, return_value=self.checkout),
            self.assertRaises(ValidationError),
        ):
            refund_tx.with_context(payment_safe_write=False)._send_refund_request()

    def test_idempotency_key_is_sent_as_a_header(self):
        headers = self.provider._build_request_headers(
            "POST", "/refunds", {}, idempotency_key="abc123"
        )
        self.assertEqual(headers["Idempotency-Key"], "abc123")
        self.assertNotIn(
            "Idempotency-Key", self.provider._build_request_headers("POST", "/refunds", {})
        )

    def test_succeeded_refund_confirms_the_refund_transaction(self):
        refund_tx = self.source_tx._create_child_transaction(100.0, is_refund=True)
        refund_tx._apply_updates(self.refund)
        self.assertEqual(refund_tx.state, "done")
        self.assertEqual(refund_tx.provider_reference, "re_3jfrywsf")

    def test_pending_refund_sets_the_refund_transaction_pending(self):
        refund_tx = self.source_tx._create_child_transaction(100.0, is_refund=True)
        refund_tx._apply_updates({**self.refund, "status": const.REFUND_STATUS_PENDING})
        self.assertEqual(refund_tx.state, "pending")

    def test_failed_refund_sets_the_refund_transaction_in_error(self):
        refund_tx = self.source_tx._create_child_transaction(100.0, is_refund=True)
        refund_tx._apply_updates(
            {**self.refund, "status": const.REFUND_STATUS_FAILED, "failure_reason": "no balance"}
        )
        self.assertEqual(refund_tx.state, "error")
        self.assertIn("no balance", refund_tx.state_message)

    def test_voided_refund_cancels_the_refund_transaction(self):
        refund_tx = self.source_tx._create_child_transaction(100.0, is_refund=True)
        refund_tx._apply_updates({**self.refund, "status": const.REFUND_STATUS_VOIDED})
        self.assertEqual(refund_tx.state, "cancel")

    def test_refund_amount_data_uses_the_amount_returned_to_the_customer(self):
        refund_tx = self.source_tx._create_child_transaction(100.0, is_refund=True)
        self.assertEqual(
            refund_tx._extract_amount_data(
                {**self.refund, "customer_refunded_amount_in_cents": 10000}
            ),
            {"amount": 100.0, "currency_code": "USD"},
        )
        self.assertIsNone(refund_tx._extract_amount_data({"id": "re_3jfrywsf"}))

    def test_refund_amount_mismatch_sets_the_refund_transaction_in_error(self):
        refund_tx = self.source_tx._create_child_transaction(100.0, is_refund=True)
        refund_tx._process(
            const.PROVIDER_CODE, {**self.refund, "customer_refunded_amount_in_cents": 5000}
        )
        self.assertEqual(refund_tx.state, "error")

    def test_extract_reference_from_refund_webhook(self):
        refund_tx = self.source_tx._create_child_transaction(100.0, is_refund=True)
        self._update_transaction(refund_tx, provider_reference="re_3jfrywsf")

        reference = self.env["payment.transaction"]._extract_reference(
            const.PROVIDER_CODE,
            {"event_type": "refund.create", "refund": {"id": "re_3jfrywsf"}},
        )

        self.assertEqual(reference, refund_tx.reference)


@tagged("post_install", "-at_install")
class RecurrenteApiTokenTest(RecurrenteApiCommon):
    def setUp(self):
        super().setUp()
        self.token = self._create_token(provider_ref="pay_m_7v5ie3pw", payment_details="4242")
        self.payment = {"object": "one_time_payment", "id": "on_cagx1jfm", "status": "paid"}
        self.intent = {
            "type": "payment",
            "id": "in_z2zh85f7",
            "status": "succeeded",
            "raw_status": "succeeded",
            "amount_in_cents": 111111,
            "currency": "USD",
            "payment": {
                "id": "pa_eggta1c0",
                "paymentable": {"id": "on_cagx1jfm", "type": "OneTimePayment"},
            },
        }
        self.card = {"id": "pay_m_7v5ie3pw", "type": "card", "card": {"last4": "4242"}}

    def _charge_with_token(self, responses):
        tx = self._create_transaction("token", token_id=self.token.id)
        with patch(SEND_API_REQUEST, side_effect=responses) as request_mock:
            tx.with_context(payment_safe_write=False)._send_payment_request()
        return tx, request_mock

    def test_provider_and_method_support_tokenization(self):
        self.assertTrue(self.provider.support_tokenization)
        self.assertTrue(self.payment_method.support_tokenization)

    def test_validation_operations_are_not_offered(self):
        providers = self.env["payment.provider"]._get_compatible_providers(
            self.env.company.id, self.partner.id, 0.0, is_validation=True
        )
        self.assertNotIn(self.provider, providers)

    def test_token_values_of_a_card(self):
        tx = self._create_transaction("redirect", tokenize=True)
        values = tx._extract_token_values({"payment_method": self.card})
        self.assertEqual(values, {"provider_ref": "pay_m_7v5ie3pw", "payment_details": "4242"})

    @mute_logger("odoo.addons.payment_recurrente_api.models.payment_transaction")
    def test_no_token_values_for_something_else_than_a_card(self):
        tx = self._create_transaction("redirect", tokenize=True)
        self.assertEqual(tx._extract_token_values({}), {})
        self.assertEqual(
            tx._extract_token_values({"payment_method": {"id": "pay_m_1", "type": "balance"}}), {}
        )

    def test_paying_with_tokenization_saves_the_card(self):
        tx = self._create_transaction("redirect", tokenize=True)
        tx._process(const.PROVIDER_CODE, {**self.checkout, "payment_method": self.card})
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.token_id.provider_ref, "pay_m_7v5ie3pw")
        self.assertEqual(tx.token_id.payment_details, "4242")
        self.assertEqual(tx.token_id.partner_id, tx.partner_id)

    def test_charging_a_saved_card_sends_the_payment_method(self):
        tx, request_mock = self._charge_with_token([self.payment, [self.intent]])

        post_call, get_call = request_mock.call_args_list
        self.assertEqual(post_call.args[:2], ("POST", "/one_time_payments"))
        payload = post_call.kwargs["json"]
        self.assertEqual(payload["payment_method_id"], "pay_m_7v5ie3pw")
        self.assertEqual(payload["items"][0]["amount_in_cents"], 111111)
        self.assertEqual(payload["items"][0]["currency"], "USD")
        self.assertNotIn("charge_type", payload["items"][0])
        self.assertTrue(post_call.kwargs["idempotency_key"])
        self.assertEqual(get_call.args[:2], ("GET", "/intents"))
        self.assertEqual(set(get_call.kwargs["params"]), {"from_time", "until_time"})

    def test_charging_a_saved_card_processes_the_intent(self):
        tx, _request_mock = self._charge_with_token([self.payment, [self.intent]])
        self._assert_processed_with(tx, self.intent)

    def test_charging_a_saved_card_without_finding_its_intent(self):
        tx, _request_mock = self._charge_with_token([self.payment, []])
        self._assert_processed_with(tx, self.payment)

    @mute_logger("odoo.addons.payment_recurrente_api.models.payment_transaction")
    def test_charging_a_saved_card_when_the_intent_lookup_fails(self):
        tx, _request_mock = self._charge_with_token([self.payment, ValidationError("down")])
        self._assert_processed_with(tx, self.payment)

    def test_declined_saved_card_raises_the_error(self):
        tx = self._create_transaction("token", token_id=self.token.id)
        with (
            patch(SEND_API_REQUEST, side_effect=ValidationError("card declined")),
            self.assertRaises(ValidationError),
        ):
            tx.with_context(payment_safe_write=False)._send_payment_request()

    def test_intent_confirms_the_transaction(self):
        tx = self._create_transaction("token", token_id=self.token.id)
        tx._apply_updates(self.intent)
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.provider_reference, "in_z2zh85f7")

    def test_pending_intent(self):
        tx = self._create_transaction("token", token_id=self.token.id)
        tx._apply_updates({**self.intent, "status": const.INTENT_STATUS_PENDING})
        self.assertEqual(tx.state, "pending")

    def test_failed_intent(self):
        tx = self._create_transaction("token", token_id=self.token.id)
        tx._apply_updates(
            {
                **self.intent,
                "status": const.INTENT_STATUS_FAILED,
                "details": {"failure_reason": "insufficient funds"},
            }
        )
        self.assertEqual(tx.state, "error")
        self.assertIn("insufficient funds", tx.state_message)

    def test_canceled_intent(self):
        tx = self._create_transaction("token", token_id=self.token.id)
        tx._apply_updates({**self.intent, "status": const.INTENT_STATUS_CANCELED})
        self.assertEqual(tx.state, "cancel")

    @mute_logger("odoo.addons.payment_recurrente_api.models.payment_transaction")
    def test_unknown_intent_status(self):
        tx = self._create_transaction("token", token_id=self.token.id)
        tx._apply_updates({**self.intent, "status": "something_new"})
        self.assertEqual(tx.state, "error")

    def test_paid_one_time_payment_confirms_the_transaction(self):
        tx = self._create_transaction("token", token_id=self.token.id)
        tx._apply_updates(self.payment)
        self.assertEqual(tx.state, "done")

    @mute_logger("odoo.addons.payment_recurrente_api.models.payment_transaction")
    def test_unknown_one_time_payment_status(self):
        tx = self._create_transaction("token", token_id=self.token.id)
        tx._apply_updates({**self.payment, "status": "something_new"})
        self.assertEqual(tx.state, "error")

    def test_amount_data_of_an_intent_and_of_a_one_time_payment(self):
        tx = self._create_transaction("token", token_id=self.token.id)
        self.assertEqual(
            tx._extract_amount_data(self.intent), {"amount": 1111.11, "currency_code": "USD"}
        )
        self.assertIsNone(tx._extract_amount_data(self.payment))

    def test_intent_with_another_amount_sets_the_transaction_in_error(self):
        tx = self._create_transaction("token", token_id=self.token.id)
        tx._process(const.PROVIDER_CODE, {**self.intent, "amount_in_cents": 100})
        self.assertEqual(tx.state, "error")

    def test_extract_reference_of_a_saved_card_notification(self):
        tx = self._create_transaction("token", token_id=self.token.id)
        self._update_transaction(tx, provider_reference="in_z2zh85f7")
        Transaction = self.env["payment.transaction"]

        self.assertEqual(
            Transaction._extract_reference(
                const.PROVIDER_CODE, {"event_type": "intent.succeeded", "id": "in_z2zh85f7"}
            ),
            tx.reference,
        )
        self.assertFalse(
            Transaction._extract_reference(
                const.PROVIDER_CODE, {"event_type": "subscription.create", "id": "in_z2zh85f7"}
            )
        )

    def test_refund_of_a_saved_card_payment_uses_its_intent(self):
        source_tx = self._create_transaction("token", token_id=self.token.id)
        self._update_transaction(source_tx, provider_reference="in_z2zh85f7")
        source_tx._set_done()
        refund_tx = source_tx._create_child_transaction(source_tx.amount, is_refund=True)
        refund = {
            "id": "re_3jfrywsf",
            "status": const.REFUND_STATUS_SUCCEEDED,
            "customer_refunded_amount_in_cents": 111111,
            "currency": "USD",
        }

        with patch(SEND_API_REQUEST, side_effect=[refund]) as request_mock:
            refund_tx.with_context(payment_safe_write=False)._send_refund_request()

        request_mock.assert_called_once()
        self.assertEqual(request_mock.call_args.args[:2], ("POST", "/refunds"))
        self.assertEqual(request_mock.call_args.kwargs["json"], {"intent_id": "in_z2zh85f7"})

    def test_refund_of_a_payment_without_intent_is_refused(self):
        source_tx = self._create_transaction("token", token_id=self.token.id)
        self._update_transaction(source_tx, provider_reference="on_cagx1jfm")
        source_tx._set_done()
        refund_tx = source_tx._create_child_transaction(100.0, is_refund=True)
        with (
            patch(SEND_API_REQUEST) as request_mock,
            self.assertRaises(ValidationError),
        ):
            refund_tx.with_context(payment_safe_write=False)._send_refund_request()
        request_mock.assert_not_called()


@tagged("post_install", "-at_install")
class RecurrenteApiSignatureTest(RecurrenteApiCommon):
    def setUp(self):
        super().setUp()
        self.body = b'{"event_type": "intent.succeeded"}'

    def _verify(self, headers, body=None, secret=WEBHOOK_SECRET, now=None):
        return utils.verify_webhook_signature(
            secret,
            headers.get("svix-id"),
            headers.get("svix-timestamp"),
            headers.get("svix-signature"),
            self.body if body is None else body,
            now=now,
        )

    def test_valid_signature(self):
        self.assertTrue(self._verify(sign_webhook(self.body)))

    def test_tampered_body(self):
        self.assertFalse(self._verify(sign_webhook(self.body), body=b'{"event_type": "x"}'))

    def test_wrong_secret(self):
        other = "whsec_" + "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="
        self.assertFalse(self._verify(sign_webhook(self.body, secret=other)))

    def test_stale_timestamp(self):
        old = int(time.time()) - const.WEBHOOK_TOLERANCE_SECONDS - 10
        self.assertFalse(self._verify(sign_webhook(self.body, timestamp=old)))

    def test_one_valid_signature_among_several(self):
        headers = sign_webhook(self.body)
        headers["svix-signature"] = f"v1,AAAA {headers['svix-signature']} v2,BBBB"
        self.assertTrue(self._verify(headers))

    def test_missing_headers(self):
        self.assertFalse(self._verify({}))
        self.assertFalse(self._verify(sign_webhook(self.body), secret=""))


@tagged("post_install", "-at_install")
class RecurrenteApiControllerTest(RecurrenteApiCommon, PaymentHttpCommon):
    def _post_webhook(self, payload, headers=None):
        body = json.dumps(payload).encode()
        headers = sign_webhook(body) if headers is None else headers
        return self.url_open(
            self._build_url(RecurrenteApiController._webhook_url),
            data=body,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )

    def _webhook_payload(self, event_type="intent.succeeded"):
        return {"event_type": event_type, "checkout": {"id": self.checkout_id}}

    @mute_logger("odoo.addons.payment_recurrente_api.controllers.main")
    def test_webhook_confirms_the_transaction(self):
        tx = self._create_transaction("redirect")
        self._update_transaction(tx, provider_reference=self.checkout_id)

        with patch(SEND_API_REQUEST, return_value=self.checkout) as request_mock:
            response = self._post_webhook(self._webhook_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request_mock.call_args.args[:2], ("GET", f"/checkouts/{self.checkout_id}"))
        self.assertEqual(tx.state, "done")

    @mute_logger("odoo.addons.payment_recurrente_api.controllers.main")
    def test_webhook_with_invalid_signature_is_refused(self):
        tx = self._create_transaction("redirect")
        self._update_transaction(tx, provider_reference=self.checkout_id)

        with patch(SEND_API_REQUEST, return_value=self.checkout) as request_mock:
            response = self._post_webhook(
                self._webhook_payload(), headers=sign_webhook(b"other body")
            )

        self.assertEqual(response.status_code, 403)
        request_mock.assert_not_called()
        self.assertEqual(tx.state, "draft")

    @mute_logger("odoo.addons.payment_recurrente_api.controllers.main")
    def test_webhook_ignores_legacy_events(self):
        tx = self._create_transaction("redirect")
        self._update_transaction(tx, provider_reference=self.checkout_id)

        with patch(SEND_API_REQUEST, return_value=self.checkout) as request_mock:
            response = self._post_webhook(self._webhook_payload("payment_intent.succeeded"))

        self.assertEqual(response.status_code, 200)
        request_mock.assert_not_called()

    @mute_logger("odoo.addons.payment_recurrente_api.controllers.main")
    def test_webhook_asks_for_a_retry_when_the_api_is_unreachable(self):
        tx = self._create_transaction("redirect")
        self._update_transaction(tx, provider_reference=self.checkout_id)

        with patch(SEND_API_REQUEST, side_effect=ValidationError("unreachable")):
            response = self._post_webhook(self._webhook_payload())

        self.assertEqual(response.status_code, 503)

    @mute_logger("odoo.addons.payment_recurrente_api.controllers.main")
    def test_webhook_with_invalid_body(self):
        response = self.url_open(
            self._build_url(RecurrenteApiController._webhook_url),
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        self.assertEqual(response.status_code, 400)

    @mute_logger("odoo.addons.payment_recurrente_api.controllers.main")
    def test_return_confirms_the_transaction(self):
        tx = self._create_transaction("redirect")
        self._update_transaction(tx, provider_reference=self.checkout_id)

        with patch(SEND_API_REQUEST, return_value=self.checkout):
            self._make_http_get_request(
                self._build_url(RecurrenteApiController._return_url), params={"ref": tx.reference}
            )

        self.assertEqual(tx.state, "done")

    @mute_logger("odoo.addons.payment_recurrente_api.controllers.main")
    def test_cancel_with_valid_token_cancels_the_transaction(self):
        tx = self._create_transaction("redirect")
        self._update_transaction(tx, provider_reference=self.checkout_id)
        params = {
            "ref": tx.reference,
            "access_token": self._generate_test_access_token(tx.reference),
        }

        with patch(SEND_API_REQUEST, return_value={**self.checkout, "status": "unpaid"}):
            self._make_http_get_request(
                self._build_url(RecurrenteApiController._cancel_url), params=params
            )

        self.assertEqual(tx.state, "cancel")

    @mute_logger("odoo.addons.payment_recurrente_api.controllers.main")
    def test_cancel_with_invalid_token_is_refused(self):
        tx = self._create_transaction("redirect")
        self._update_transaction(tx, provider_reference=self.checkout_id)

        response = self._make_http_get_request(
            self._build_url(RecurrenteApiController._cancel_url),
            params={"ref": tx.reference, "access_token": "forged"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(tx.state, "draft")

    @mute_logger("odoo.addons.payment_recurrente_api.controllers.main")
    def test_refund_webhook_confirms_the_refund_transaction(self):
        source_tx = self._create_transaction("redirect")
        self._update_transaction(source_tx, provider_reference=self.checkout_id)
        source_tx._set_done()
        refund_tx = source_tx._create_child_transaction(100.0, is_refund=True)
        self._update_transaction(refund_tx, provider_reference="re_3jfrywsf")
        refund = {
            "id": "re_3jfrywsf",
            "status": const.REFUND_STATUS_SUCCEEDED,
            "customer_refunded_amount_in_cents": 10000,
            "currency": "USD",
        }

        with patch(SEND_API_REQUEST, return_value=refund) as request_mock:
            response = self._post_webhook(
                {"event_type": "refund.create", "refund": {"id": "re_3jfrywsf"}}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request_mock.call_args.args[:2], ("GET", "/refunds/re_3jfrywsf"))
        self.assertEqual(refund_tx.state, "done")

    @mute_logger("odoo.addons.payment_recurrente_api.controllers.main")
    def test_webhook_confirms_a_saved_card_payment(self):
        token = self._create_token(provider_ref="pay_m_7v5ie3pw")
        tx = self._create_transaction("token", token_id=token.id)
        self._update_transaction(tx, provider_reference="in_z2zh85f7")
        intent = {
            "type": "payment",
            "id": "in_z2zh85f7",
            "status": "succeeded",
            "raw_status": "succeeded",
            "amount_in_cents": 111111,
            "currency": "USD",
        }

        with patch(SEND_API_REQUEST, return_value=intent) as request_mock:
            response = self._post_webhook({"event_type": "intent.succeeded", "id": "in_z2zh85f7"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request_mock.call_args.args[:2], ("GET", "/intents/in_z2zh85f7"))
        self.assertEqual(tx.state, "done")
