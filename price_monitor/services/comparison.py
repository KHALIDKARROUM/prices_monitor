"""Compare recorded API quotes only when they describe the same market series."""

from __future__ import annotations

import math
from itertools import combinations

from .. import config


SOURCE_DEFINITIONS = (
    ("fred_brent", "brent", "FRED", "EIA/FRED - DCOILBRENTEU", "Brent Europe · spot quotidien", "brent_spot", "USD/baril"),
    ("eia_brent", "brent", "EIA", "U.S. EIA - PET.RBRTE.D", "Brent Europe · spot quotidien", "brent_spot", "USD/baril"),
    ("alpha_brent", "brent", "Alpha Vantage", "Alpha Vantage - BRENT", "Brent Europe · spot quotidien", "brent_spot", "USD/baril"),
    ("live_brent", "brent", "Yahoo Finance", "Yahoo Finance - Brent futures BZ=F", "Brent BZ=F · contrat à terme", "brent_futures", "USD/baril"),
    ("fred_diesel", "gasoil", "FRED", "EIA/FRED - DDFUELNYH", "Diesel New York Harbor · spot quotidien", "diesel_spot", "USD/gallon"),
    ("eia_diesel", "gasoil", "EIA", "U.S. EIA - PET.DDFUELNYH.D", "Diesel New York Harbor · spot quotidien", "diesel_spot", "USD/gallon"),
    ("live_gasoil_proxy", "gasoil", "Yahoo Finance", "Yahoo Finance - NY Harbor Heating Oil HO=F (proxy gasoil)", "Heating oil HO=F · contrat à terme / proxy", "heating_oil_futures", "USD/gallon"),
    ("bls_asphalt_ppi", "bitume", "BLS", "U.S. BLS Public Data API - WPU058", "Indice des prix de l'asphalte · mensuel", "asphalt_index", "indice (déc. 1984 = 100)"),
)
SOURCE_CODE_BY_LABEL = {definition[3]: definition[0] for definition in SOURCE_DEFINITIONS}
# Equivalent daily feeds publish to cents/barrel or thousandths/gallon.
# Differences beyond half the published precision deserve investigation.
ROUNDING_TOLERANCE = {"brent": 0.005, "gasoil": 0.0005}


