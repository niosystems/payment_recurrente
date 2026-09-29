import base64
import binascii
import hashlib
import hmac
import time

from odoo.addons.payment_recurrente_api import const


def verify_webhook_signature(secret, svix_id, svix_timestamp, svix_signature, body, now=None):
    """Check the Svix signature of a Recurrente webhook delivery.

    Recurrente delivers webhooks through Svix. The signed content is
    `{svix-id}.{svix-timestamp}.{raw body}`, signed with HMAC-SHA256 using the base64-decoded
    part of the `whsec_...` endpoint secret. The `svix-signature` header holds one or more
    space-separated `v1,<base64 signature>` entries.

    :param str secret: The signing secret of the webhook endpoint (`whsec_...`).
    :param str svix_id: The value of the `svix-id` header.
    :param str svix_timestamp: The value of the `svix-timestamp` header (Unix seconds).
    :param str svix_signature: The value of the `svix-signature` header.
    :param bytes body: The raw request body, exactly as received.
    :param float now: The current Unix time; defaults to the system clock.
    :return: Whether the delivery is authentic and recent.
    :rtype: bool
    """
    if not (secret and svix_id and svix_timestamp and svix_signature):
        return False

    try:
        timestamp = int(svix_timestamp)
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except (ValueError, binascii.Error):
        return False

    if abs((time.time() if now is None else now) - timestamp) > const.WEBHOOK_TOLERANCE_SECONDS:
        return False

    signed_content = b".".join((svix_id.encode(), svix_timestamp.encode(), body))
    expected = base64.b64encode(hmac.new(key, signed_content, hashlib.sha256).digest()).decode()

    for entry in svix_signature.split():
        version, _sep, signature = entry.partition(",")
        if version == "v1" and hmac.compare_digest(signature, expected):
            return True
    return False
