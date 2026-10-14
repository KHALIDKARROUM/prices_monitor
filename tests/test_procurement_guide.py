from __future__ import annotations

from price_monitor.services.procurement import build_procurement_guide


def test_procurement_guide_combines_sql_suppliers_benchmark_and_buying_steps(empty_database):
    empty_database.insert_benchmark(
        {
            "code": "bls_wpu058",
            "product": "bitume",
            "label": "Indice PPI asphalte",
            "value": 546.938,
            "unit": "indice (déc. 1984 = 100)",
            "geography": "États-Unis",
            "source": "U.S. BLS Public Data API - WPU058",
            "source_date": "2026-08-01",
        }
    )

    guide = build_procurement_guide(empty_database, "bitume", "europe")

    assert guide["product"] == "bitume"
    assert guide["market_context"]["benchmarks"][0]["code"] == "bls_wpu058"
    assert {supplier["name"] for supplier in guide["suppliers"]} >= {
        "Shell Bitumen",
        "TotalEnergies Bitumen",
        "Nynas Bitumen",
    }
    assert len(guide["steps"]) == 6
    assert "landed cost" in guide["landed_cost_formula"]
    assert "no free, executable global spot price" in guide["market_context"]["warning"]


def test_existing_french_supplier_seed_is_displayed_in_english(empty_database):
    with empty_database.connect() as conn:
        conn.execute(
            "UPDATE supplier_channels SET name=?, coverage=? WHERE name=?",
            ("TotalEnergies - réseau entreprises", "Réseau international", "TotalEnergies - business network"),
        )

    guide = build_procurement_guide(empty_database, "gasoil")
    supplier = next(row for row in guide["suppliers"] if row["name"] == "TotalEnergies - business network")
    assert supplier["coverage"].startswith("International network")
