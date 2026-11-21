"""Bot de Telegram: recibe mensajes y archivos, y envía avisos programados."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, time

import anthropic
from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
)

from .agent import FinanceAgent
from .config import Config
from .db import FinanceDB, TOTAL, prev_month
from .tools import ToolContext

log = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".csv", ".txt", ".xlsx", ".xlsm"}
MAX_FILE_BYTES = 5 * 1024 * 1024

HELP = """👋 Soy tu asistente de finanzas. Háblame normal, por ejemplo:

• "Gasté 320 en el súper y 85 en un café"
• "Me pagaron 15,000 de quincena"
• "¿Cómo voy este mes?"
• "¿En qué estoy gastando de más?"
• "Ponme un presupuesto de 3,000 para restaurantes"
• "Quiero juntar 30,000 para un fondo de emergencia en diciembre 2027"
• "Ayúdame a acomodar mis finanzas"
• Mándame el extracto de tu banco en CSV o Excel y lo importo.

Comandos:
/mes – cómo vas este mes (sobre, a la par o debajo)
/analisis – a dónde se va tu dinero
/plan – plan para acomodar tus finanzas
/metas – tus metas de ahorro
/nuevo – empezar una conversación nueva (tus datos se conservan)

Cada día te aviso si te acercas a un presupuesto, cada lunes te mando cómo vas y el día 1 \
te mando el resumen del mes anterior."""


def build_app(cfg: Config) -> Application:
    db = FinanceDB(cfg.db_path)
    cfg.uploads_dir.mkdir(parents=True, exist_ok=True)
    ctx = ToolContext(db=db, uploads_dir=cfg.uploads_dir,
                      today=lambda: datetime.now(cfg.timezone).date())
    agent = FinanceAgent(ctx, cfg.currency)

    app = Application.builder().token(cfg.telegram_token).build()
    app.bot_data.update(cfg=cfg, db=db, ctx=ctx, agent=agent)

    allowed = filters.User(user_id=list(cfg.allowed_user_ids)) if cfg.allowed_user_ids else None

    def guarded(handler_filter):
        return handler_filter & allowed if allowed else handler_filter & filters.User(user_id=[])

    app.add_handler(CommandHandler("start", cmd_start))  # muestra el id aunque no esté autorizado
    app.add_handler(CommandHandler("ayuda", cmd_help, filters=guarded(filters.ALL)))
    app.add_handler(CommandHandler("nuevo", cmd_reset, filters=guarded(filters.ALL)))
    for cmd, prompt in {
        "mes": "¿Cómo voy este mes? Dame el conteo desde el día 1 y dime si voy por encima, a la "
               "par o por debajo en cada presupuesto.",
        "analisis": "Analiza a dónde se va mi dinero y en qué estoy gastando de más.",
        "plan": "Ayúdame a acomodar mis finanzas: revisa mis datos y propón un plan concreto.",
        "metas": "¿Cómo van mis metas de ahorro?",
        "presupuestos": "Muéstrame mis presupuestos y cómo van.",
    }.items():
        app.add_handler(CommandHandler(cmd, _prompt_command(prompt), filters=guarded(filters.ALL)))
    app.add_handler(MessageHandler(guarded(filters.Document.ALL), on_document))
    app.add_handler(MessageHandler(guarded(filters.TEXT & ~filters.COMMAND), on_text))
    app.add_error_handler(on_error)

    tz = cfg.timezone
    jq = app.job_queue
    jq.run_daily(job_budget_alerts, time(hour=20, minute=30, tzinfo=tz), name="alertas")
    jq.run_daily(job_weekly_status, time(hour=9, minute=7, tzinfo=tz), days=(1,), name="semanal")
    jq.run_daily(job_monthly_report, time(hour=9, minute=13, tzinfo=tz), name="mensual")
    return app


# ---------------------------------------------------------------------------- utilidades
async def send_long(bot, chat_id: int, text: str) -> None:
    """Envía texto respetando el límite de 4096 caracteres de Telegram."""
    chunks, current = [], ""
    for para in text.split("\n"):
        if len(current) + len(para) + 1 > 3800:
            chunks.append(current)
            current = ""
        current += para + "\n"
    chunks.append(current)
    for chunk in filter(str.strip, chunks):
        try:
            await bot.send_message(chat_id, chunk, parse_mode="Markdown")
        except BadRequest:  # Markdown mal formado: se envía como texto plano
            await bot.send_message(chat_id, chunk)


async def ask_and_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    agent: FinanceAgent = context.bot_data["agent"]
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    try:
        answer = await agent.ask(chat_id, text)
    except anthropic.RateLimitError:
        answer = "Estoy recibiendo demasiadas solicitudes; intenta de nuevo en un minuto."
    except anthropic.AuthenticationError:
        answer = "La clave de la API de Anthropic no es válida. Revisa ANTHROPIC_API_KEY."
    except anthropic.APIConnectionError:
        answer = "No pude conectarme con Claude. Revisa tu conexión e intenta de nuevo."
    except anthropic.APIStatusError as e:
        log.error("Error de API (%s): %s", e.status_code, e.message)
        answer = "Hubo un error al procesar tu mensaje. Intenta de nuevo."
    await send_long(context.bot, chat_id, answer)


def _prompt_command(prompt: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await ask_and_reply(update, context, prompt)
    return handler


# ---------------------------------------------------------------------------- handlers
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["cfg"]
    user_id = update.effective_user.id
    if user_id not in cfg.allowed_user_ids:
        await update.message.reply_text(
            f"Tu id de Telegram es {user_id}. Agrégalo a ALLOWED_USER_IDS en el archivo .env "
            "y reinicia el bot para poder usarlo."
        )
        return
    await update.message.reply_text(HELP)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data["agent"].reset(update.effective_chat.id)
    await update.message.reply_text("Listo, empezamos de cero la conversación. Tus datos siguen guardados.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await ask_and_reply(update, context, update.message.text)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["cfg"]
    doc = update.message.document
    name = doc.file_name or "extracto.csv"
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in ALLOWED_EXTENSIONS:
        await update.message.reply_text("Por ahora solo puedo leer extractos en CSV o Excel (.xlsx).")
        return
    if doc.file_size and doc.file_size > MAX_FILE_BYTES:
        await update.message.reply_text("El archivo es muy grande (máximo 5 MB).")
        return
    file_id = uuid.uuid4().hex[:12]
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(cfg.uploads_dir / f"{file_id}{ext}")
    note = update.message.caption or ""
    await ask_and_reply(
        update, context,
        f"Te subí un extracto bancario: '{name}' (archivo_id={file_id}). Impórtalo, categoriza "
        f"los movimientos y dime a dónde se fue mi dinero. {note}".strip(),
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Error no manejado", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        await context.bot.send_message(update.effective_chat.id,
                                       "Algo salió mal procesando eso. Intenta de nuevo.")


# ---------------------------------------------------------------------------- tareas programadas
async def job_budget_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Avisa (sin usar la IA) de presupuestos que cruzaron el 80% o el 100%."""
    cfg: Config = context.bot_data["cfg"]
    db: FinanceDB = context.bot_data["db"]
    today = context.bot_data["ctx"].today()
    pending = db.pending_alerts(today)
    if not pending:
        return
    worst: dict[str, dict] = {}
    for a in pending:
        if a["categoria"] not in worst or a["umbral"] > worst[a["categoria"]]["umbral"]:
            worst[a["categoria"]] = a
        db.mark_alert_sent(today.strftime("%Y-%m"), a["categoria"], a["umbral"])
    lines = ["⚠️ *Alerta de presupuesto*"]
    for cat, a in worst.items():
        label = "Gasto total" if cat == TOTAL else cat
        if a["umbral"] >= 100:
            lines.append(f"• {label}: te pasaste — {a['gastado']:,.2f} de {a['presupuesto']:,.2f} "
                         f"{cfg.currency}")
        else:
            lines.append(f"• {label}: llevas {a['usado_pct']}% ({a['gastado']:,.2f} de "
                         f"{a['presupuesto']:,.2f} {cfg.currency}); te quedan {a['restante']:,.2f}")
    for uid in cfg.allowed_user_ids:
        await send_long(context.bot, uid, "\n".join(lines))


async def _proactive(context: ContextTypes.DEFAULT_TYPE, prompt: str) -> None:
    cfg: Config = context.bot_data["cfg"]
    agent: FinanceAgent = context.bot_data["agent"]
    for uid in cfg.allowed_user_ids:
        try:
            answer = await agent.ask(uid, prompt)
        except anthropic.APIError as e:
            log.error("No se pudo generar el aviso programado: %s", e)
            continue
        await send_long(context.bot, uid, answer)


async def job_weekly_status(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _proactive(context, "(Mensaje automático del lunes) Hazme un repaso corto de cómo voy este "
                              "mes: total gastado, si voy por encima, a la par o por debajo en cada "
                              "presupuesto, y un consejo concreto para esta semana.")


async def job_monthly_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    today = context.bot_data["ctx"].today()
    if today.day != 1:
        return
    y, m = prev_month(today.year, today.month)
    await _proactive(context, f"(Mensaje automático del día 1) Arrancó un mes nuevo. Hazme el "
                              f"resumen de {y}-{m:02d} con resumen_mensual: ingresos, gastos, ahorro, "
                              f"en qué gasté de más, cómo me fue con los presupuestos y metas, y qué "
                              f"ajustes de presupuesto me recomiendas para este mes.")