def compare_api_prices(
    observations: list[dict],
    benchmarks: list[dict] | None = None,
    source_health: list[dict] | None = None,
) -> dict:
    """Return provider prices, pairwise verdicts and an explicit coverage summary.

    The caller supplies either the current collection or the latest recorded
    market date per provider. Missing APIs, publication lags, futures and proxies
    cannot establish an agreement.
    """
    recorded = {row["source"]: row for row in observations}
    recorded.update({row["source"]: row for row in benchmarks or []})
    health = {row["code"]: row for row in source_health or []}
    configured = {
        "eia_brent": bool(config.EIA_API_KEY),
        "eia_diesel": bool(config.EIA_API_KEY),
        "alpha_brent": bool(config.ALPHA_VANTAGE_API_KEY),
        "live_brent": config.LIVE_QUOTES_ENABLED,
        "live_gasoil_proxy": config.LIVE_QUOTES_ENABLED,
    }
    sources = []
    for code, product, provider, label, instrument, group, default_unit in SOURCE_DEFINITIONS:
        row = recorded.get(label, {})
        if group == "asphalt_index":
            row = next((item for item in benchmarks or [] if item.get("code") == "bls_wpu058"), row)
            if row.get("source") == "BLS/FRED - WPU058":
                provider = "BLS via FRED"
        value = row.get("value") if group == "asphalt_index" else row.get("price")
        try:
            value = float(value)
            if value <= 0 or not math.isfinite(value):
                value = None
        except (TypeError, ValueError):
            value = None
        source_state = health.get(code, {})
        if not configured.get(code, True):
            availability = "not_configured"
        elif source_state.get("last_error"):
            availability = "unavailable"
        else:
            availability = "available" if value is not None else "no_data"
        sources.append({
            "code": code, "product": product, "provider": provider, "source": row.get("source", label),
            "instrument": instrument, "group": group,
            "kind": "index" if group == "asphalt_index" else "price",
            "price": value, "unit": row.get("unit", default_unit),
            "currency": "" if group == "asphalt_index" else row.get("currency", "USD"),
            "source_date": str(row["source_date"])[:10] if row.get("source_date") else None,
            "collected_at": row.get("collected_at"),
            "last_success_at": source_state.get("last_success_at"),
            "availability": availability,
        })

    pairs = []
    for left, right in combinations(sources, 2):
        if left["product"] != right["product"] or left["kind"] != "price" or right["kind"] != "price":
            continue
        same_unit = left["unit"] == right["unit"] and left["currency"] == right["currency"]
        have_prices = left["price"] is not None and right["price"] is not None
        difference = abs(left["price"] - right["price"]) if have_prices and same_unit else None
        difference_pct = difference / left["price"] * 100 if difference is not None else None
        if not have_prices:
            status, reason = "unverified", "Données manquantes : comparaison non vérifiée."
        elif not same_unit:
            status, reason = "not_comparable", "Unités ou devises différentes."
        elif left["group"] != right["group"]:
            status, reason = "not_comparable", "Produits de marché différents : spot, contrat à terme ou proxy."
            if left["source_date"] != right["source_date"]:
                reason += " Dates de publication également différentes."
        elif not left["source_date"] or left["source_date"] != right["source_date"]:
            status, reason = "not_comparable", "Dates de publication différentes ou absentes."
        elif left["availability"] != "available" or right["availability"] != "available":
            status, reason = "unverified", "Une API est indisponible ou désactivée ; prix enregistré seulement."
        elif difference > ROUNDING_TOLERANCE[left["product"]] + 1e-12:
            status, reason = "mismatch", "Même série et même date : écart à vérifier (révision ou erreur possible)."
        else:
            status, reason = "match", "Même série, date, unité et devise : valeurs cohérentes à l'arrondi près."
        pairs.append({
            "product": left["product"], "left": left["code"], "right": right["code"],
            "difference": round(difference, 6) if difference is not None else None,
            "difference_pct": round(difference_pct, 4) if difference_pct is not None else None,
            "unit": left["unit"] if same_unit else None,
            "status": status, "reason": reason,
        })
    checked = [pair for pair in pairs if pair["status"] in {"match", "mismatch"}]
    mismatches = sum(pair["status"] == "mismatch" for pair in pairs)
    by_code = {source["code"]: source for source in sources}
    warnings = []
    for pair in pairs:
        if pair["status"] != "mismatch":
            continue
        left, right = by_code[pair["left"]], by_code[pair["right"]]
        warnings.append(
            f"{left['instrument']} ({left['source_date']}) : "
            f"{left['provider']} = {left['price']:.4f}, "
            f"{right['provider']} = {right['price']:.4f} {pair['unit']} ; "
            f"écart de {pair['difference']:.6f} {pair['unit']} "
            f"({pair['difference_pct']:.4f} %)."
        )
    message = (
        "Les prix des API diffèrent : un problème est possible. Vérifiez les sources concernées."
        if mismatches else
        "Les prix des API comparables sont cohérents."
        if checked else
        "Comparaison non vérifiée : aucune paire de prix équivalente disponible."
    )
    return {
        "sources": sources, "pairs": pairs,
        "status": "warning" if mismatches else "consistent" if checked else "unverified",
        "message": message, "warnings": warnings,
        "comparable_pair_count": len(checked), "mismatch_count": mismatches,
        "rounding_tolerance": ROUNDING_TOLERANCE,
        "note": "FRED et Alpha Vantage relaient les séries EIA : leur accord confirme la cohérence des flux, pas une mesure indépendante. BLS fournit un indice, pas un prix du bitume.",
    }
