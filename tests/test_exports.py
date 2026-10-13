from __future__ import annotations

import csv
import io

from openpyxl import load_workbook

from price_monitor.services.export import FIELDS, csv_bytes, xlsx_bytes


def sample_row() -> dict:
    return {
        "id": 7,
        "product": "bitume",
        "price": 528.5,
        "unit": "USD/tonne",
        "currency": "USD",
        "source": "Devis Fournisseur ABC",
        "supplier": "Fournisseur ABC",
        "source_date": "2026-09-08",
        "collected_at": "2026-09-08T12:00:00+00:00",
        "variation": 2.5,
        "variation_pct": 0.47,
        "is_unchanged": 0,
        "notes": "FOB Méditerranée",
    }


def test_csv_export_has_utf8_bom_headers_and_all_values():
    body = csv_bytes([sample_row()])

    assert body.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))

    assert rows[0]["product"] == "bitume"
    assert rows[0]["price"] == "528.5"
    assert rows[0]["notes"] == "FOB Méditerranée"
    assert tuple(rows[0]) == FIELDS


def test_excel_export_creates_readable_workbook_with_frozen_header():
    body = xlsx_bytes([sample_row()])
    workbook = load_workbook(io.BytesIO(body), read_only=False)
    sheet = workbook["Relevés"]

    assert sheet.freeze_panes == "A2"
    assert [cell.value for cell in sheet[1]] == [
        "ID",
        "Produit",
        "Prix / indice",
        "Unité",
        "Devise",
        "Source",
        "Fournisseur",
        "Date source",
        "Date collecte",
        "Variation",
        "Variation %",
        "Inchangée",
        "Remarque",
        "Type de donnée",
        "Code indicateur",
        "Instrument",
    ]
    assert [cell.value for cell in sheet[2]] == list(sample_row().values()) + [None, None, None]
    assert sheet[1][0].font.bold is True
