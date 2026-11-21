"""Lectura de extractos bancarios (CSV / Excel).

El flujo es en dos pasos para que funcione con el formato de cualquier banco:
1. `preview()` lee el archivo y devuelve columnas + filas de muestra; Claude decide
   qué columna es la fecha, la descripción y el monto.
2. `parse()` aplica ese mapeo y devuelve movimientos normalizados.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from pathlib import Path

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%y",
                "%Y/%m/%d", "%d.%m.%Y", "%d %b %Y", "%d-%b-%Y")


def read_rows(path: str | Path) -> list[list[str]]:
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else (v.date().isoformat() if isinstance(v, datetime)
                                           else v.isoformat() if isinstance(v, date) else str(v))
                     for v in row]
            if any(c.strip() for c in cells):
                rows.append(cells)
        return rows

    raw = path.read_bytes()
    for enc in ("utf-8-sig", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return [r for r in csv.reader(io.StringIO(text), delimiter=_guess_delimiter(text))
            if any(c.strip() for c in r)]


def _guess_delimiter(text: str) -> str:
    """Elige el separador que produce más filas con el mismo número (>1) de columnas.

    Más fiable que csv.Sniffer con montos tipo "45,50" en archivos separados por ';'.
    """
    sample = [l for l in text.splitlines()[:50] if l.strip()]
    best, best_score = ",", -1
    for d in (";", ",", "\t", "|"):
        counts: dict[int, int] = {}
        for row in csv.reader(sample, delimiter=d):
            if len(row) > 1:
                counts[len(row)] = counts.get(len(row), 0) + 1
        score = max(counts.values(), default=0)
        if score > best_score:
            best, best_score = d, score
    return best


def preview(path: str | Path, n: int = 8) -> dict:
    rows = read_rows(path)
    # La fila de encabezado suele ser la primera con varias celdas de texto no numérico.
    header_idx = 0
    for i, r in enumerate(rows[:15]):
        texty = sum(1 for c in r if c.strip() and not _looks_numeric(c))
        if texty >= 2:
            header_idx = i
            break
    header = rows[header_idx] if rows else []
    return {
        "fila_encabezado": header_idx,
        "columnas": header,
        "muestra": rows[header_idx + 1: header_idx + 1 + n],
        "total_filas": max(len(rows) - header_idx - 1, 0),
    }


def _looks_numeric(s: str) -> bool:
    return parse_amount(s) is not None


def parse_amount(s: str) -> float | None:
    s = (s or "").strip().replace("$", "").replace("€", "").replace(" ", "")
    s = re.sub(r"[A-Za-z]{3}$", "", s)  # MXN, EUR, USD al final
    if not s:
        return None
    negative = s.startswith("-") or (s.startswith("(") and s.endswith(")")) or s.endswith("-")
    s = s.strip("()-+")
    # Formato europeo 1.234,56 vs americano 1,234.56
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        s = s.replace(",", ".") if len(parts[-1]) in (1, 2) else s.replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if negative else v


def parse_date(s: str, fmt: str | None = None) -> str | None:
    s = (s or "").strip()
    if not s:
        return None
    s = s.split(" ")[0] if re.match(r"^\d{4}-\d{2}-\d{2}[ T]", s) else s
    s = s.split("T")[0]
    for f in ([fmt] if fmt else []) + list(DATE_FORMATS):
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return None


def parse(
    path: str | Path,
    date_column: int,
    description_column: int,
    amount_column: int | None = None,
    debit_column: int | None = None,
    credit_column: int | None = None,
    date_format: str | None = None,
    negative_is_expense: bool = True,
    header_row: int | None = None,
) -> tuple[list[dict], list[str]]:
    """Devuelve (movimientos, errores). Columnas indexadas desde 0."""
    if amount_column is None and debit_column is None and credit_column is None:
        raise ValueError("Indica amount_column o debit_column/credit_column")
    rows = read_rows(path)
    if header_row is None:
        header_row = preview(path)["fila_encabezado"]
    out, errors = [], []

    def cell(r: list[str], i: int | None) -> str:
        return r[i] if i is not None and i < len(r) else ""

    for n, r in enumerate(rows[header_row + 1:], start=header_row + 2):
        d = parse_date(cell(r, date_column), date_format)
        if not d:
            errors.append(f"Fila {n}: fecha no reconocida ({cell(r, date_column)!r})")
            continue
        desc = cell(r, description_column).strip()
        if amount_column is not None:
            amt = parse_amount(cell(r, amount_column))
            if amt is None or amt == 0:
                errors.append(f"Fila {n}: monto no reconocido ({cell(r, amount_column)!r})")
                continue
            is_expense = (amt < 0) == negative_is_expense
        else:
            debit = parse_amount(cell(r, debit_column)) or 0.0
            credit = parse_amount(cell(r, credit_column)) or 0.0
            if debit:
                amt, is_expense = debit, True
            elif credit:
                amt, is_expense = credit, False
            else:
                errors.append(f"Fila {n}: sin cargo ni abono")
                continue
        out.append({"date": d, "amount": abs(amt), "kind": "gasto" if is_expense else "ingreso",
                    "description": desc})
    return out, errors
