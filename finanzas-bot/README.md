# 💰 Finanzas: tu agente de finanzas personales en Telegram

Un bot de Telegram que usa Claude para:

- **Leer tu Gmail y/o tu Outlook** (solo lectura) y registrar solo las compras de las **notificaciones del banco**, recibos de comercios y **estados de cuenta en PDF**, avisándote por Telegram de cada una.
- **Registrar gastos e ingresos** con lenguaje natural: *"gasté 320 en el súper y 85 en un café"*.
- **Llevar el conteo del mes desde el día 1** y decirte si vas **por encima, a la par o por debajo** de cada presupuesto, con una proyección a fin de mes y cuánto puedes gastar por día.
- **Presupuestos y alertas**: te avisa al llegar al 80% y al 100% de cada presupuesto.
- **Decirte a dónde se va tu dinero**: gastos hormiga, suscripciones y cargos recurrentes, categorías que crecieron y gastos grandes.
- **Ayudarte a acomodar tus finanzas** con un plan concreto (fondo de emergencia, deudas, recortes con el ahorro estimado).
- **Metas de ahorro** con el ahorro mensual que necesitas para llegar.
- **Importar extractos del banco** en CSV o Excel: detecta las columnas, evita duplicados y categoriza todo solo.

Avisos automáticos:
| Cuándo | Qué |
|---|---|
| Cada 15 min (con Gmail u Outlook) | Compras e ingresos nuevos detectados en tu correo |
| Al llegar un estado de cuenta | Pago mínimo, pago para no generar intereses y fecha límite |
| 3 días antes de la fecha límite | Recordatorio de pago de la tarjeta |
| Todos los días, 20:30 | Alertas de presupuesto pendientes |
| Lunes, 9:07 | Cómo vas en el mes y un consejo para la semana |
| Día 1, 9:13 | Resumen del mes anterior y ajustes recomendados |

## Puesta en marcha

