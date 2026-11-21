"""Capa de datos: SQLite con movimientos, presupuestos, metas y alertas."""

from __future__ import annotations

import calendar
import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,              -- YYYY-MM-DD
    amount      REAL NOT NULL CHECK (amount > 0),
    kind        TEXT NOT NULL CHECK (kind IN ('gasto', 'ingreso')),
    category    TEXT NOT NULL DEFAULT 'Sin categoría',
    description TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'manual',  -- manual | importado
    fingerprint TEXT UNIQUE                -- evita duplicados al importar
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);

CREATE TABLE IF NOT EXISTS budgets (
    category      TEXT PRIMARY KEY,
    monthly_limit REAL NOT NULL CHECK (monthly_limit > 0)
);

CREATE TABLE IF NOT EXISTS goals (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL UNIQUE,
    target   REAL NOT NULL CHECK (target > 0),
    saved    REAL NOT NULL DEFAULT 0,
    deadline TEXT                          -- YYYY-MM-DD opcional
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Para no repetir la misma alerta varias veces en un mes.
CREATE TABLE IF NOT EXISTS alerts_sent (
    month     TEXT NOT NULL,               -- YYYY-MM
    category  TEXT NOT NULL,
    threshold INTEGER NOT NULL,            -- 80, 100
    PRIMARY KEY (month, category, threshold)
);
"""

TOTAL = "TOTAL"
ALERT_THRESHOLDS = (80, 100)
# Margen (en puntos porcentuales) para considerar que vas "a la par".
PACE_TOLERANCE = 10


def month_bounds(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def normalize_category(category: str | None) -> str:
    category = (category or "").strip()
    return category[:1].upper() + category[1:] if category else "Sin categoría"


class FinanceDB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    @contextmanager
    def _tx(self):
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------ movimientos
    def add_transaction(
        self,
        tx_date: str,
        amount: float,
        kind: str,
        category: str | None,
        description: str = "",
        source: str = "manual",
        dedupe: bool = False,
    ) -> int | None:
        """Inserta un movimiento. Con dedupe=True devuelve None si ya existía."""
        date.fromisoformat(tx_date)  # valida formato
        amount = round(abs(float(amount)), 2)
        if amount == 0:
            raise ValueError("El monto no puede ser 0")
        if kind not in ("gasto", "ingreso"):
            raise ValueError("kind debe ser 'gasto' o 'ingreso'")
        fingerprint = None
        if dedupe:
            raw = f"{tx_date}|{amount:.2f}|{kind}|{description.strip().lower()}"
            fingerprint = hashlib.sha1(raw.encode()).hexdigest()
        with self._tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO transactions"
                " (date, amount, kind, category, description, source, fingerprint)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tx_date, amount, kind, normalize_category(category),
                 description.strip(), source, fingerprint),
            )
            return cur.lastrowid if cur.rowcount else None

    def delete_transaction(self, tx_id: int) -> bool:
        with self._tx() as c:
            return c.execute("DELETE FROM transactions WHERE id = ?", (tx_id,)).rowcount > 0

    def update_transaction(self, tx_id: int, **fields) -> bool:
        allowed = {"date", "amount", "kind", "category", "description"}
        fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not fields:
            return False
        if "category" in fields:
            fields["category"] = normalize_category(fields["category"])
        if "amount" in fields:
            fields["amount"] = round(abs(float(fields["amount"])), 2)
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._tx() as c:
            return c.execute(
                f"UPDATE transactions SET {sets} WHERE id = ?", (*fields.values(), tx_id)
            ).rowcount > 0

    def list_transactions(
        self,
        start: str | None = None,
        end: str | None = None,
        category: str | None = None,
        kind: str | None = None,
        search: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        q = "SELECT * FROM transactions WHERE 1=1"
        args: list = []
        if start:
            q += " AND date >= ?"
            args.append(start)
        if end:
            q += " AND date <= ?"
            args.append(end)
        if category:
            q += " AND category = ? COLLATE NOCASE"
            args.append(category)
        if kind:
            q += " AND kind = ?"
            args.append(kind)
        if search:
            q += " AND description LIKE ?"
            args.append(f"%{search}%")
        q += " ORDER BY date DESC, id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self._conn.execute(q, args)]

    def uncategorized(self, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, date, amount, kind, description FROM transactions"
            " WHERE category = 'Sin categoría' ORDER BY date LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def set_categories(self, assignments: list[dict]) -> int:
        with self._tx() as c:
            n = 0
            for a in assignments:
                n += c.execute(
                    "UPDATE transactions SET category = ? WHERE id = ?",
                    (normalize_category(a["category"]), int(a["id"])),
                ).rowcount
            return n

    def categories(self) -> list[str]:
        rows = self._conn.execute("SELECT DISTINCT category FROM transactions ORDER BY 1")
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ presupuestos
    def set_budget(self, category: str, monthly_limit: float) -> None:
        cat = TOTAL if category.strip().upper() == TOTAL else normalize_category(category)
        with self._tx() as c:
            c.execute(
                "INSERT INTO budgets (category, monthly_limit) VALUES (?, ?)"
                " ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit",
                (cat, round(float(monthly_limit), 2)),
            )

    def delete_budget(self, category: str) -> bool:
        with self._tx() as c:
            return c.execute(
                "DELETE FROM budgets WHERE category = ? COLLATE NOCASE", (category,)
            ).rowcount > 0

    def budgets(self) -> dict[str, float]:
        return {r[0]: r[1] for r in self._conn.execute("SELECT category, monthly_limit FROM budgets")}

    # ------------------------------------------------------------------ metas
    def set_goal(self, name: str, target: float, deadline: str | None = None,
                 saved: float | None = None) -> None:
        if deadline:
            date.fromisoformat(deadline)
        with self._tx() as c:
            c.execute(
                "INSERT INTO goals (name, target, saved, deadline) VALUES (?, ?, COALESCE(?, 0), ?)"
                " ON CONFLICT(name) DO UPDATE SET target = excluded.target,"
                " deadline = COALESCE(excluded.deadline, goals.deadline),"
                " saved = COALESCE(?, goals.saved)",
                (name.strip(), float(target), saved, deadline, saved),
            )

    def contribute_goal(self, name: str, amount: float) -> dict | None:
        with self._tx() as c:
            if not c.execute(
                "UPDATE goals SET saved = ROUND(saved + ?, 2) WHERE name = ? COLLATE NOCASE",
                (float(amount), name.strip()),
            ).rowcount:
                return None
        return next((g for g in self.goals() if g["name"].lower() == name.strip().lower()), None)

    def delete_goal(self, name: str) -> bool:
        with self._tx() as c:
            return c.execute(
                "DELETE FROM goals WHERE name = ? COLLATE NOCASE", (name.strip(),)
            ).rowcount > 0

    def goals(self, today: date | None = None) -> list[dict]:
        today = today or date.today()
        out = []
        for r in self._conn.execute("SELECT * FROM goals ORDER BY id"):
            g = dict(r)
            remaining = max(g["target"] - g["saved"], 0)
            g["progreso_pct"] = round(100 * g["saved"] / g["target"], 1)
            g["falta"] = round(remaining, 2)
            if g["deadline"] and remaining > 0:
                d = date.fromisoformat(g["deadline"])
                months = max((d.year - today.year) * 12 + d.month - today.month, 1)
                g["ahorro_mensual_necesario"] = round(remaining / months, 2)
            out.append(g)
        return out

    # ------------------------------------------------------------------ ajustes
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        r = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return r[0] if r else default

    def set_setting(self, key: str, value: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def all_settings(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self._conn.execute("SELECT key, value FROM settings")}

    # ------------------------------------------------------------------ análisis
    def _totals_by_category(self, start: date, end: date, kind: str = "gasto") -> dict[str, float]:
        rows = self._conn.execute(
            "SELECT category, ROUND(SUM(amount), 2) FROM transactions"
            " WHERE kind = ? AND date BETWEEN ? AND ? GROUP BY category ORDER BY 2 DESC",
            (kind, start.isoformat(), end.isoformat()),
        )
        return {r[0]: r[1] for r in rows}

    def month_status(self, today: date) -> dict:
        """Estado del mes en curso: gastado vs presupuesto y ritmo (sobre / a la par / debajo)."""
        start, end = month_bounds(today.year, today.month)
        days_in_month = end.day
        elapsed_pct = round(100 * today.day / days_in_month, 1)

        spent = self._totals_by_category(start, today)
        income = sum(self._totals_by_category(start, today, "ingreso").values())
        total_spent = round(sum(spent.values()), 2)
        budgets = self.budgets()

        def pace(used: float, limit: float) -> dict:
            used_pct = round(100 * used / limit, 1)
            if used_pct > elapsed_pct + PACE_TOLERANCE:
                status = "por encima"
            elif used_pct < elapsed_pct - PACE_TOLERANCE:
                status = "por debajo"
            else:
                status = "a la par"
            projected = round(used / today.day * days_in_month, 2)
            return {
                "gastado": round(used, 2),
                "presupuesto": limit,
                "usado_pct": used_pct,
                "restante": round(limit - used, 2),
                "ritmo": status,
                "proyeccion_fin_de_mes": projected,
                "disponible_por_dia": round(max(limit - used, 0) / (days_in_month - today.day + 1), 2),
            }

        by_budget = {
            cat: pace(total_spent if cat == TOTAL else spent.get(cat, 0.0), limit)
            for cat, limit in budgets.items()
        }
        monthly_income = self.get_setting("ingreso_mensual")
        return {
            "mes": start.strftime("%Y-%m"),
            "dia": today.day,
            "dias_del_mes": days_in_month,
            "mes_transcurrido_pct": elapsed_pct,
            "ingresos_registrados": round(income, 2),
            "ingreso_mensual_declarado": float(monthly_income) if monthly_income else None,
            "gasto_total": total_spent,
            "gasto_por_categoria": spent,
            "presupuestos": by_budget,
            "categorias_sin_presupuesto": [c for c in spent if c not in budgets],
        }

    def month_report(self, year: int, month: int) -> dict:
        """Resumen de un mes completo + comparación con el mes anterior."""
        start, end = month_bounds(year, month)
        py, pm = prev_month(year, month)
        pstart, pend = month_bounds(py, pm)
        spent = self._totals_by_category(start, end)
        prev = self._totals_by_category(pstart, pend)
        income = round(sum(self._totals_by_category(start, end, "ingreso").values()), 2)
        total = round(sum(spent.values()), 2)
        budgets = self.budgets()
        return {
            "mes": start.strftime("%Y-%m"),
            "ingresos": income,
            "gastos": total,
            "ahorro": round(income - total, 2),
            "tasa_ahorro_pct": round(100 * (income - total) / income, 1) if income else None,
            "gasto_por_categoria": spent,
            "mes_anterior": {"gastos": round(sum(prev.values()), 2), "por_categoria": prev},
            "cambios_vs_mes_anterior": {
                c: round(spent.get(c, 0) - prev.get(c, 0), 2)
                for c in sorted(set(spent) | set(prev))
            },
            "presupuestos": {
                c: {"limite": lim,
                    "gastado": total if c == TOTAL else spent.get(c, 0.0),
                    "excedido": (total if c == TOTAL else spent.get(c, 0.0)) > lim}
                for c, lim in budgets.items()
            },
        }

    def spending_insights(self, today: date, months: int = 3) -> dict:
        """Detecta fugas de dinero: gastos hormiga, recurrentes y categorías que crecen."""
        start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        for _ in range(months - 1):
            start = (start - timedelta(days=1)).replace(day=1)
        rows = self.list_transactions(start=start.isoformat(), end=today.isoformat(),
                                      kind="gasto", limit=100_000)
        total = sum(r["amount"] for r in rows) or 1.0

        # Gastos hormiga: compras pequeñas pero frecuentes por categoría.
        small_threshold = float(self.get_setting("umbral_gasto_hormiga", "150"))
        hormiga: dict[str, dict] = {}
        for r in rows:
            if r["amount"] <= small_threshold:
                h = hormiga.setdefault(r["category"], {"veces": 0, "total": 0.0})
                h["veces"] += 1
                h["total"] = round(h["total"] + r["amount"], 2)
        hormiga = {k: v for k, v in sorted(hormiga.items(), key=lambda kv: -kv[1]["total"])
                   if v["veces"] >= 4}

        # Recurrentes: misma descripción en >= 2 meses distintos (suscripciones, etc.).
        seen: dict[str, dict] = {}
        for r in rows:
            key = " ".join(r["description"].lower().split()[:3])
            if not key:
                continue
            s = seen.setdefault(key, {"meses": set(), "montos": [], "categoria": r["category"]})
            s["meses"].add(r["date"][:7])
            s["montos"].append(r["amount"])
        recurring = [
            {"descripcion": k, "categoria": v["categoria"], "meses": len(v["meses"]),
             "monto_promedio": round(sum(v["montos"]) / len(v["montos"]), 2)}
            for k, v in seen.items() if len(v["meses"]) >= 2
        ]
        recurring.sort(key=lambda x: -x["monto_promedio"])

        by_cat: dict[str, float] = {}
        for r in rows:
            by_cat[r["category"]] = by_cat.get(r["category"], 0) + r["amount"]
        share = {c: round(100 * v / total, 1)
                 for c, v in sorted(by_cat.items(), key=lambda kv: -kv[1])}

        biggest = sorted(rows, key=lambda r: -r["amount"])[:10]
        return {
            "periodo": f"{start.isoformat()} a {today.isoformat()}",
            "gasto_total": round(sum(r["amount"] for r in rows), 2),
            "porcentaje_por_categoria": share,
            "gastos_hormiga": hormiga,
            "umbral_gasto_hormiga": small_threshold,
            "posibles_recurrentes": recurring[:20],
            "gastos_mas_grandes": [
                {k: r[k] for k in ("id", "date", "amount", "category", "description")}
                for r in biggest
            ],
        }

    # ------------------------------------------------------------------ alertas
    def pending_alerts(self, today: date) -> list[dict]:
        """Alertas de presupuesto (80% / 100%) que todavía no se han enviado este mes."""
        status = self.month_status(today)
        month = status["mes"]
        out = []
        for cat, info in status["presupuestos"].items():
            for th in ALERT_THRESHOLDS:
                if info["usado_pct"] >= th:
                    exists = self._conn.execute(
                        "SELECT 1 FROM alerts_sent WHERE month=? AND category=? AND threshold=?",
                        (month, cat, th),
                    ).fetchone()
                    if not exists:
                        out.append({"categoria": cat, "umbral": th, **info})
        return out

    def mark_alert_sent(self, month: str, category: str, threshold: int) -> None:
        with self._tx() as c:
            c.execute("INSERT OR IGNORE INTO alerts_sent VALUES (?, ?, ?)",
                      (month, category, threshold))
