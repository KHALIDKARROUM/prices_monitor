"""Structured commercial quotes, equivalent-offer checks and landed costs."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import unicodedata

UNITS = {"gasoil": "gallon", "brent": "baril", "bitume": "tonne"}
INCOTERMS = ("EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP")
DOCUMENT_TYPES = {"quotation": "Quotation", "proforma": "Pro forma invoice", "invoice": "Invoice",
                  "purchase_order": "Purchase order", "certificate": "Certificate of analysis",
                  "safety_data": "Safety data sheet", "technical_data": "Technical data sheet", "other": "Other supporting document"}
PACKAGING = {"bulk": "Bulk", "drums": "Drums", "big_bags": "Big bags", "container": "Container", "other": "Other"}
COST_FIELDS = {"transport_cost": "Transport", "insurance_cost": "Insurance", "inspection_cost": "Inspection",
               "duties_cost": "Customs duties", "taxes_cost": "Taxes", "storage_cost": "Storage",
               "heating_cost": "Heating", "finance_cost": "Financing costs"}
EXTRA_COST_FIELDS = tuple(key for key in COST_FIELDS if key != "transport_cost")
QUOTE_TEXT_FIELDS = {"supplier": 150, "reference": 100, "grade": 100, "specification": 150, "origin": 100,
                     "incoterm_place": 200, "destination": 200, "packaging_detail": 150, "payment_terms": 200, "notes": 2000}
EQUIVALENCE_FIELDS = {"product": "Product", "grade": "Grade", "specification": "Standard / specification",
                      "origin": "Origin", "incoterm": "Incoterm", "incoterm_place": "Incoterm location",
                      "destination": "Destination", "quantity": "Quantity", "unit": "Unit",
                      "packaging": "Packaging", "packaging_detail": "Packaging details",
                      "delivery_date": "Delivery date", "payment_terms": "Payment terms",
                      "document_type": "Commercial document type"}


class QuoteNotFound(ValueError):
    pass


class QuoteConflict(ValueError):
    pass


def text(value, label: str, maximum: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = unicodedata.normalize("NFC", value).strip()
    if (required and not value) or len(value) > maximum or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError(f"{label} is required and limited to {maximum} characters." if required else f"{label} is limited to {maximum} characters.")
    return value


def number(value, label: str, *, positive: bool = False, places: int = 6) -> Decimal:
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label} must be numeric.") from None
    if not result.is_finite() or result < 0 or (positive and result == 0) or result > Decimal("1000000000000"):
        raise ValueError(f"{label} must be {'greater than zero' if positive else 'non-negative'} and less than 1 trillion.")
    result = result.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if positive and result == 0:
        raise ValueError(f"{label} is too small for calculation precision.")
    return result


def rounded(value: Decimal) -> float:
    return float(value.quantize(Decimal(".000001"), rounding=ROUND_HALF_UP))


def quote_date(value, label: str) -> str:
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError()
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"{label} must be a valid date in YYYY-MM-DD format.") from None


def landed_costs(payload: dict) -> dict:
    quantity = number(payload.get("quantity"), "Quantity", positive=True)
    price = number(payload.get("unit_price"), "Unit price", positive=True)
    currency = str(payload.get("currency") or "USD").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("Currency must be a three-letter code.")
    rate_input = payload.get("exchange_rate")
    if rate_input in (None, "") and currency == "USD":
        rate_input = 1
    rate = number(rate_input, "Exchange rate", positive=True, places=8)
    if currency == "USD" and rate != 1:
        raise ValueError("The USD-to-USD exchange rate must equal 1.")
    if rate > Decimal("1000000"):
        raise ValueError("The exchange rate is too high.")
    costs = {key: number(payload.get(key) if payload.get(key) not in (None, "") else 0, label) for key, label in COST_FIELDS.items()}
    other = payload.get("other_costs", [])
    if not isinstance(other, list) or len(other) > 20:
        raise ValueError("Other costs may contain at most 20 items.")
    items = []
    for item in other:
        if not isinstance(item, dict):
            raise ValueError("Invalid cost item.")
        items.append({"label": text(item.get("label"), "Cost item label", 100, True),
                      "amount": rounded(number(item.get("amount"), "Cost item amount"))})
    other = items
    merchandise = quantity * price
    extras = sum(costs.values()) + sum(Decimal(str(item["amount"])) for item in other)
    total = merchandise + extras
    if total > Decimal("999999999999") or total * rate > Decimal("999999999999"):
        raise ValueError("Total cost exceeds calculation capacity.")
    breakdown = [{"key": "merchandise", "label": "Merchandise", "amount": rounded(merchandise)}]
    breakdown += [{"key": key, "label": label, "amount": rounded(costs[key])} for key, label in COST_FIELDS.items()]
    breakdown += [{"key": "other", **item} for item in other]
    for item in breakdown:
        item["amount_usd"] = rounded(Decimal(str(item["amount"])) * rate)
    return {"quantity": rounded(quantity), "unit_price": rounded(price), "currency": currency, "exchange_rate": float(rate),
            **{key: rounded(value) for key, value in costs.items()}, "other_costs": other,
            "merchandise_cost": rounded(merchandise), "additional_cost": rounded(extras),
            "total_cost": rounded(total), "total_cost_usd": rounded(total * rate),
            "landed_unit_cost_usd": rounded(total * rate / quantity), "cost_breakdown": breakdown}


def validate_quote(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Quote must be a JSON object.")
    product = payload.get("product")
    if not isinstance(product, str) or product not in UNITS:
        raise ValueError("Unknown product.")
    unit = payload.get("unit") or UNITS[product]
    if unit != UNITS[product]:
        raise ValueError(f"Use the {UNITS[product]} unit for this product.")
    required = {"supplier", "grade", "specification", "origin", "incoterm_place", "destination", "payment_terms"}
    fields = {key: text(payload.get(key), EQUIVALENCE_FIELDS.get(key, key), maximum, key in required)
              for key, maximum in QUOTE_TEXT_FIELDS.items()}
    incoterm = str(payload.get("incoterm") or "").upper()
    if incoterm not in INCOTERMS:
        raise ValueError("Choose a valid Incoterm.")
    document_type = payload.get("document_type", "quotation")
    if not isinstance(document_type, str) or document_type not in {"quotation", "proforma", "invoice", "purchase_order"}:
        raise ValueError("Invalid commercial document type.")
    packaging = payload.get("packaging", "bulk")
    if not isinstance(packaging, str) or packaging not in PACKAGING or (packaging == "other" and not fields["packaging_detail"]):
        raise ValueError("Specify the packaging.")
    dates = {key: quote_date(payload.get(key), label) for key, label in
             [("quote_date", "Quote date"), ("valid_until", "Validity end date"), ("delivery_date", "Delivery date") ]}
    if dates["valid_until"] < dates["quote_date"] or dates["delivery_date"] < dates["quote_date"]:
        raise ValueError("Validity and delivery dates must follow the quote date.")
    status = payload.get("status", "active")
    if not isinstance(status, str) or status not in {"active", "withdrawn"}:
        raise ValueError("Invalid quote status.")
    return {**fields, **dates, **landed_costs(payload), "product": product, "unit": unit,
            "incoterm": incoterm, "packaging": packaging, "document_type": document_type, "status": status}


def compare_quotes(quotes: list[dict], as_of: date | None = None) -> dict:
    """Display selected offers together, rank only genuinely equivalent ones."""
    as_of = as_of or date.today()
    base = quotes[0]
    normalized = lambda v: " ".join(str(v).casefold().split())
    differences = []
    for quote in quotes:
        reasons = [label for key, label in EQUIVALENCE_FIELDS.items() if normalized(quote[key]) != normalized(base[key])]
        if quote["status"] != "active":
            reasons.append("Quote withdrawn")
        if not quote["quote_date"] <= as_of.isoformat() <= quote["valid_until"]:
            reasons.append("Quote invalid on comparison date")
        differences.append({"quote_id": quote["id"], "reasons": reasons})
    equivalent = not any(item["reasons"] for item in differences)
    ranked = sorted(quotes, key=lambda q: (q["total_cost_usd"], q["id"])) if equivalent else []
    return {"as_of": as_of.isoformat(), "equivalent": equivalent, "quotes": quotes, "differences": differences,
            "ranking": [{"quote_id": q["id"], "rank": i, "total_cost_usd": q["total_cost_usd"]} for i, q in enumerate(ranked, 1)],
            "message": "Equivalent quotes: ranked by landed cost in USD." if equivalent else "Quotes are not equivalent or valid: no price ranking."}
