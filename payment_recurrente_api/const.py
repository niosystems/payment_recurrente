PROVIDER_CODE = "recurrente_api"

API_BASE_URL = "https://app.recurrente.com/api/"

SUPPORTED_CURRENCIES = ("GTQ", "USD")

DEFAULT_PAYMENT_METHOD_CODES = {"recurrente"}

# Checkout `status` values returned by `GET /checkouts/{id}`.
CHECKOUT_STATUS_PAID = "paid"
CHECKOUT_STATUS_IN_PROGRESS = "payment_in_progress"
CHECKOUT_STATUS_EXPIRED = "expired"
CHECKOUT_STATUS_UNPAID = "unpaid"

# Identifier prefixes: unified intents (`in_`) and one-time payments (`on_`) charged with a token.
INTENT_ID_PREFIX = "in_"
ONE_TIME_PAYMENT_ID_PREFIX = "on_"

# Unified intent `status` values, as returned by `GET /intents/{id}`.
INTENT_STATUS_SUCCEEDED = "succeeded"
INTENT_STATUS_PENDING = "pending"
INTENT_STATUS_FAILED = "failed"
INTENT_STATUS_CANCELED = "canceled"

# `status` returned by `POST /one_time_payments` once the saved card has been charged.
ONE_TIME_PAYMENT_STATUS_PAID = "paid"

# The one-time payment response does not carry its intent. It is looked up in the intents created
# within this many minutes around the charge.
INTENT_LOOKUP_MINUTES = 10

# Refund `status` values returned by `POST /refunds` and `GET /refunds/{id}`.
REFUND_STATUS_PENDING = "pending"
REFUND_STATUS_SUCCEEDED = "succeeded"
REFUND_STATUS_FAILED = "failed"
REFUND_STATUS_VOIDED = "voided"

# Only the unified `intent.*` payment events and `refund.create` are handled. The legacy per-type
# events (`payment_intent.*`, ...) are ignored so that a payment is never processed twice.
WEBHOOK_EVENT_PREFIX = "intent."
WEBHOOK_REFUND_EVENT = "refund.create"

# Maximum accepted gap, in seconds, between a webhook's timestamp and the server's clock.
WEBHOOK_TOLERANCE_SECONDS = 5 * 60

# Key added to the payment data when the customer comes back through the cancel URL.
RETURN_CANCEL_FLAG = "odoo_customer_canceled"