1. **Crea el bot en Telegram**: habla con [@BotFather](https://t.me/BotFather), manda `/newbot` y copia el token.
2. **Consigue una API key de Anthropic** en <https://platform.claude.com>.
3. **Instala y configura**:
   ```bash
   cd finanzas-bot
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env   # y rellena TELEGRAM_BOT_TOKEN y ANTHROPIC_API_KEY
   ```
4. **Arranca el bot**: `python main.py`
5. **Autorízate**: mándale `/start` a tu bot; te responderá con tu id de Telegram. Ponlo en `ALLOWED_USER_IDS` dentro de `.env` y reinicia. Así nadie más puede ver ni tocar tus finanzas.

6. **(Opcional, recomendado) Conecta tu correo**: Gmail, Outlook o los dos (ver las secciones siguientes).

El bot necesita estar corriendo para responder y mandar los avisos: déjalo en tu computadora, en una Raspberry Pi o en un servidor pequeño (por ejemplo con `systemd`, `tmux` o Docker).

## Conectar Gmail

El bot usa la API oficial de Gmail con permiso de **solo lectura** (`gmail.readonly`): puede leer, pero nunca enviar, borrar ni modificar correos. Tienes que crear tu propia "app" en Google (gratis, 10 minutos, una sola vez):

1. Entra a <https://console.cloud.google.com/>, crea un proyecto (p. ej. "finanzas-bot").
2. En **APIs y servicios → Biblioteca**, busca **Gmail API** y actívala.
3. En **APIs y servicios → Pantalla de consentimiento de OAuth** (o "Google Auth Platform"): tipo **Externo**, pon tu nombre y correo, y en **Usuarios de prueba** agrega tu propio Gmail.
4. En **Credenciales → Crear credenciales → ID de cliente de OAuth**, tipo **App de escritorio**. Descarga el JSON y guárdalo como `data/gmail_credentials.json`.
5. En la computadora donde corre el bot (necesita navegador): `python main.py --vincular-gmail`. Se abre Google, aceptas, y se guarda `data/gmail_token.json`. Google mostrará "app no verificada": es normal, porque la app es tuya; dale "Continuar".
6. Reinicia el bot. La primera revisión lee los últimos 60 días; luego cada 15 minutos. También puedes usar `/correo` para revisar al momento.

Si corres el bot en un servidor sin navegador, haz el paso 5 en tu computadora y copia `data/gmail_token.json` al servidor.

**Estados de cuenta con contraseña**: muchos bancos protegen el PDF (con tu RFC, fecha de nacimiento, etc.). Pon esas contraseñas en `PDF_PASSWORDS` en `.env` para que el bot pueda abrirlos.

**Qué cuenta y qué no**: se registran compras, cargos, retiros, pagos de servicios, suscripciones, depósitos y reembolsos. Se ignoran publicidad, compras rechazadas, **pagos a tu tarjeta de crédito y transferencias entre tus cuentas** (para no contar dos veces el mismo gasto). Si una compra llega como notificación y luego aparece en el estado de cuenta, o ya la habías anotado a mano, no se duplica (mismo monto en ±3 a 5 días). Si algo se registra mal, dile al bot "borra el movimiento 123" o "eso era de Supermercado".

Para afinar qué correos revisa, usa `GMAIL_QUERY` con la [sintaxis de búsqueda de Gmail](https://support.google.com/mail/answer/7190), por ejemplo `from:(bbva.mx OR banorte.com OR mercadolibre.com)`.

## Conectar Outlook

Sirve para Outlook.com, Hotmail, Live y cuentas de Microsoft 365 (trabajo o escuela). Usa la API oficial de Microsoft (Microsoft Graph) con permiso de **solo lectura** (`Mail.Read`). Necesitas registrar tu propia app (gratis, una sola vez):

1. Entra a <https://entra.microsoft.com> (o <https://portal.azure.com>) con tu cuenta de Microsoft → **Registros de aplicaciones** → **Nuevo registro**.
2. Nombre: "finanzas-bot". En **Tipos de cuenta admitidos** elige *Cuentas en cualquier directorio organizativo y cuentas personales de Microsoft*. Sin URI de redirección. **Registrar**.
3. Copia el **Id. de aplicación (cliente)** y ponlo en `OUTLOOK_CLIENT_ID` en `.env`.
4. En **Autenticación** → **Configuración avanzada**, activa **Permitir flujos de clientes públicos** y guarda.
5. En **Permisos de API** → **Agregar un permiso** → **Microsoft Graph** → **Permisos delegados** → marca **Mail.Read** → agregar.
6. Ejecuta `python main.py --vincular-outlook`. Te mostrará un código y la dirección <https://microsoft.com/devicelogin>: ábrela **desde cualquier dispositivo** (tu celular sirve), escribe el código y acepta. Se guarda `data/outlook_token.json`.
7. Reinicia el bot. Como con Gmail, la primera revisión cubre 60 días y luego revisa cada 15 minutos; `/correo` revisa los dos buzones.

El bot revisa todas tus carpetas excepto *Correo no deseado* y *Elementos eliminados*, y solo toma los correos cuyo asunto, remitente o vista previa tienen palabras como compra, cargo, estado de cuenta, depósito, recibo, pedido, etc. Si tu banco usa otras palabras, ponlas en `OUTLOOK_KEYWORDS`. Si la cuenta es de tu trabajo, puede que tu organización tenga que aprobar la app. Puedes revocar el acceso en <https://account.live.com/consent/Manage> (cuenta personal) o <https://myapps.microsoft.com> (trabajo).

## Comandos

| Comando | Qué hace |
|---|---|
| `/mes` | Conteo del mes: ¿voy sobre, a la par o debajo? |
| `/analisis` | A dónde se va tu dinero y dónde gastas de más |
| `/plan` | Plan para acomodar tus finanzas |
| `/presupuestos` | Tus presupuestos y cómo van |
| `/metas` | Tus metas de ahorro |
| `/correo` | Revisa tu correo ahora (Gmail y Outlook) |
| `/nuevo` | Empieza una conversación nueva (los datos se conservan) |
| `/ayuda` | Ejemplos de uso |

Y cualquier otra cosa, escríbela normal. Para importar, manda el archivo del extracto al chat.

**Primeros pasos recomendados**: dile cuánto ganas al mes, pídele que te proponga presupuestos (*"ayúdame a armar mis presupuestos"*) y sube el extracto de los últimos 2 o 3 meses para que tenga historial.

## Cómo funciona

```
Telegram ──► bot.py ──► agent.py (Claude + herramientas) ──► tools.py ──► db.py (SQLite)
                 ├── tareas programadas (alertas, lunes, día 1, recordatorios de pago)
Gmail   ──► gmail_client.py   ──┐
Outlook ──► outlook_client.py ──┴► email_sync.py (Claude extrae movimientos) ──► db.py
```

- `finanzas/db.py`: base de datos SQLite (`data/finanzas.db`) y los cálculos: ritmo del mes, resúmenes, detección de gastos hormiga y recurrentes, alertas.
- `finanzas/tools.py`: las herramientas que Claude puede usar (registrar, buscar, presupuestos, metas, importar...).
- `finanzas/agent.py`: el agente; usa `claude-opus-5-5` con un bucle de herramientas. Si un filtro de seguridad rechaza una petición, la API la reintenta con un modelo de respaldo (`fallbacks: "default"`).
- `finanzas/importer.py`: lector de CSV/Excel tolerante a formatos (`1.234,56` o `1,234.56`, `;` o `,`, cargo/abono separados).
- `finanzas/gmail_client.py`: lectura de Gmail (solo lectura), texto de correos HTML y apertura de PDFs con contraseña.
- `finanzas/outlook_client.py`: lectura de Outlook vía Microsoft Graph (solo lectura), con vinculación por código.
- `finanzas/email_sync.py`: Claude clasifica cada correo y extrae los movimientos en un formato fijo (salida estructurada); evita duplicados y guarda los estados de cuenta.
- `finanzas/bot.py`: comandos de Telegram y avisos programados.

Los cálculos (sumas, porcentajes, ritmo) los hace el código, no la IA, así que las cifras son exactas; Claude decide qué consultar y te lo explica.

### Configuración (`.env`)

| Variable | Por defecto | Descripción |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Token de @BotFather |
| `ANTHROPIC_API_KEY` | — | Clave de la API de Anthropic |
| `ALLOWED_USER_IDS` | — | Ids de Telegram autorizados, separados por coma |
| `CURRENCY` | `MXN` | Moneda que muestra el bot |
| `TIMEZONE` | `America/Mexico_City` | Zona horaria para fechas y avisos |
| `DATA_DIR` | `data` | Carpeta de la base de datos, extractos y credenciales de Gmail |
| `PDF_PASSWORDS` | — | Contraseñas de tus estados de cuenta en PDF, separadas por coma |
| `GMAIL_INTERVAL_MINUTES` | `15` | Cada cuánto revisar el correo (Gmail y Outlook) |
| `GMAIL_LOOKBACK_DAYS` | `60` | Días hacia atrás en la primera revisión (Gmail y Outlook) |
| `OUTLOOK_CLIENT_ID` | — | Id. de aplicación de tu registro en Microsoft Entra |
| `OUTLOOK_TENANT` | `common` | `common` (personal + trabajo) o `consumers` (solo personal) |
| `OUTLOOK_KEYWORDS` | (compra, cargo, estado de cuenta…) | Palabras para elegir correos de Outlook, separadas por coma |
| `GMAIL_QUERY` | (búsqueda de palabras como compra, cargo, estado de cuenta…) | Búsqueda de Gmail personalizada |

El "a la par" tiene un margen de ±10 puntos: el día 15 de un mes de 30 días (50% del mes), haber usado entre 40% y 60% del presupuesto es ir a la par. Se cambia en `PACE_TOLERANCE` (`db.py`). El umbral de "gasto hormiga" es 150 por defecto; dile al bot que lo cambie si quieres otro.

## Pruebas

```bash
pip install pytest
python -m pytest
```

## Privacidad y seguridad

Tus movimientos se guardan solo en tu equipo (`data/`). Cada mensaje que le mandas al bot, y los datos que consulta para responderte, se envían a la API de Anthropic para generar la respuesta, y pasan por Telegram.

Con Gmail u Outlook conectados, **solo los correos que coinciden con la búsqueda** (palabras como compra, cargo, estado de cuenta…), con sus PDFs, se envían a la API de Anthropic para extraer los movimientos. El resto de tu correo no sale de Google ni de Microsoft. `data/gmail_token.json` y `data/outlook_token.json` dan acceso de lectura a tu correo: no los compartas. Puedes revocar el acceso cuando quieras (Google: <https://myaccount.google.com/permissions>; Microsoft: ver "Conectar Outlook").

Como cualquiera puede mandarte un correo, la lectura de correos se hace con una llamada a Claude **sin herramientas** y con formato de salida fijo: un correo malicioso no puede hacer que el bot borre datos, ni haga o mande nada; como mucho registraría un movimiento falso, que verías en la notificación. No subas `.env` ni `data/` a GitHub (ya están en `.gitignore`).

> Este bot da orientación general de finanzas personales; no es asesoría financiera, fiscal ni legal profesional.
