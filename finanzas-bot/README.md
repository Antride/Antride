# 💰 Finanzas: tu agente de finanzas personales en Telegram

Un bot de Telegram que usa Claude para:

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

El bot necesita estar corriendo para responder y mandar los avisos: déjalo en tu computadora, en una Raspberry Pi o en un servidor pequeño (por ejemplo con `systemd`, `tmux` o Docker).

## Comandos

| Comando | Qué hace |
|---|---|
| `/mes` | Conteo del mes: ¿voy sobre, a la par o debajo? |
| `/analisis` | A dónde se va tu dinero y dónde gastas de más |
| `/plan` | Plan para acomodar tus finanzas |
| `/presupuestos` | Tus presupuestos y cómo van |
| `/metas` | Tus metas de ahorro |
| `/nuevo` | Empieza una conversación nueva (los datos se conservan) |
| `/ayuda` | Ejemplos de uso |

Y cualquier otra cosa, escríbela normal. Para importar, manda el archivo del extracto al chat.

**Primeros pasos recomendados**: dile cuánto ganas al mes, pídele que te proponga presupuestos (*"ayúdame a armar mis presupuestos"*) y sube el extracto de los últimos 2 o 3 meses para que tenga historial.

## Cómo funciona

```
Telegram ──► bot.py ──► agent.py (Claude + herramientas) ──► tools.py ──► db.py (SQLite)
                 └── tareas programadas (alertas, lunes, día 1)
```

- `finanzas/db.py`: base de datos SQLite (`data/finanzas.db`) y los cálculos: ritmo del mes, resúmenes, detección de gastos hormiga y recurrentes, alertas.
- `finanzas/tools.py`: las herramientas que Claude puede usar (registrar, buscar, presupuestos, metas, importar...).
- `finanzas/agent.py`: el agente; usa `claude-opus-5-5` con un bucle de herramientas. Si un filtro de seguridad rechaza una petición, la API la reintenta con un modelo de respaldo (`fallbacks: "default"`).
- `finanzas/importer.py`: lector de CSV/Excel tolerante a formatos (`1.234,56` o `1,234.56`, `;` o `,`, cargo/abono separados).
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
| `DATA_DIR` | `data` | Carpeta de la base de datos y los extractos |

El "a la par" tiene un margen de ±10 puntos: el día 15 de un mes de 30 días (50% del mes), haber usado entre 40% y 60% del presupuesto es ir a la par. Se cambia en `PACE_TOLERANCE` (`db.py`). El umbral de "gasto hormiga" es 150 por defecto; dile al bot que lo cambie si quieres otro.

## Pruebas

```bash
pip install pytest
python -m pytest
```

## Privacidad

Tus movimientos se guardan solo en tu equipo (`data/`). Cada mensaje que le mandas al bot, y los datos que consulta para responderte, se envían a la API de Anthropic para generar la respuesta, y pasan por Telegram. No subas `.env` ni `data/` a GitHub (ya están en `.gitignore`).

> Este bot da orientación general de finanzas personales; no es asesoría financiera, fiscal ni legal profesional.
