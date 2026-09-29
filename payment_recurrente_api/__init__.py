from . import controllers, models
from odoo.addons.payment import reset_payment_provider, setup_provider

from odoo.addons.payment_recurrente_api import const


def post_init_hook(env):
    setup_provider(env, const.PROVIDER_CODE)


def uninstall_hook(env):
    reset_payment_provider(env, const.PROVIDER_CODE)
