from odoo import api, fields, models, release
from odoo.exceptions import ValidationError
from odoo.tools import urls

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_recurrente_api import const

_logger = get_payment_logger(__name__)


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[(const.PROVIDER_CODE, "Recurrente")],
        ondelete={const.PROVIDER_CODE: "set default"},
    )
    recurrente_api_secret_key = fields.Char(
        string="Secret Key",
        help="The secret API key (`sk_test_...` or `sk_live_...`) from Recurrente's Settings > API"
        " Keys. The key, not the Odoo state, decides whether Recurrente processes real payments.",
        required_if_provider=const.PROVIDER_CODE,
        copy=False,
        groups="base.group_system",
    )
    recurrente_api_webhook_secret = fields.Char(
        string="Webhook Signing Secret",
        help="The signing secret (`whsec_...`) of the webhook endpoint registered in Recurrente."
        " It is used to verify that notifications really come from Recurrente.",
        copy=False,
        groups="base.group_system",
    )

    # === CONSTRAINT METHODS === #

    @api.constrains("state", "recurrente_api_secret_key")
    def _check_recurrente_api_key_matches_state(self):
        """Prevent test keys from being used when enabled and live keys in test mode."""
        for provider in self.filtered(lambda p: p.code == const.PROVIDER_CODE):
            key = provider.recurrente_api_secret_key or ""
            if provider.state == "enabled" and key.startswith("sk_test_"):
                raise ValidationError(
                    self.env._(
                        "A Recurrente test key cannot be used while the provider is enabled."
                    )
                )
            if provider.state == "test" and key.startswith("sk_live_"):
                raise ValidationError(
                    self.env._(
                        "A Recurrente live key cannot be used while the provider is in test mode:"
                        " real payments would be processed."
                    )
                )

    # === COMPUTE METHODS === #

    def _compute_feature_support_fields(self):
        """Override of `payment` to enable additional features."""
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == const.PROVIDER_CODE).update(
            {
                "support_refund": "partial",
                "support_tokenization": True,
            }
        )

    @api.model
    def _get_compatible_providers(self, *args, is_validation=False, **kwargs):
        """Override of `payment` to filter out Recurrente for validation operations.

        Saving a card without paying (a validation) would need a `setup` checkout and the matching
        Recurrente customer, which is not supported yet. Cards are saved while paying instead.
        """
        providers = super()._get_compatible_providers(*args, is_validation=is_validation, **kwargs)
        if is_validation:
            providers = providers.filtered(lambda p: p.code != const.PROVIDER_CODE)
        return providers

    def _get_supported_currencies(self):
        """Override of `payment` to return the supported currencies."""
        supported_currencies = super()._get_supported_currencies()
        if self.code == const.PROVIDER_CODE:
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name in const.SUPPORTED_CURRENCIES
            )
        return supported_currencies

    # === CRUD METHODS === #

    def _get_default_payment_method_codes(self):
        """Override of `payment` to return the default payment method codes."""
        self.ensure_one()

        if self.code != const.PROVIDER_CODE:
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    # === REQUEST HELPERS === #

    def _build_request_url(self, endpoint, **kwargs):
        """Override of `payment` to build the request URL."""
        if self.code != const.PROVIDER_CODE:
            return super()._build_request_url(endpoint, **kwargs)
        return urls.urljoin(const.API_BASE_URL, endpoint.strip("/"))

    def _build_request_headers(self, method, endpoint, payload, **kwargs):
        """Override of `payment` to build the request headers.

        :param str idempotency_key: The optional key that makes the request safe to replay.
        """
        if self.code != const.PROVIDER_CODE:
            return super()._build_request_headers(method, endpoint, payload, **kwargs)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"Odoo/{release.version} PaymentRecurrenteApi",
            "X-SECRET-KEY": self.recurrente_api_secret_key or "",
        }
        if idempotency_key := kwargs.get("idempotency_key"):
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _parse_response_error(self, response):
        """Override of `payment` to parse the error message.

        Recurrente errors look like `{"message": "...", "errors": {"field": ["detail"]}}`.
        """
        if self.code != const.PROVIDER_CODE:
            return super()._parse_response_error(response)

        error = response.json()
        message = error.get("message") or response.reason
        details = error.get("errors")
        if isinstance(details, dict) and details:
            message += " " + "; ".join(
                f"{field}: {', '.join(value) if isinstance(value, list) else value}"
                for field, value in details.items()
            )
        return message
