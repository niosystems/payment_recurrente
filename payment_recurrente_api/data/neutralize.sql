-- disable recurrente payment provider
UPDATE payment_provider
   SET recurrente_api_secret_key = NULL,
       recurrente_api_webhook_secret = NULL;
