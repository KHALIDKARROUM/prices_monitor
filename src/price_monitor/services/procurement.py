from __future__ import annotations

from typing import Any

from ..database import DatabaseBackend


REGIONS = {
    "all": "All regions",
    "europe": "Europe",
    "africa_middle_east": "Africa & Middle East",
    "asia_pacific": "Asia-Pacific",
    "americas": "Americas",
    "local": "Local distributor",
}

COMMON_STEPS = [
    {
        "number": 1,
        "title": "Define technical requirements",
        "detail": "Specify the standard, grade, quantity, packaging, destination, and delivery window.",
    },
    {
        "number": 2,
        "title": "Send the same RFQ to at least three sellers",
        "detail": "Request price, currency, validity, minimum volume, origin, loading point, lead time, and Incoterm 2020.",
    },
    {
        "number": 3,
        "title": "Verify each counterparty",
        "detail": "Check business registration, licenses, beneficial ownership, sanctions, references, and bank details through a second channel.",
    },
    {
        "number": 4,
        "title": "Compare landed costs",
        "detail": "Include merchandise, freight, insurance, inspection, duties, taxes, exchange, storage, and delay or heating costs.",
    },
    {
        "number": 5,
        "title": "Agree on quality, payment, and claims",
        "detail": "Include specifications, quantity tolerance, inspection, documents, schedule, governing law, and the claims process.",
    },
    {
        "number": 6,
        "title": "Pay and receive with checks",
        "detail": "Prefer a letter of credit or approved credit; reconcile weight, certificate of analysis, and inspection before final payment.",
    },
]

PRODUCT_GUIDANCE = {
    "gasoil": {
        "title": "Buy wholesale diesel",
        "specification": "EN 590, 10 ppm sulfur, cetane, density, flash point, cloud point, and FAME content as required locally.",
        "logistics": "For road tankers: ex-rack/FCA or DAP. For cargoes: FOB, CFR, or CIF with a confirmed receiving terminal.",
        "rfq_fields": [
            "Product and standard (e.g. EN 590 10 ppm)", "Quantity and tolerance", "Destination port/depot",
            "Delivery date or window", "Requested Incoterm 2020", "Currency and pricing formula",
            "Certificate of analysis, SDS, and origin", "Payment terms and offer validity",
        ],
        "warning": (
            "DDFUELNYH is a FOB New York Harbor spot price in USD/gallon. "
            "It is a negotiation benchmark, not the landed price in your country."
        ),
    },
    "bitume": {
        "title": "Buy bitumen",
        "specification": "Choose the grade (e.g. 35/50, 50/70, or 70/100), EN 12591/ASTM D946, modified bitumen or emulsion, and end use.",
        "logistics": "Bulk shipments require heated tankers and storage. For small volumes or distant imports, compare drums, big bags, or containers, including melting costs and losses.",
        "rfq_fields": [
            "Grade, standard, and road/industrial use", "Quantity and packaging", "Origin and refinery",
            "Loading point and destination", "Loading/delivery temperature", "Requested Incoterm 2020",
            "Product data sheet, SDS, analysis certificate, and inspection method", "Freight, demurrage, heating, and offer validity",
        ],
        "warning": (
            "There is no free, executable global spot price for bitumen. WPU058 is a monthly U.S. trend index; "
            "the USD/tonne price must come from a comparable supplier quote by grade, location, and Incoterm."
        ),
    },
}


def build_procurement_guide(
    database: DatabaseBackend, product: str = "gasoil", region: str = "all"
) -> dict[str, Any]:
    product = product.strip().lower()
    if product not in PRODUCT_GUIDANCE:
        raise ValueError("Unknown product. Use gasoil or bitume.")
    region = region.strip().lower() or "all"
    if region not in REGIONS:
        raise ValueError("Unknown region.")

    latest_prices = [row for row in database.latest() if row.get("product") == product]
    latest_quote = latest_prices[0] if latest_prices else None
    benchmarks = database.latest_benchmarks(product=product)
    guidance = PRODUCT_GUIDANCE[product]
    return {
        "product": product,
        "region": region,
        "region_label": REGIONS[region],
        "title": guidance["title"],
        "market_context": {
            "latest_price": latest_quote,
            "benchmarks": benchmarks,
            "warning": guidance["warning"],
        },
        "suppliers": database.supplier_channels(product=product, region=region),
        "steps": COMMON_STEPS,
        "specification": guidance["specification"],
        "logistics": guidance["logistics"],
        "rfq_fields": guidance["rfq_fields"],
        "landed_cost_formula": (
            "landed cost = merchandise + freight + insurance + inspection + duties/taxes "
            "+ storage/heating + financing costs ± currency exchange"
        ),
        "disclaimer": (
            "The listed companies provide official contact channels, not an endorsement or guarantee of sale. "
            "Check availability, licenses, sanctions, quality, taxes, and contract terms in your jurisdiction."
        ),
    }
