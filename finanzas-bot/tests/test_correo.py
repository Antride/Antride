import asyncio
import io
import json
from datetime import date

import pytest

from finanzas.db import FinanceDB
from finanzas.email_sync import EXTRACTION_SCHEMA, apply_extraction, sync_mailbox
from finanzas.gmail_client import EmailMessage, html_to_text, unlock_pdf


def make_email(id="m1", subject="Compra con tu tarjeta", body="Compra por $450.00 en AMAZON"):
    return EmailMessage(id=id, sender="Banco <alertas@banco.mx>", subject=subject,
                        date="Fri, 20 Nov 2026 10:00:00 -0600", internal_date="2026-11-20",
                        body=body)


def notif(monto=450.0, comercio="Amazon", fecha="2026-11-20"):
    return {"tipo": "notificacion_movimiento", "institucion": "Banco", "cuenta": "Crédito 1234",
            "movimientos": [{"fecha": fecha, "monto": monto, "tipo": "gasto",
                             "comercio": comercio, "categoria": "Compras en línea"}],
            "estado_de_cuenta": None}


@pytest.fixture
def db():
    return FinanceDB(":memory:")


def test_schema_is_strict_everywhere():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(EXTRACTION_SCHEMA)


def test_notification_is_recorded(db):
    ev = apply_extraction(db, make_email(), notif())
    assert len(ev.added) == 1
    tx = db.list_transactions()[0]
    assert tx["source"] == "correo" and tx["amount"] == 450
    assert tx["category"] == "Compras en línea" and "Crédito 1234" in tx["description"]


def test_other_emails_are_ignored(db):
    ev = apply_extraction(db, make_email(), {"tipo": "otro", "institucion": None, "cuenta": None,
                                             "movimientos": [], "estado_de_cuenta": None})
    assert ev.kind == "otro" and db.list_transactions() == []


def test_manual_entry_and_notification_not_duplicated(db):
    db.add_transaction("2026-11-19", 450, "gasto", "Compras en línea", "amazon")
    ev = apply_extraction(db, make_email(), notif())
    assert ev.added == [] and ev.skipped_duplicates == 1


def test_statement_dedupes_against_notifications_one_to_one(db):
    apply_extraction(db, make_email("n1"), notif(60, "Starbucks", "2026-11-03"))
    statement = {
        "tipo": "estado_de_cuenta", "institucion": "Banco", "cuenta": "Crédito 1234",
        "movimientos": [
            {"fecha": "2026-11-03", "monto": 60, "tipo": "gasto", "comercio": "Starbucks",
             "categoria": "Café y snacks"},
            # Segundo café igual el mismo día: NO es duplicado
            {"fecha": "2026-11-03", "monto": 60, "tipo": "gasto", "comercio": "Starbucks",
             "categoria": "Café y snacks"},
            {"fecha": "2026-11-10", "monto": 1200, "tipo": "gasto", "comercio": "Liverpool",
             "categoria": "Ropa"},
            {"fecha": "2026-11-12", "monto": 300, "tipo": "ingreso", "comercio": "Devolución",
             "categoria": "Reembolsos"},
            {"fecha": "mal", "monto": 1, "tipo": "gasto", "comercio": "x", "categoria": "x"},
        ],
        "estado_de_cuenta": {"fecha_corte": "2026-11-15", "fecha_limite_pago": "2026-12-05",
                             "saldo": 5000.0, "pago_minimo": 350.0,
                             "pago_para_no_generar_intereses": 4800.0},
    }
    ev = apply_extraction(db, make_email("s1", "Tu estado de cuenta"), statement)
    assert ev.skipped_duplicates == 1 and len(ev.added) == 3
    assert len(db.list_transactions(search="Starbucks")) == 2
    st = db.statements()[0]
    assert st["due_date"] == "2026-12-05" and st["no_interest_payment"] == 4800


def test_payment_reminder_fires_once(db):
    db.add_statement("Banco", "Crédito 1234", "2026-11-15", "2026-12-05", 5000, 350, 4800, "s1")
    assert db.due_payment_reminders(date(2026, 11, 30)) == []
    due = db.due_payment_reminders(date(2026, 12, 3))
    assert len(due) == 1
    db.mark_statement_reminded(due[0]["id"])
    assert db.due_payment_reminders(date(2026, 12, 4)) == []


def test_html_to_text():
    raw = ("<html><head><style>.x{}</style></head><body><table><tr><td>Monto</td>"
           "<td>$1,250.00</td></tr><tr><td>Comercio</td><td>OXXO&nbsp;CENTRO</td></tr>"
           "</table><script>x()</script></body></html>")
    text = html_to_text(raw)
    assert "$1,250.00" in text and "OXXO CENTRO" in text and "x()" not in text


def test_unlock_pdf_with_password():
    from pypdf import PdfReader, PdfWriter

    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.encrypt("ABCD800101")
    buf = io.BytesIO()
    w.write(buf)
    assert unlock_pdf(buf.getvalue(), ["otra"]) is None
    unlocked = unlock_pdf(buf.getvalue(), ["otra", "ABCD800101"])
    assert not PdfReader(io.BytesIO(unlocked)).is_encrypted


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeGmail:
    def __init__(self, emails):
        self.emails = {e.id: e for e in emails}
        self.queries = []

    def search(self, q, max_results):
        self.queries.append(q)
        return list(self.emails)

    def get(self, msg_id, passwords):
        return self.emails[msg_id]


class FakeClient:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

        async def create(**kw):
            self.calls.append(kw)
            data = self.outputs[len(self.calls) - 1]
            return _Obj(stop_reason="end_turn",
                        content=[_Obj(type="text", text=json.dumps(data))])
        self.beta = _Obj(messages=_Obj(create=create))


def test_sync_mailbox_processes_each_email_once(db):
    gmail = FakeGmail([make_email("a"), make_email("b", "Oferta", "50% de descuento")])
    other = {"tipo": "otro", "institucion": None, "cuenta": None, "movimientos": [],
             "estado_de_cuenta": None}
    client = FakeClient([other, notif(), other, notif()])
    events = asyncio.run(sync_mailbox(db, gmail, client, []))
    assert len(events) == 1 and len(db.list_transactions()) == 1
    assert "newer_than:60d" in gmail.queries[0]
    # La segunda vez no vuelve a llamar a Claude por los mismos correos
    asyncio.run(sync_mailbox(db, gmail, client, []))
    assert len(client.calls) == 2
    assert "newer_than:4d" in gmail.queries[1]
    # El contenido del correo va delimitado como no confiable
    text = client.calls[0]["messages"][0]["content"][-1]["text"]
    assert text.startswith("<correo>") and text.endswith("</correo>")
    assert client.calls[0]["output_config"]["format"]["type"] == "json_schema"
