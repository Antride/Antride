"""Herramientas que Claude puede usar, y su ejecución contra la base de datos."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from . import importer
from .db import FinanceDB

SUGGESTED_CATEGORIES = [
    "Vivienda", "Servicios", "Supermercado", "Restaurantes", "Comida a domicilio", "Café y snacks",
    "Transporte", "Gasolina", "Salud", "Cuidado personal", "Entretenimiento", "Suscripciones",
    "Ropa", "Educación", "Deudas", "Ahorro e inversión", "Regalos", "Mascotas", "Viajes",
    "Compras en línea", "Comisiones bancarias", "Otros",
    # ingresos
    "Salario", "Freelance", "Reembolsos", "Otros ingresos",
]

_DATE = {"type": "string", "description": "Fecha en formato YYYY-MM-DD"}

TOOLS: list[dict] = [
    {
        "name": "registrar_movimiento",
        "description": (
            "Registra un gasto o un ingreso. Úsala cada vez que el usuario mencione que gastó, "
            "pagó, compró, cobró o recibió dinero. Si no dice la fecha, usa la de hoy. "
            "El resultado incluye alertas de presupuesto si se cruzó el 80% o el 100%."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "monto": {"type": "number", "description": "Monto positivo"},
                "tipo": {"type": "string", "enum": ["gasto", "ingreso"]},
                "categoria": {"type": "string",
                              "description": "Preferiblemente una de las categorías sugeridas"},
                "descripcion": {"type": "string"},
                "fecha": _DATE,
            },
            "required": ["monto", "tipo", "categoria", "descripcion"],
        },
    },
    {
        "name": "editar_movimiento",
        "description": "Corrige un movimiento existente por su id (solo los campos indicados).",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "monto": {"type": "number"},
                "tipo": {"type": "string", "enum": ["gasto", "ingreso"]},
                "categoria": {"type": "string"},
                "descripcion": {"type": "string"},
                "fecha": _DATE,
            },
            "required": ["id"],
        },
    },
    {
        "name": "borrar_movimiento",
        "description": "Elimina un movimiento por su id. Confirma con el usuario antes si hay duda.",
        "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}},
                         "required": ["id"]},
    },
    {
        "name": "buscar_movimientos",
        "description": "Lista movimientos con filtros opcionales (fechas, categoría, tipo, texto).",
        "input_schema": {
            "type": "object",
            "properties": {
                "desde": _DATE, "hasta": _DATE,
                "categoria": {"type": "string"},
                "tipo": {"type": "string", "enum": ["gasto", "ingreso"]},
                "texto": {"type": "string", "description": "Busca en la descripción"},
                "limite": {"type": "integer", "description": "Máximo de resultados (def. 50)"},
            },
        },
    },
    {
        "name": "estado_del_mes",
        "description": (
            "Conteo del mes en curso desde el día 1: gasto total y por categoría, y para cada "
            "presupuesto el % usado, lo restante, la proyección a fin de mes, cuánto puede gastar "
            "por día y si va 'por encima', 'a la par' o 'por debajo' del ritmo esperado."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "resumen_mensual",
        "description": "Resumen de un mes cerrado (ingresos, gastos, ahorro) comparado con el anterior.",
        "input_schema": {
            "type": "object",
            "properties": {"anio": {"type": "integer"}, "mes": {"type": "integer"}},
            "required": ["anio", "mes"],
        },
    },
    {
        "name": "analizar_gastos",
        "description": (
            "Analiza a dónde se va el dinero en los últimos meses: % por categoría, gastos "
            "hormiga (pequeños y frecuentes), posibles suscripciones/recurrentes y los gastos "
            "más grandes. Úsala para detectar dónde el usuario está gastando de más."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"meses": {"type": "integer",
                                     "description": "Meses completos hacia atrás (def. 3)"}},
        },
    },
    {
        "name": "fijar_presupuesto",
        "description": ("Crea o actualiza el presupuesto mensual de una categoría. Usa la "
                        "categoría 'TOTAL' para el límite de gasto total del mes."),
        "input_schema": {
            "type": "object",
            "properties": {"categoria": {"type": "string"}, "limite_mensual": {"type": "number"}},
            "required": ["categoria", "limite_mensual"],
        },
    },
    {
        "name": "borrar_presupuesto",
        "description": "Elimina el presupuesto de una categoría.",
        "input_schema": {"type": "object", "properties": {"categoria": {"type": "string"}},
                         "required": ["categoria"]},
    },
    {
        "name": "ver_presupuestos",
        "description": "Lista los presupuestos mensuales configurados.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "fijar_meta",
        "description": "Crea o actualiza una meta de ahorro (fondo de emergencia, viaje, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string"},
                "objetivo": {"type": "number", "description": "Monto total a juntar"},
                "fecha_limite": _DATE,
                "ahorrado": {"type": "number", "description": "Monto ya ahorrado (opcional)"},
            },
            "required": ["nombre", "objetivo"],
        },
    },
    {
        "name": "aportar_meta",
        "description": "Suma (o resta, con monto negativo) dinero a una meta de ahorro.",
        "input_schema": {
            "type": "object",
            "properties": {"nombre": {"type": "string"}, "monto": {"type": "number"}},
            "required": ["nombre", "monto"],
        },
    },
    {
        "name": "borrar_meta",
        "description": "Elimina una meta de ahorro.",
        "input_schema": {"type": "object", "properties": {"nombre": {"type": "string"}},
                         "required": ["nombre"]},
    },
    {
        "name": "ver_metas",
        "description": "Lista las metas con su progreso y el ahorro mensual necesario.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "guardar_ajuste",
        "description": (
            "Guarda un dato del perfil financiero. Claves útiles: 'ingreso_mensual' (número), "
            "'dia_de_pago', 'deudas' (texto libre), 'umbral_gasto_hormiga' (número), "
            "'objetivo_principal' (texto)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"clave": {"type": "string"}, "valor": {"type": "string"}},
            "required": ["clave", "valor"],
        },
    },
    {
        "name": "ver_extracto",
        "description": ("Muestra columnas y filas de muestra de un extracto bancario que el "
                        "usuario subió, para decidir cómo importarlo."),
        "input_schema": {"type": "object", "properties": {"archivo_id": {"type": "string"}},
                         "required": ["archivo_id"]},
    },
    {
        "name": "importar_extracto",
        "description": (
            "Importa los movimientos de un extracto ya revisado con ver_extracto. Columnas "
            "indexadas desde 0. Usa columna_monto si hay una sola columna con signo, o "
            "columna_cargo/columna_abono si el banco las separa. Los duplicados se omiten. "
            "Los movimientos quedan 'Sin categoría': después usa sin_categorizar y categorizar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "archivo_id": {"type": "string"},
                "columna_fecha": {"type": "integer"},
                "columna_descripcion": {"type": "integer"},
                "columna_monto": {"type": "integer"},
                "columna_cargo": {"type": "integer"},
                "columna_abono": {"type": "integer"},
                "formato_fecha": {"type": "string",
                                  "description": "Formato strptime, p. ej. %d/%m/%Y (opcional)"},
                "negativo_es_gasto": {"type": "boolean",
                                      "description": "Con columna_monto: true si los cargos vienen negativos (def. true)"},
            },
            "required": ["archivo_id", "columna_fecha", "columna_descripcion"],
        },
    },
    {
        "name": "sin_categorizar",
        "description": "Lista movimientos que aún no tienen categoría.",
        "input_schema": {"type": "object", "properties": {"limite": {"type": "integer"}}},
    },
    {
        "name": "categorizar",
        "description": "Asigna categorías a varios movimientos a la vez.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asignaciones": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}, "categoria": {"type": "string"}},
                        "required": ["id", "categoria"],
                    },
                }
            },
            "required": ["asignaciones"],
        },
    },
]


@dataclass
class ToolContext:
    db: FinanceDB
    uploads_dir: Path
    today: Callable[[], date]


def _upload_path(ctx: ToolContext, file_id: str) -> Path:
    # Solo se aceptan ids generados por el bot (sin rutas).
    if not file_id or "/" in file_id or "\\" in file_id or file_id.startswith("."):
        raise ValueError("archivo_id inválido")
    matches = list(ctx.uploads_dir.glob(f"{file_id}.*"))
    if not matches:
        raise ValueError(f"No encuentro el archivo {file_id}")
    return matches[0]


def _budget_alerts(ctx: ToolContext) -> list[dict]:
    """Devuelve (y marca como enviadas) las alertas nuevas de presupuesto."""
    today = ctx.today()
    out = []
    by_cat: dict[str, dict] = {}
    for a in ctx.db.pending_alerts(today):
        prev = by_cat.get(a["categoria"])
        if not prev or a["umbral"] > prev["umbral"]:
            by_cat[a["categoria"]] = a
        ctx.db.mark_alert_sent(today.strftime("%Y-%m"), a["categoria"], a["umbral"])
    for a in by_cat.values():
        out.append({k: a[k] for k in ("categoria", "umbral", "gastado", "presupuesto",
                                      "restante", "ritmo")})
    return out


def run_tool(ctx: ToolContext, name: str, args: dict) -> str:
    """Ejecuta una herramienta y devuelve el resultado como JSON (texto)."""
    db = ctx.db
    today = ctx.today()

    if name == "registrar_movimiento":
        tx_id = db.add_transaction(
            args.get("fecha") or today.isoformat(), args["monto"], args["tipo"],
            args.get("categoria"), args.get("descripcion", ""),
        )
        result: dict = {"ok": True, "id": tx_id}
        if args["tipo"] == "gasto":
            alerts = _budget_alerts(ctx)
            if alerts:
                result["alertas_presupuesto"] = alerts
        return _json(result)

    if name == "editar_movimiento":
        ok = db.update_transaction(
            args["id"], date=args.get("fecha"), amount=args.get("monto"), kind=args.get("tipo"),
            category=args.get("categoria"), description=args.get("descripcion"),
        )
        return _json({"ok": ok})

    if name == "borrar_movimiento":
        return _json({"ok": db.delete_transaction(args["id"])})

    if name == "buscar_movimientos":
        rows = db.list_transactions(
            start=args.get("desde"), end=args.get("hasta"), category=args.get("categoria"),
            kind=args.get("tipo"), search=args.get("texto"), limit=min(args.get("limite", 50), 300),
        )
        for r in rows:
            r.pop("fingerprint", None)
        return _json({"movimientos": rows, "cantidad": len(rows)})

    if name == "estado_del_mes":
        return _json(db.month_status(today))

    if name == "resumen_mensual":
        return _json(db.month_report(int(args["anio"]), int(args["mes"])))

    if name == "analizar_gastos":
        return _json(db.spending_insights(today, max(1, min(int(args.get("meses", 3)), 12))))

    if name == "fijar_presupuesto":
        db.set_budget(args["categoria"], args["limite_mensual"])
        return _json({"ok": True, "presupuestos": db.budgets()})

    if name == "borrar_presupuesto":
        return _json({"ok": db.delete_budget(args["categoria"])})

    if name == "ver_presupuestos":
        return _json({"presupuestos": db.budgets()})

    if name == "fijar_meta":
        db.set_goal(args["nombre"], args["objetivo"], args.get("fecha_limite"), args.get("ahorrado"))
        return _json({"ok": True, "metas": db.goals(today)})

    if name == "aportar_meta":
        goal = db.contribute_goal(args["nombre"], args["monto"])
        return _json({"ok": goal is not None, "meta": goal})

    if name == "borrar_meta":
        return _json({"ok": db.delete_goal(args["nombre"])})

    if name == "ver_metas":
        return _json({"metas": db.goals(today)})

    if name == "guardar_ajuste":
        db.set_setting(args["clave"], args["valor"])
        return _json({"ok": True})

    if name == "ver_extracto":
        return _json(importer.preview(_upload_path(ctx, args["archivo_id"])))

    if name == "importar_extracto":
        path = _upload_path(ctx, args["archivo_id"])
        txs, errors = importer.parse(
            path,
            date_column=args["columna_fecha"],
            description_column=args["columna_descripcion"],
            amount_column=args.get("columna_monto"),
            debit_column=args.get("columna_cargo"),
            credit_column=args.get("columna_abono"),
            date_format=args.get("formato_fecha"),
            negative_is_expense=args.get("negativo_es_gasto", True),
        )
        added = sum(
            1 for t in txs
            if db.add_transaction(t["date"], t["amount"], t["kind"], None, t["description"],
                                  source="importado", dedupe=True) is not None
        )
        return _json({
            "importados": added,
            "duplicados_omitidos": len(txs) - added,
            "errores": errors[:20],
            "total_errores": len(errors),
            "rango_fechas": [min((t["date"] for t in txs), default=None),
                             max((t["date"] for t in txs), default=None)],
        })

    if name == "sin_categorizar":
        rows = db.uncategorized(min(int(args.get("limite", 100)), 200))
        return _json({"movimientos": rows, "cantidad": len(rows)})

    if name == "categorizar":
        n = db.set_categories([{"id": a["id"], "category": a["categoria"]}
                               for a in args["asignaciones"]])
        alerts = _budget_alerts(ctx)
        return _json({"actualizados": n, **({"alertas_presupuesto": alerts} if alerts else {})})

    raise ValueError(f"Herramienta desconocida: {name}")


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
