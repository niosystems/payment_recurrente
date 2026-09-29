# Recurrente para Odoo 20

![Odoo 20.0](https://img.shields.io/badge/Odoo-20.0-714B67) ![License: LGPL--3](https://img.shields.io/badge/license-LGPL--3-blue)

> Versión objetivo: **Odoo 20.0**. Ver [Versiones soportadas](#versiones-soportadas) para las demás ramas.

Proveedor de pago para Odoo 20 que cobra con el checkout hospedado de [Recurrente](https://docs.recurrente.com) (Guatemala, GTQ y USD). Se comunica directamente con `https://app.recurrente.com/api`, sin servidores intermedios.

Módulo: [`payment_recurrente_api`](payment_recurrente_api). Escrito sobre el framework estándar de `payment`, con el mismo esquema que los proveedores incluidos en Odoo (`payment_mollie`, `payment_stripe`, ...).

## Índice

- [Versiones soportadas](#versiones-soportadas)
- [Cómo funciona](#cómo-funciona)
- [Configuración](#configuración)
- [Alcance actual](#alcance-actual)
- [Tarjetas guardadas (tokenización)](#tarjetas-guardadas-tokenización)
- [Reembolsos](#reembolsos)
- [Diferencias entre las ramas 19.0 y 20.0](#diferencias-entre-las-ramas-190-y-200)
- [Pruebas](#pruebas)
- [Publicación](#publicación)
- [Documentación de referencia](#documentación-de-referencia)
- [Licencia](#licencia)

## Versiones soportadas

Un módulo independiente por versión de Odoo, una rama por versión, publicado por separado en la Odoo Apps Store. La lógica de negocio es la misma en todas; solo cambia lo que exige el framework de `payment` de cada versión (ver [Diferencias entre las ramas 19.0 y 20.0](#diferencias-entre-las-ramas-190-y-200) más abajo, o el equivalente en cada rama que se agregue).

| Rama | Odoo | Estado |
|---|---|---|
| `17.0` | 17.0 | ⏳ Planeada — API antigua de `payment` (`_get_tx_from_notification_data` / `_process_notification_data`) |
| `18.0` | 18.0 | ⏳ Planeada |
| `19.0` | 19.0 | ✅ Publicada |
| `20.0` (esta rama) | 20.0 | ✅ Publicada |
| `master` | próxima versión (21.0) | ⏳ Se abre cuando Odoo publique esa versión |

Cada rama nueva se porta desde la más cercana ya publicada (por ejemplo, 18.0 nacerá de 19.0), así que su README solo documenta la diferencia con esa vecina, no con todas las anteriores.

## Cómo funciona

1. Al pagar, Odoo crea un checkout (`POST /checkouts`) y guarda su id en `provider_reference`.
2. El cliente paga en la página de Recurrente y regresa a `/payment/recurrente_api/return` (o `/cancel`).
3. Odoo **nunca deduce el estado del pago de la petición recibida**: consulta `GET /checkouts/{id}` con la llave secreta y registra el resultado con `_record()`. El cron estándar de `payment` lo procesa (`_apply_updates` y validación de monto/moneda).
4. Los webhooks (`intent.*` y `refund.create`) solo sirven para identificar el checkout y disparar esa misma consulta. Se verifican con la firma Svix (`whsec_...`).

| Estado del checkout en Recurrente | Estado de la transacción |
|---|---|
| `paid` | `done` (también si estaba `cancel`: pago tardío) |
| `payment_in_progress` | `pending` |
| `expired` | `cancel` |
| `unpaid` | sin cambio (o `cancel` si el cliente volvió por la URL de cancelación) |

## Configuración

1. Instalar el módulo y abrir *Facturación > Configuración > Proveedores de pago > Recurrente*.
2. Pegar la **llave secreta** (`sk_test_...` o `sk_live_...`). La llave decide el ambiente en Recurrente; el módulo rechaza combinaciones incoherentes (llave de prueba con `is_live` activo y viceversa).
3. Registrar un webhook en Recurrente apuntando a:

   ```
   https://<tu-dominio>/payment/recurrente_api/webhook
   ```

   Suscribirlo a los eventos **`intent.*`** y **`refund.create`**. Los eventos legacy (`payment_intent.*`, ...) se ignoran para no procesar un pago dos veces.
4. Pegar el `signingSecret` (`whsec_...`) que devuelve Recurrente en *Webhook Signing Secret*. Sin él, los webhooks se aceptan sin verificar firma (sigue siendo seguro porque el estado siempre se consulta a la API, pero se recomienda configurarlo).
5. Marcar `is_live` según el tipo de llave (desactivado con `sk_test_...`, activado con `sk_live_...`) y publicar el proveedor. Con una llave de prueba equivale al *Modo de prueba* de la rama 19.0.

Para probar sin dinero real usa un [Sandbox de Recurrente](https://docs.recurrente.com/guides-english/getting-started/introduction) (tarjeta `4242 4242 4242 4242`).

## Alcance actual

Incluido: pago único con redirección, tarjetas guardadas y cobros con token, reembolsos desde Odoo, GTQ/USD, webhooks firmados, validación de monto y moneda, neutralización de credenciales en bases duplicadas.

No incluido (posibles siguientes pasos): agregar una tarjeta sin pagar (`mode: "setup"`), suscripciones propias de Recurrente, prellenado de datos del cliente, referencia de transferencia bancaria (`bank_transfer_memo`).

## Tarjetas guardadas (tokenización)

- **Se guardan al pagar.** Si el cliente marca "guardar mi método de pago", Odoo toma el `payment_method` del checkout pagado (`pay_m_...`) y crea el token de la tarjeta, sin un cobro ni un checkout extra.
- **Cobros con token** (suscripciones de Odoo, pago con un clic, cobros automáticos): llaman a `POST /one_time_payments` con ese `payment_method_id` y un `Idempotency-Key`. Si el banco rechaza, la API responde con error y la transacción queda en *Error* con el mensaje.
- **El cobro solo devuelve `id` y `status`.** Para obtener el monto, el estado normalizado y un identificador que usen los reembolsos y los webhooks, el módulo busca el intent del cobro en `GET /intents` (ventana de ±10 minutos). Si no lo encuentra, procesa la respuesta simple: el pago queda confirmado, pero **no se puede reembolsar desde Odoo** y hay que hacerlo desde el panel de Recurrente.
- **Requisitos:** los cobros únicos con método guardado deben estar habilitados en tu cuenta de Recurrente (si no, la API responde 403), y en el proveedor hay que activar *Permitir guardar métodos de pago*, que viene desactivado.
- **Agregar una tarjeta sin pagar** (desde *Mis métodos de pago*) no está disponible: Recurrente se excluye de ese flujo.

## Reembolsos

El botón *Reembolsar* de la transacción crea una transacción hija y llama a `POST /refunds` con el `intent_id` del pago (se obtiene del checkout). El monto solo se envía en reembolsos parciales; si cubre todo el saldo pendiente se omite, porque algunos procesadores solo aceptan el reembolso total. Cada solicitud lleva un `Idempotency-Key`. El resultado se confirma consultando `GET /refunds/{id}`, ya sea con la respuesta o con el webhook `refund.create`.

Límites de Recurrente que Odoo no puede saltarse (el error de la API se muestra al usuario):

- Solo pagos con **tarjeta** o **Balance Recurrente**; las transferencias bancarias y los pagos en **cuotas** no se pueden reembolsar.
- Dentro de los **30 días** siguientes al pago.
- El comercio necesita **saldo suficiente** en Recurrente.
- El cliente recibe el 100 %; la comisión de Recurrente **no** se devuelve, y si la retención de IVA del mes ya se emitió el costo para el comercio es ligeramente mayor.
- Los reembolsos parciales dependen del procesador (CyberSource / Visa CyberSource); en otros, solo se acepta el saldo completo.

## Diferencias entre las ramas 19.0 y 20.0

La lógica de negocio (checkout, estados, reembolsos, firma Svix) es la misma. Cambia solo el framework de `payment` de Odoo:

| Tema | 19.0 | 20.0 (esta rama) |
|---|---|---|
| Modo del proveedor | `state`: `disabled` / `test` / `enabled` | `is_live` + `is_published` |
| Procesar datos del pago | `tx._process(code, data)`, síncrono | `tx._record(data)` → `payment.data` + cron → `tx._process(data)` |
| Validación de monto | Antes de `_apply_updates`, con cualquier estado | Después, solo si el estado es `authorized`/`done` |
| Métodos de pago | Registros globales enlazados con `payment_method_ids` del proveedor | Cada método pertenece a un proveedor (`provider_id`) |
| Formulario de redirección | Plantilla propia | `payment.generic_redirect_form` |
| Imágenes en XML | `type="base64"` | `type="bytes"` |

## Pruebas

Las pruebas viven en `payment_recurrente_api/tests` y requieren una base de datos de Odoo:

```bash
./odoo-bin -d test_recurrente --addons-path=addons,<ruta-a-este-repo> -i payment_recurrente_api --test-tags /payment_recurrente_api --stop-after-init
```

## Publicación

El módulo se publica de forma gratuita e independiente en la Odoo Apps Store, una versión por rama. El checklist frente a las guías de la tienda, las imágenes pendientes y el procedimiento de empaquetado están en [`APP_STORE.md`](APP_STORE.md).

## Documentación de referencia

Documentación oficial de la API: [docs.recurrente.com](https://docs.recurrente.com). Este repositorio mantiene además una copia local en `docs/recurrente/` para uso interno del equipo; no se versiona en git ni se incluye en el ZIP publicado, por derechos de autor sobre el contenido original.

## Licencia

[LGPL-3](LICENSE). Desarrollado por NioSystems. Recurrente es una marca de su propietario; este módulo no es un producto oficial de Recurrente.
