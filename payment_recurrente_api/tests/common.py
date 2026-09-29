import base64
import hashlib
import hmac
import time

from odoo.addons.payment.tests.common import PaymentCommon
from odoo.addons.payment_recurrente_api import const

WEBHOOK_SECRET = "whsec_" + base64.b64encode(b"unit-test-signing-key-32-bytes!!").decode()


def sign_webhook(body, secret=WEBHOOK_SECRET, svix_id="msg_test", timestamp=None):
    """Return the Svix headers of a delivery of `body`, signed with `secret`."""
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed_content = b".".join((svix_id.encode(), timestamp.encode(), body))
    signature = base64.b64encode(hmac.new(key, signed_content, hashlib.sha256).digest()).decode()
    return {
        "svix-id": svix_id,
        "svix-timestamp": timestamp,
        "svix-signature": f"v1,{signature}",
    }


class RecurrenteApiCommon(PaymentCommon):
    def _create_transaction(self, flow, sudo=True, **values):
        """Create a transaction that can be updated like it is while its data is processed.

        Requests that Odoo makes outside of the processing, like charging a saved card or
        refunding, are tested with `with_context(payment_safe_write=False)` to make sure they
        never write on the transaction directly.
        """
        tx = super()._create_transaction(flow, sudo=sudo, **values)
        return tx.with_context(payment_safe_write=True)

    def _assert_processed_with(self, transaction, payment_data):
        self.assertEqual([data.payload for data in transaction.payment_data_ids], [payment_data])

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.recurrente = cls._prepare_provider(
            const.PROVIDER_CODE,
            update_values={
                "recurrente_api_secret_key": "sk_test_dummy",
                "recurrente_api_webhook_secret": WEBHOOK_SECRET,
            },
        )
        cls.provider = cls.recurrente
        cls.payment_methods = cls.provider.payment_method_ids
        cls.payment_method = cls.payment_methods[:1]
        cls.payment_method_id = cls.payment_method.id
        cls.payment_method_code = cls.payment_method.code
        cls.currency = cls.currency_usd

        cls.checkout_id = "ch_eegw9j5zgqoae3ms"
        cls.checkout = {
            "id": cls.checkout_id,
            "status": const.CHECKOUT_STATUS_PAID,
            "total_in_cents": 111111,
            "currency": "USD",
        }
