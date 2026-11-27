import json
from datetime import date

import pytest

from finanzas import importer
from finanzas.db import FinanceDB
from finanzas.tools import TOOLS, ToolContext, run_tool

TODAY = date(2026, 11, 15)


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(db=FinanceDB(":memory:"), uploads_dir=tmp_path, today=lambda: TODAY)


def call(ctx, name, **args):
    return json.loads(run_tool(ctx, name, args))


def test_tool_schemas_are_valid():
    names = [t["name"] for t in TOOLS]
    assert len(names) == len(set(names))
    for t in TOOLS:
        assert t["input_schema"]["type"] == "object"


def test_register_and_month_pace(ctx):
    call(ctx, "fijar_presupuesto", categoria="Restaurantes", limite_mensual=1000)
    call(ctx, "fijar_presupuesto", categoria="TOTAL", limite_mensual=10000)
    call(ctx, "registrar_movimiento", monto=300, tipo="gasto", categoria="restaurantes",
         descripcion="tacos")
    status = call(ctx, "estado_del_mes")
    # Día 15 de 30 = 50% del mes; 30% usado -> por debajo del ritmo
    assert status["mes_transcurrido_pct"] == 50.0
    assert status["presupuestos"]["Restaurantes"]["ritmo"] == "por debajo"
    call(ctx, "registrar_movimiento", monto=250, tipo="gasto", categoria="Restaurantes",
         descripcion="cena")
    assert call(ctx, "estado_del_mes")["presupuestos"]["Restaurantes"]["ritmo"] == "a la par"
    assert call(ctx, "estado_del_mes")["presupuestos"]["TOTAL"]["gastado"] == 550


def test_budget_alerts_fire_once(ctx):
    call(ctx, "fijar_presupuesto", categoria="Café y snacks", limite_mensual=500)
    r = call(ctx, "registrar_movimiento", monto=450, tipo="gasto", categoria="Café y snacks",
             descripcion="café")
    assert r["alertas_presupuesto"][0]["umbral"] == 80
    r = call(ctx, "registrar_movimiento", monto=10, tipo="gasto", categoria="Café y snacks",
             descripcion="café")
    assert "alertas_presupuesto" not in r
    r = call(ctx, "registrar_movimiento", monto=100, tipo="gasto", categoria="Café y snacks",
             descripcion="café")
    assert r["alertas_presupuesto"][0]["umbral"] == 100


def test_goals(ctx):
    call(ctx, "fijar_meta", nombre="Emergencia", objetivo=12000, fecha_limite="2027-11-15")
    g = call(ctx, "aportar_meta", nombre="emergencia", monto=2000)["meta"]
    assert g["falta"] == 10000 and g["ahorro_mensual_necesario"] == 833.33


def test_insights_detect_hormiga_and_recurring(ctx):
    db = ctx.db
    for m in (9, 10):
        db.add_transaction(f"2026-{m:02d}-05", 199, "gasto", "Suscripciones", "Netflix mensual")
        for d in range(1, 6):
            db.add_transaction(f"2026-{m:02d}-{d:02d}", 60, "gasto", "Café y snacks", "Café")
    ins = call(ctx, "analizar_gastos", meses=3)
    assert "Café y snacks" in ins["gastos_hormiga"]
    assert any(r["descripcion"].startswith("netflix") for r in ins["posibles_recurrentes"])


def test_month_report(ctx):
    db = ctx.db
    db.add_transaction("2026-10-01", 20000, "ingreso", "Salario", "Nómina")
    db.add_transaction("2026-10-10", 5000, "gasto", "Vivienda", "Renta")
    db.add_transaction("2026-09-10", 4000, "gasto", "Vivienda", "Renta")
    r = call(ctx, "resumen_mensual", anio=2026, mes=10)
    assert r["ahorro"] == 15000 and r["tasa_ahorro_pct"] == 75.0
    assert r["cambios_vs_mes_anterior"]["Vivienda"] == 1000


