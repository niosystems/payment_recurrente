# Publicación en Odoo Apps (Odoo 20.0)

Checklist de las [guías para vendedores de Odoo Apps](https://apps.odoo.com/apps/vendor-guidelines) aplicado a `payment_recurrente_api`. El módulo es **gratuito**: el manifest no lleva `price` ni `currency`.

Antes de cada subida, corre el verificador (falla si hay incumplimientos claros):

```bash
python scripts/check_appstore.py
```

> Un error en el manifest despublica **todos** los módulos del repositorio. Las reglas pueden cambiar: contrasta con la guía vigente antes de subir.

## Estado frente a las guías

Leyenda: ✅ cumple · ⚠️ decisión o dato pendiente · ❌ incumple y hay que corregirlo.

### Manifest

| Guía | Estado | Detalle |
|---|---|---|
| `name` de máximo 25 caracteres, explícito, sin adjetivos ni nombre de empresa | ✅ | `Recurrente Payments` (19 caracteres). |
| Mismo nombre en todas las versiones de Odoo | ✅ | Aplicado en `19.0` y `20.0`. |
| `version` con la versión de Odoo y formato mayor.menor.corrección | ✅ | `20.0.1.0.0`. |
| Apps en beta con versión menor a 1.0 | ✅ | Se publica `1.0.0`: ya hubo cobros y reembolsos reales en modo *live*, con fondos propios. |
| `license` | ✅ | LGPL-3, con el texto completo en `LICENSE`. |
| `depends` completo y existente | ✅ | Solo `payment`. Sin librerías Python externas (la firma Svix está implementada en el módulo). |
| `summary` | ✅ | Presente. |
| `price` / `currency` | ✅ | No se definen: la app es gratuita. |
| `support` (email) | ✅ | No hace falta en apps gratuitas; solo lo ven los compradores. |
| `live_test_url` | ✅ | Opcional; no aplica (requiere una cuenta de Recurrente). |
| Manifest sin errores | ✅ | Validado; los `data` existen y no hay claves desconocidas. |

### Página de descripción (`static/description/index.html`)

| Guía | Estado | Detalle |
|---|---|---|
| Descripción y capturas en **inglés** | ✅ | La sección "En español" se quitó; el `README.md` sigue en español. |
| Solo enlaces a archivos de `static/description`, YouTube canónico, Teams, `mailto:` o `skype:`; cualquier otro enlace externo se invalida | ✅ | Sin `href` externos: el repositorio y Recurrente aparecen como texto plano. |
| Sin `<script>`, `<style>`, iframes, formularios ni widgets | ✅ | Sin bloque `<style>`; todo con atributos en línea permitidos. |
| Estilos solo con clases Bootstrap 4 y atributos `color`, `font-*`, `margin-*`, `padding-*`, `border-*` | ✅ | Reescrita con clases Bootstrap 4 (`container`, `row`, `col-md-*`); sin clases `oe_*`. |
| Sin promociones, anuncios ni enlaces a otras tiendas o plataformas | ✅ | |
| Información exacta, sin funciones falsas ni ocultas | ✅ | Cada función descrita está probada en Sandbox. Revísalo si cambias el texto; no menciones transferencias bancarias ni criptomonedas. |
| Si requiere un servicio externo, debe anunciarse | ✅ | Está en la sección "BOUNDARIES" y en el subtítulo del héroe. |
| Datos enviados a un servicio externo: explicarlos en el manifest y en la descripción | ✅ | Sección **"DATA SENT TO RECURRENTE"** (05) en la página, y frase en la clave `description` del manifest. |

### Funcionalidad y datos

| Guía | Estado | Detalle |
|---|---|---|
| Instalable copiando la carpeta, sin otros pasos | ✅ | Probado en bases limpias de 19.0 y 20.0. |
| No altera la verificación de Odoo Enterprise ni la separación portal/interno | ✅ | No toca esas partes. |
| No es un clon de un módulo Enterprise | ✅ | |
| Sin clave de activación ni dependencia del vendedor | ✅ | La única llave es la de Recurrente del propio comercio. |
| Código propio; respetar licencias y copyright | ✅ | Escrito desde cero. Las copias de la documentación de Recurrente viven en `docs/`, que no se versiona ni entra en el ZIP. |
| Uso del nombre y del logo de Recurrente | ✅ | Autorizado por Recurrente por escrito. Ellos mismos pidieron la etiqueta "Developed by NioSystems" en la portada, que ya está. La página también aclara que no es un producto oficial. |
| Sin código ofuscado, descargas ni ejecución de código externo | ✅ | |
| Sin borrado de datos no solicitado | ✅ | Solo `neutralize.sql` limpia las llaves, al neutralizar una base (documentado). |
| Transparencia con datos del cliente | ✅ | Los logs solo guardan identificadores, no nombres, correos ni teléfonos. |

### Puntuación de la tienda (5 criterios)

| Criterio | Estado |
|---|---|
| Tiene icono | ✅ (`icon.png`, 128×128, cuadrado) |
| Tiene imagen de portada (thumbnail) | ⚠️ Falta `banner.png` y la clave `images` del manifest |
| Licencia definida | ✅ |
| Valoración mayor o igual a 3 | — (aún sin valoraciones) |
| Descripción en HTML | ✅ |

## Pendiente: imágenes y GIF

Todos van en `payment_recurrente_api/static/description/`, en inglés, con el **Sandbox**, **sin llaves visibles** (difumina `sk_...` y `whsec_...`) y sin datos reales de clientes.

| Archivo | Qué mostrar | Formato |
|---|---|---|
| `banner.png` | Portada: logo de Recurrente y el texto "Recurrente Payments". | PNG, proporción 2:1 (por ejemplo 1120×560) |
| `provider_form.png` | Ficha del proveedor con *Secret Key* y *Webhook Signing Secret* (difuminados), modo de prueba y pestaña *Payment Methods*. | PNG, ancho ≥ 1200 px |
| `checkout_option.png` | El pago en la tienda web o en una factura, con **Recurrente** como opción. | PNG |
| `saved_card.png` | Ficha de un contacto (o *Payment Tokens*) con una tarjeta guardada `•••• 4242`. | PNG |
| `refund.png` | Transacción confirmada con el botón de reembolso, o la transacción hija `R-...` confirmada. | PNG |
| `demo.gif` | Flujo completo de 15 a 25 segundos: elegir Recurrente, pagar en el Sandbox con `4242 4242 4242 4242`, volver a Odoo y ver la transacción confirmada. | GIF, menos de 5 MB |

Cuando existan: quita los marcadores `<!--` y `-->` alrededor de los `<img>` en `index.html` (solo los de archivos que existan) y descomenta la línea `"images"` en `__manifest__.py`.

## Antes de subir cada rama

- [ ] `python scripts/check_appstore.py` sin `FAIL`, y sin `WARN` que no hayas decidido aceptar.
- [ ] Las pruebas del módulo pasan (`--test-tags /payment_recurrente_api`), sin avisos propios.
- [ ] El nombre del módulo es el mismo en las dos ramas.
- [ ] No hay llaves, contraseñas ni datos reales en el código, en `static/` ni en las capturas.
- [ ] El ZIP contiene la carpeta `payment_recurrente_api/` en su raíz, sin `__pycache__` ni `.git`.

## Subida

Se sube una vez **por versión de Odoo** (una rama, un ZIP), con la misma cuenta y el mismo nombre de módulo. Para generar el ZIP desde la raíz de este repositorio:

```bash
git archive --format=zip --prefix=payment_recurrente_api/ -o payment_recurrente_api_20.0.zip HEAD:payment_recurrente_api
```

`git archive` solo incluye archivos versionados, así que no arrastra basura local.

## Mantenimiento

- Una corrección que aplique a las dos ramas se copia con `git cherry-pick`; **no** hagas merge entre `19.0` y `20.0` (los frameworks de `payment` difieren; ver la tabla del `README.md`).
- Sube una versión nueva cada vez que cambie el comportamiento (`20.0.1.1.0`, ...). Las versiones anteriores de Odoo no se actualizan solas.
