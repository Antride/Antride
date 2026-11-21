"""El agente: bucle de herramientas con la API de Claude."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

import anthropic

from .tools import SUGGESTED_CATEGORIES, TOOLS, ToolContext, run_tool

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
MAX_TOOL_ROUNDS = 15
# Al superar este número de mensajes, se empieza una conversación nueva (los datos
# siguen en la base de datos; solo se olvida el hilo de la charla).
MAX_HISTORY_MESSAGES = 60

WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

SYSTEM_PROMPT = f"""Eres "Finanzas", un asesor de finanzas personales que habla con su usuario por \
Telegram, en español, con tono cercano, directo y sin juzgar.

Tu trabajo:
1. Registrar gastos e ingresos. Cuando el usuario diga algo como "gasté 250 en el súper" o \
"me pagaron la quincena", regístralo de inmediato con registrar_movimiento, eligiendo una categoría \
razonable sin preguntar (categorías sugeridas: {", ".join(SUGGESTED_CATEGORIES)}). Si un mensaje trae \
varios movimientos, regístralos todos. Si el monto es ambiguo, pregunta.
2. Llevar el conteo del mes desde el día 1: usa estado_del_mes para decir cuánto lleva gastado, \
cuánto le queda y si va por encima, a la par o por debajo del ritmo esperado de cada presupuesto.
3. Presupuestos y alertas: ayuda a definir presupuestos realistas por categoría (y un TOTAL). Si una \
herramienta devuelve alertas_presupuesto, avísalo claramente.
4. Detectar a dónde se va el dinero y dónde está gastando de más: usa analizar_gastos y explica con \
cifras concretas los gastos hormiga, suscripciones, categorías que crecieron y gastos grandes.
5. Ayudar a acomodar las finanzas: propone un plan concreto (por ejemplo, la regla 50/30/20 adaptada \
a su ingreso), prioriza fondo de emergencia y pago de deudas caras, y sugiere recortes específicos \
con el ahorro mensual estimado de cada uno.
6. Metas de ahorro: crea metas, registra aportes y di cuánto debe ahorrar al mes para llegar.
7. Importar extractos: cuando el usuario suba un archivo recibirás su archivo_id. Llama ver_extracto, \
deduce las columnas, importa con importar_extracto y luego categoriza todo con sin_categorizar + \
categorizar (repite hasta que no quede nada sin categoría). Después resume lo importado.

Reglas:
- Basa tus respuestas en los datos de las herramientas; nunca inventes cifras. Si faltan datos \
(por ejemplo, el ingreso mensual), pregúntalo y guárdalo con guardar_ajuste.
- Sé breve: es un chat de móvil. Usa listas cortas y montos con separador de miles y la moneda \
indicada. No uses tablas ni encabezados Markdown; puedes usar *negritas* y emojis con moderación.
- Tras registrar un movimiento, confirma en una línea (monto, categoría) y, si es útil, cómo va esa \
categoría este mes.
- Das orientación general de finanzas personales, no asesoría financiera, fiscal ni legal \
profesional; para inversiones o decisiones grandes, sugiere consultar a un profesional.
"""


class Refused(Exception):
    """El modelo (y su respaldo) rechazaron la petición."""


class FinanceAgent:
    def __init__(self, ctx: ToolContext, currency: str, client: anthropic.AsyncAnthropic | None = None):
        self.ctx = ctx
        self.currency = currency
        self.client = client or anthropic.AsyncAnthropic()
        self._history: dict[int, list] = defaultdict(list)
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def reset(self, chat_id: int) -> None:
        self._history.pop(chat_id, None)

    def _context_header(self) -> str:
        today = self.ctx.today()
        return f"[Hoy es {WEEKDAYS[today.weekday()]} {today.isoformat()}. Moneda: {self.currency}]"

    async def ask(self, chat_id: int, text: str) -> str:
        """Procesa un mensaje del usuario y devuelve la respuesta final en texto."""
        async with self._locks[chat_id]:
            history = self._history[chat_id]
            if len(history) > MAX_HISTORY_MESSAGES:
                history.clear()
            checkpoint = len(history)
            history.append({"role": "user", "content": f"{self._context_header()}\n\n{text}"})
            try:
                return await self._run(history)
            except Refused:
                del history[checkpoint:]
                return "No puedo ayudarte con eso. ¿Te ayudo con otra cosa de tus finanzas?"
            except Exception:
                # Deja el historial como estaba para no romper la conversación.
                del history[checkpoint:]
                raise

    async def _run(self, messages: list) -> str:
        for _ in range(MAX_TOOL_ROUNDS):
            response = await self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
                output_config={"effort": "medium"},
                cache_control={"type": "ephemeral"},
                # Si un clasificador de seguridad rechaza la petición, la API la reintenta
                # en el modelo de respaldo recomendado en lugar de devolver un rechazo.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            # Se guarda el contenido completo (incluidos bloques de razonamiento) tal cual.
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                raise Refused()
            if response.stop_reason != "tool_use":
                text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "max_tokens":
                    text += "\n\n(Respuesta recortada por longitud.)"
                return text or "Listo."

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                try:
                    output = run_tool(self.ctx, block.name, dict(block.input))
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": output})
                except Exception as e:  # el error se le devuelve a Claude para que lo corrija
                    log.warning("Error en herramienta %s: %s", block.name, e)
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": json.dumps({"error": str(e)}, ensure_ascii=False),
                                    "is_error": True})
            messages.append({"role": "user", "content": results})

        return "Me tomó demasiados pasos resolver esto. ¿Puedes reformularlo?"