def test_import_csv_signed_amounts(ctx, tmp_path):
    (tmp_path / "abc123.csv").write_text(
        "Banco XYZ\nFecha;Concepto;Importe\n"
        "03/11/2026;OXXO;-45,50\n04/11/2026;NOMINA;15.000,00\n05/11/2026;UBER;-120,00\n",
        encoding="utf-8")
    prev = call(ctx, "ver_extracto", archivo_id="abc123")
    assert prev["columnas"] == ["Fecha", "Concepto", "Importe"]
    r = call(ctx, "importar_extracto", archivo_id="abc123", columna_fecha=0,
             columna_descripcion=1, columna_monto=2)
    assert r["importados"] == 3 and r["total_errores"] == 0
    # Importar de nuevo no duplica
    r = call(ctx, "importar_extracto", archivo_id="abc123", columna_fecha=0,
             columna_descripcion=1, columna_monto=2)
    assert r["importados"] == 0 and r["duplicados_omitidos"] == 3
    pending = call(ctx, "sin_categorizar")["movimientos"]
    assert len(pending) == 3
    nomina = next(p for p in pending if p["description"] == "NOMINA")
    assert nomina["kind"] == "ingreso" and nomina["amount"] == 15000
    call(ctx, "categorizar", asignaciones=[{"id": p["id"], "categoria": "Otros"} for p in pending])
    assert call(ctx, "sin_categorizar")["cantidad"] == 0


def test_import_debit_credit_columns(ctx, tmp_path):
    (tmp_path / "def456.csv").write_text(
        "Fecha,Descripcion,Cargo,Abono\n2026-11-01,Renta,8000.00,\n2026-11-02,Pago,,\"1,500.50\"\n")
    r = call(ctx, "importar_extracto", archivo_id="def456", columna_fecha=0,
             columna_descripcion=1, columna_cargo=2, columna_abono=3)
    assert r["importados"] == 2
    pago = ctx.db.list_transactions(search="Pago")[0]
    assert pago["kind"] == "ingreso" and pago["amount"] == 1500.5


def test_upload_path_rejects_traversal(ctx):
    with pytest.raises(ValueError):
        run_tool(ctx, "ver_extracto", {"archivo_id": "../secret"})


@pytest.mark.parametrize("raw,expected", [
    ("1,234.56", 1234.56), ("1.234,56", 1234.56), ("-45,5", -45.5), ("(100.00)", -100.0),
    ("$ 2,000", 2000.0), ("350.00 MXN", 350.0), ("abc", None),
])
def test_parse_amount(raw, expected):
    assert importer.parse_amount(raw) == expected


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, content, stop_reason):
        self.content, self.stop_reason = content, stop_reason


class _FakeMessages:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.beta = _Block(messages=_FakeMessages(responses))


def test_agent_runs_tool_loop(ctx):
    import asyncio
    from finanzas.agent import FinanceAgent

    client = _FakeClient([
        _Resp([_Block(type="tool_use", id="t1", name="registrar_movimiento",
                      input={"monto": 250, "tipo": "gasto", "categoria": "Supermercado",
                             "descripcion": "súper"})], "tool_use"),
        _Resp([_Block(type="text", text="Registré 250 en Supermercado.")], "end_turn"),
    ])
    agent = FinanceAgent(ctx, "MXN", client=client)
    answer = asyncio.run(agent.ask(1, "gasté 250 en el súper"))
    assert answer == "Registré 250 en Supermercado."
    assert ctx.db.list_transactions()[0]["amount"] == 250
    second = client.beta.messages.calls[1]["messages"]
    # (la lista es la misma que el historial; el último es la respuesta final)
    assert second[2]["content"][0]["tool_use_id"] == "t1"
    assert "2026-11-15" in second[0]["content"]


def test_agent_rolls_back_on_refusal(ctx):
    import asyncio
    from finanzas.agent import FinanceAgent

    agent = FinanceAgent(ctx, "MXN", client=_FakeClient([_Resp([], "refusal")]))
    asyncio.run(agent.ask(1, "hola"))
    assert agent._history[1] == []
