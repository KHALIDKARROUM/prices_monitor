"""Metadata shared by collection, history and forecasts of the BLS index."""

INDEX_CODE = "bls_wpu058"
INDEX_UNIT = "indice (déc. 1984 = 100)"
INDEX_LABEL = "Bitumen · BLS index"
INDEX_SOURCE = "U.S. BLS Public Data API - WPU058"
INDEX_MIRROR = "BLS/FRED - WPU058"


def benchmark_payload(source_date: str, value: float, *, mirror: bool = False) -> dict:
    return {
        "code": INDEX_CODE, "product": "bitume",
        "label": "Asphalt and petroleum products PPI (bitumen proxy)",
        "value": value, "unit": INDEX_UNIT, "geography": "United States",
        "source": INDEX_MIRROR if mirror else INDEX_SOURCE,
        "source_code": "bls_asphalt_ppi", "source_date": source_date,
        "source_url": "https://fred.stlouisfed.org/series/WPU058",
        "notes": (
            "Monthly trend indicator. This is neither an international USD/tonne "
            "price nor an executable purchase offer."
        ),
    }
