import json

from werkzeug.exceptions import BadRequest, Forbidden, ServiceUnavailable

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_recurrente_api import const, utils

_logger = get_payment_logger(__name__)


class RecurrenteApiController(http.Controller):
    _return_url = "/payment/recurrente_api/return"
    _cancel_url = "/payment/recurrente_api/cancel"
    _webhook_url = "/payment/recurrente_api/webhook"

    @http.route(
        _return_url, type="http", auth="public", methods=["GET"], csrf=False, save_session=False
    )
    def recurrente_api_return(self, **data):
        """Refresh the transaction after the customer comes back from the Recurrente checkout.

        The route is flagged with `save_session=False` to prevent Odoo from assigning a new session
        to the user on the redirection from Recurrente: some browsers do not send the session cookie
        along with cross-site redirections. The redirection to '/payment/status' afterwards
        retrieves the user's session, and with it the transaction to monitor.

        The state of the payment is never deduced from the request: it is fetched from the API.

        :param dict data: The transaction reference (`ref`) embedded in the return URL.
        """
        _logger.info("Handling redirection from Recurrente for %r.", data.get("ref"))
        tx_sudo = (
            request.env["payment.transaction"]
            .sudo()
            ._search_by_reference(const.PROVIDER_CODE, data)
        )
        if tx_sudo:
            self._fetch_and_record(tx_sudo)
        return request.redirect("/payment/status")

    @http.route(
        _cancel_url, type="http", auth="public", methods=["GET"], csrf=False, save_session=False
    )
    def recurrente_api_cancel(self, access_token=None, **data):
        """Flag the transaction as canceled when the customer leaves the Recurrente checkout.

        :param str access_token: The token proving the request comes from the generated cancel URL.
        :param dict data: The transaction reference (`ref`) embedded in the cancel URL.
        :raise Forbidden: If the access token is invalid.
        """
        _logger.info("Handling cancellation from Recurrente for %r.", data.get("ref"))
        tx_sudo = (
            request.env["payment.transaction"]
            .sudo()
            ._search_by_reference(const.PROVIDER_CODE, data)
        )
        if tx_sudo:
            if not payment_utils.check_access_token(access_token, tx_sudo.reference):
                _logger.warning("Received a cancellation with an invalid access token.")
                raise Forbidden
            self._fetch_and_record(tx_sudo, extra_data={const.RETURN_CANCEL_FLAG: True})
        return request.redirect("/payment/status")

    @http.route(_webhook_url, type="http", auth="public", methods=["POST"], csrf=False)
    def recurrente_api_webhook(self):
        """Refresh the transaction when Recurrente notifies a payment event.

        Recurrente delivers webhooks through Svix. The signature is verified with the signing secret
        of the provider that owns the transaction. Just like for redirections, the notification is
        only used to identify the checkout or refund; its state is fetched from the API.

        :return: An empty string to acknowledge the notification.
        :rtype: str
        :raise BadRequest: If the body is not a valid JSON object.
        :raise Forbidden: If the signature is missing or invalid.
        :raise ServiceUnavailable: If the checkout could not be fetched, so Recurrente retries.
        """
        body = request.httprequest.get_data()
        try:
            data = json.loads(body)
        except ValueError:
            raise BadRequest from None
        if not isinstance(data, dict):
            raise BadRequest
        # Only identifiers are logged: the notification carries customer data (name, email, phone).
        _logger.info(
            "Notification received from Recurrente: event %r, id %r, checkout %r, refund %r.",
            data.get("event_type"),
            data.get("id"),
            (data.get("checkout") or {}).get("id"),
            (data.get("refund") or {}).get("id"),
        )

        event_type = str(data.get("event_type", ""))
        if not (
            event_type.startswith(const.WEBHOOK_EVENT_PREFIX)
            or event_type == const.WEBHOOK_REFUND_EVENT
        ):
            return ""  # Not a payment or refund event (or a legacy one); nothing to do.

        tx_sudo = (
            request.env["payment.transaction"]
            .sudo()
            ._search_by_reference(const.PROVIDER_CODE, data)
        )
        if not tx_sudo:
            return ""  # Unknown checkout, e.g. created outside of Odoo.

        self._verify_webhook_signature(tx_sudo.provider_id, body)
        if not self._fetch_and_record(tx_sudo):
            raise ServiceUnavailable
        return ""

    @staticmethod
    def _verify_webhook_signature(provider_sudo, body):
        """Verify the Svix signature of the current request.

        Without a configured signing secret, the verification is skipped. This remains safe as the
        notification never carries the payment state, but configuring the secret is recommended.

        :param payment.provider provider_sudo: The provider that owns the transaction.
        :param bytes body: The raw body of the request.
        :raise Forbidden: If the signature is invalid.
        """
        secret = provider_sudo.recurrente_api_webhook_secret
        if not secret:
            _logger.warning(
                "No webhook signing secret configured on provider %s; skipping verification.",
                provider_sudo.id,
            )
            return
        headers = request.httprequest.headers
        if not utils.verify_webhook_signature(
            secret,
            headers.get("svix-id"),
            headers.get("svix-timestamp"),
            headers.get("svix-signature"),
            body,
        ):
            _logger.warning("Received a notification with an invalid signature.")
            raise Forbidden

    @staticmethod
    def _fetch_and_record(tx_sudo, extra_data=None):
        """Fetch the checkout (or refund) of the transaction and record it for processing.

        :param payment.transaction tx_sudo: The transaction whose checkout or refund to fetch.
        :param dict extra_data: Data to add to the fetched object before recording it.
        :return: Whether the object could be fetched and recorded.
        :rtype: bool
        """
        if not tx_sudo.provider_reference:
            return False
        if tx_sudo.operation == "refund":
            resource = "refunds"
        elif tx_sudo.provider_reference.startswith(const.INTENT_ID_PREFIX):
            resource = "intents"  # A payment made with a saved card has no checkout.
        else:
            resource = "checkouts"
        try:
            payment_data = tx_sudo._send_api_request(
                "GET", f"/{resource}/{tx_sudo.provider_reference}"
            )
        except ValidationError:
            _logger.error("Unable to fetch the data of transaction %s.", tx_sudo.reference)
            return False
        tx_sudo._record({**payment_data, **(extra_data or {})})
        return True
