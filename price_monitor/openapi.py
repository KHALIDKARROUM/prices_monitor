"""OpenAPI 3.0 description for the HTTP API."""

from __future__ import annotations


OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "Price Monitor API",
        "version": "0.1.0",
        "description": "API de suivi des prix du gasoil, du Brent et du bitume.",
    },
    "servers": [{"url": "/", "description": "Serveur courant"}],
    "tags": [
        {"name": "health", "description": "Sondes de vie et de disponibilité"},
        {"name": "market", "description": "Données et collectes de prix"},
    ],
    "components": {
        "securitySchemes": {
            "ApiKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            "BearerAuth": {"type": "http", "scheme": "bearer"},
        },
        "schemas": {
            "ObservationInput": {
                "type": "object",
                "required": ["product", "price", "source"],
                "properties": {
                    "product": {"type": "string", "enum": ["gasoil", "brent", "bitume"]},
                    "price": {"type": "number", "exclusiveMinimum": 0},
                    "unit": {"type": "string", "example": "USD/baril"},
                    "source": {"type": "string", "maxLength": 150},
                    "supplier": {"type": "string", "maxLength": 150},
                    "source_date": {"type": "string", "format": "date"},
                    "collected_at": {"type": "string", "format": "date-time"},
                    "notes": {"type": "string", "maxLength": 500},
                },
            },
            "ProcurementInput": {
                "type": "object",
                "required": ["product", "supplier", "quantity", "unit_price", "currency", "exchange_rate"],
                "properties": {
                    "product": {"type": "string", "enum": ["gasoil", "brent", "bitume"]},
                    "supplier": {"type": "string", "maxLength": 150},
                    "quantity": {"type": "number", "exclusiveMinimum": 0},
                    "unit": {"type": "string", "example": "tonne"},
                    "currency": {"type": "string", "pattern": "^[A-Z]{3}$", "example": "EUR"},
                    "unit_price": {"type": "number", "exclusiveMinimum": 0},
                    "exchange_rate": {"type": "number", "exclusiveMinimum": 0, "description": "USD pour 1 unité de la devise"},
                    "transport_cost": {"type": "number", "minimum": 0},
                    "budget_amount": {"type": "number", "minimum": 0},
                    "purchase_date": {"type": "string", "format": "date"},
                    "notes": {"type": "string", "maxLength": 500},
                },
            },
            "AlertRuleInput": {
                "type": "object",
                "required": ["product", "direction", "threshold", "channel"],
                "properties": {
                    "product": {"type": "string", "enum": ["gasoil", "brent", "bitume"]},
                    "direction": {"type": "string", "enum": ["above", "below", "stale"], "description": "stale surveille l'âge de la cotation en direct (Brent ou gasoil)."},
                    "basis": {"type": "string", "enum": ["quote", "index"], "default": "quote", "description": "index surveille uniquement le BLS WPU058 mensuel; quote utilise les prix et devis USD."},
                    "threshold": {"type": "number", "exclusiveMinimum": 0, "description": "Prix pour above/below ; minutes entières de 2 à 1440 pour stale."},
                    "channel": {"type": "string", "enum": ["webhook", "email", "both"]},
                    "muted": {"type": "boolean", "default": False},
                },
            },
            "Error": {"type": "object", "properties": {"error": {"type": "string"}}},
        },
    },
    "paths": {
        "/health": {
            "get": {"tags": ["health"], "summary": "Readiness probe", "responses": {"200": {"description": "Service prêt"}, "503": {"description": "Dépendance indisponible"}}}
        },
        "/health/live": {
            "get": {"tags": ["health"], "summary": "Liveness probe", "responses": {"200": {"description": "Processus actif"}}}
        },
        "/health/ready": {
            "get": {"tags": ["health"], "summary": "Readiness probe", "responses": {"200": {"description": "Dépendances disponibles"}, "503": {"description": "Dépendance indisponible"}}}
        },
        "/metrics": {
            "get": {"tags": ["health"], "summary": "Métriques Prometheus", "responses": {"200": {"description": "Métriques au format texte"}}}
        },
        "/api/dashboard": {
            "get": {"tags": ["market"], "summary": "Indicateurs, séries et qualité", "parameters": [{"$ref": "#/components/parameters/Days"}], "responses": {"200": {"description": "Tableau de bord"}}}
        },
        "/api/source-comparison": {
            "get": {"tags": ["market"], "summary": "Prix par API et écarts entre séries équivalentes", "responses": {"200": {"description": "Dernier relevé par fournisseur, date, unité, marché, disponibilité et comparaison. Seules les mêmes séries, dates, unités et devises permettent de signaler un écart suspect. Les sources absentes restent non vérifiées."}}}
        },
        "/api/connectivity": {
            "get": {"tags": ["health"], "summary": "Connexion Internet vérifiée et résultat de la dernière collecte", "responses": {"200": {"description": "État online, offline ou checking, horodatages et dernière collecte"}}}
        },
        "/api/forecast": {
            "get": {"tags": ["market"], "summary": "Prévisions gasoil, Brent et indice Bitume", "parameters": [{"$ref": "#/components/parameters/HistoryDays"}], "responses": {"200": {"description": "Prix à 7/30/90 jours; indice bitume BLS à 1/3/6 mois, depuis la dernière date source. Chaque produit expose son unité, fréquence, horizons, bandes indicatives, comparaison à la persistance et couverture historique."}, "400": {"description": "Paramètre invalide"}}}
        },
        "/api/alerts": {
            "get": {"tags": ["market"], "summary": "Lister les règles et leur état", "responses": {"200": {"description": "Règles actives, rétablies ou en pause"}}},
            "post": {"tags": ["market"], "summary": "Créer une règle d'alerte", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AlertRuleInput"}}}}, "responses": {"201": {"description": "Règle créée"}, "400": {"description": "Règle invalide"}}},
        },
        "/api/alerts/{id}": {
            "put": {"tags": ["market"], "summary": "Modifier une règle d'alerte", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}], "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/AlertRuleInput"}}}}, "responses": {"200": {"description": "Règle modifiée"}, "400": {"description": "Règle invalide"}}},
            "delete": {"tags": ["market"], "summary": "Supprimer une règle d'alerte", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}], "responses": {"200": {"description": "Règle supprimée"}, "400": {"description": "Règle introuvable"}}},
        },
        "/api/observations": {
            "description": "Authentification par clé API. Exception en développement local sans clé configurée : serveur et client de boucle locale, requêtes du tableau de bord local uniquement.",
            "get": {"tags": ["market"], "summary": "Historique filtré", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "parameters": [{"$ref": "#/components/parameters/Product"}, {"$ref": "#/components/parameters/Days"}], "responses": {"200": {"description": "Observations"}, "401": {"description": "Authentification requise"}}},
            "post": {"tags": ["market"], "summary": "Ajouter une observation", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ObservationInput"}}}}, "responses": {"201": {"description": "Observation créée"}, "400": {"description": "Entrée invalide", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}}},
        },
        "/api/history": {
            "get": {"tags": ["market"], "summary": "Historique public paginé avec filtres", "parameters": [
                {"$ref": "#/components/parameters/Product"},
                {"$ref": "#/components/parameters/Page"},
                {"$ref": "#/components/parameters/PageSize"},
                {"name": "supplier", "in": "query", "schema": {"type": "string"}},
                {"name": "source", "in": "query", "schema": {"type": "string"}},
                {"name": "date_from", "in": "query", "schema": {"type": "string", "format": "date"}},
                {"name": "date_to", "in": "query", "schema": {"type": "string", "format": "date"}},
                {"name": "min_price", "in": "query", "schema": {"type": "number", "minimum": 0}},
                {"name": "max_price", "in": "query", "schema": {"type": "number", "minimum": 0}},
                {"name": "data_kind", "in": "query", "schema": {"type": "string", "enum": ["quote", "index"]}, "description": "Filtrer prix/devis ou indice mensuel bitume; les unités restent distinctes."},
            ], "responses": {"200": {"description": "Historique filtré et paginé"}}}
        },
        "/api/history/compare": {
            "get": {"tags": ["market"], "summary": "Comparer publiquement deux périodes", "responses": {"200": {"description": "Comparaison par produit"}, "400": {"description": "Périodes invalides"}}}
        },
        "/api/collect": {
            "post": {"tags": ["market"], "summary": "Déclencher une collecte immédiate", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "responses": {"200": {"description": "Résultat de collecte avec source_comparison : statut de cohérence, message, avertissements et écarts entre les prix reçus dans cette collecte."}, "401": {"description": "Authentification requise"}}}
        },
        "/api/logs": {"get": {"tags": ["market"], "summary": "Derniers journaux de collecte", "responses": {"200": {"description": "Journaux"}}}},
        "/api/benchmarks": {
            "get": {"tags": ["market"], "summary": "Derniers indicateurs de marché non exécutables", "parameters": [{"name": "product", "in": "query", "schema": {"type": "string", "enum": ["gasoil", "bitume"]}}], "responses": {"200": {"description": "Benchmarks séparés des devis fournisseurs"}}}
        },
        "/api/suppliers": {
            "get": {"tags": ["market"], "summary": "Canaux fournisseurs officiels", "parameters": [{"name": "product", "in": "query", "schema": {"type": "string", "enum": ["gasoil", "bitume"]}}, {"name": "region", "in": "query", "schema": {"type": "string"}}], "responses": {"200": {"description": "Répertoire SQL des voies de contact"}}}
        },
        "/api/procurement-guide": {
            "get": {"tags": ["market"], "summary": "Guide de sourcing et processus d'achat", "parameters": [{"name": "product", "in": "query", "schema": {"type": "string", "enum": ["gasoil", "bitume"], "default": "gasoil"}}, {"name": "region", "in": "query", "schema": {"type": "string", "default": "all"}}], "responses": {"200": {"description": "Contexte marché, fournisseurs, RFQ et étapes de contrôle"}, "400": {"description": "Filtre invalide"}}}
        },
        "/api/procurements": {
            "description": "Authentification par clé API. Exception pour la page Budget en développement local sans clé configurée : serveur et client de boucle locale, requêtes du tableau de bord local uniquement.",
            "get": {"tags": ["market"], "summary": "Historique des achats", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "responses": {"200": {"description": "Achats et impacts calculés"}, "401": {"description": "Authentification requise"}}},
            "post": {"tags": ["market"], "summary": "Évaluer et enregistrer un achat", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ProcurementInput"}}}}, "responses": {"201": {"description": "Achat enregistré"}, "400": {"description": "Entrée invalide", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}}},
        },
        "/export.csv": {"get": {"tags": ["market"], "summary": "Exporter en CSV", "responses": {"200": {"description": "Fichier CSV"}}}},
        "/export.xlsx": {"get": {"tags": ["market"], "summary": "Exporter en Excel", "responses": {"200": {"description": "Fichier XLSX"}}}},
    },
}

OPENAPI_SPEC["components"]["parameters"] = {
    "Days": {"name": "days", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 1825, "default": 30}},
    "HistoryDays": {"name": "history_days", "in": "query", "schema": {"type": "integer", "minimum": 90, "maximum": 1825, "default": 1825}},
    "Product": {"name": "product", "in": "query", "schema": {"type": "string", "enum": ["gasoil", "brent", "bitume"]}},
    "Page": {"name": "page", "in": "query", "schema": {"type": "integer", "minimum": 1, "default": 1}},
    "PageSize": {"name": "page_size", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}},
}

from .instruments import INSTRUMENTS

instrument_schema = {"type": "string", "enum": list(INSTRUMENTS)}
OPENAPI_SPEC["components"]["parameters"]["Instrument"] = {
    "name": "instrument", "in": "query", "schema": instrument_schema,
    "description": "Série de marché précise; spot, contrat à terme, proxy, devis et indice restent séparés.",
}
for name in ("ObservationInput", "AlertRuleInput"):
    OPENAPI_SPEC["components"]["schemas"][name]["properties"]["instrument"] = {
        **instrument_schema,
        "description": "Doit correspondre au produit, à la source et à la base. Les anciens seuils quotidiens utilisent le spot par défaut; stale utilise le flux à terme.",
    }
for path in ("/api/history", "/api/history/compare", "/api/observations", "/export.csv", "/export.xlsx"):
    OPENAPI_SPEC["paths"][path]["get"].setdefault("parameters", []).append({"$ref": "#/components/parameters/Instrument"})
for product, choices in (("brent", ["brent_spot", "brent_futures"]), ("gasoil", ["diesel_spot", "heating_oil_futures"])):
    OPENAPI_SPEC["paths"]["/api/dashboard"]["get"]["parameters"].append({
        "name": f"{product}_instrument", "in": "query", "schema": {"type": "string", "enum": choices},
        "description": "Sélectionne la même série pour la carte, les statistiques et le graphique. Par défaut, contrat à terme enregistré, sinon spot.",
    })
OPENAPI_SPEC["paths"]["/api/forecast"]["get"]["description"] = "Les prévisions Brent et diesel utilisent uniquement les séries spot avec priorité stable FRED, EIA, Alpha Vantage. Contrats à terme et proxy heating oil sont exclus de cet historique."
OPENAPI_SPEC["paths"]["/api/forecast"]["get"]["description"] += " Un point par date source est sélectionné avant la limite; les 5 000 points agrégés les plus récents sont conservés dans l'ordre chronologique."
OPENAPI_SPEC["paths"]["/api/observations"]["get"]["description"] = "Les 600 relevés bruts les plus récents de la période sont retournés dans l'ordre chronologique. Pour parcourir tous les relevés, utiliser les archives paginées ou les exports."
OPENAPI_SPEC["paths"]["/api/history/compare"]["get"]["description"] = "Compare toutes les données correspondant aux filtres, sans plafond de 10 000 relevés par période; les séries quotidiennes sont agrégées par instrument et date."
for path in ("/export.csv", "/export.xlsx"):
    OPENAPI_SPEC["paths"][path]["get"]["description"] = "Exporte tous les relevés correspondant aux filtres des archives, sans limite de pagination ni plafond de 10 000 lignes."
    if path == "/export.xlsx":
        OPENAPI_SPEC["paths"][path]["get"]["description"] += " Les données continuent sur une nouvelle feuille après 1 048 575 lignes, avec un en-tête sur chaque feuille."
    history_parameters = OPENAPI_SPEC["paths"]["/api/history"]["get"]["parameters"]
    OPENAPI_SPEC["paths"][path]["get"]["parameters"] = [parameter for parameter in history_parameters if parameter.get("$ref") not in {"#/components/parameters/Page", "#/components/parameters/PageSize"}]

# Structured supplier quotes and their private evidence.
from .quotes import COST_FIELDS, QUOTE_TEXT_FIELDS, INCOTERMS, PACKAGING, DOCUMENT_TYPES
quote_properties = {key: {"type": "string", "maxLength": maximum} for key, maximum in QUOTE_TEXT_FIELDS.items()}
quote_properties.update({
    "product": {"type": "string", "enum": ["gasoil", "brent", "bitume"]},
    "unit": {"type": "string", "enum": ["gallon", "baril", "tonne"]},
    "incoterm": {"type": "string", "enum": list(INCOTERMS)},
    "document_type": {"type": "string", "enum": ["quotation", "proforma", "invoice", "purchase_order"]},
    "packaging": {"type": "string", "enum": list(PACKAGING)},
    "status": {"type": "string", "enum": ["active", "withdrawn"]},
    **{key: {"type": "string", "format": "date"} for key in ("quote_date", "valid_until", "delivery_date")},
    **{key: {"type": "number", "exclusiveMinimum": 0} for key in ("quantity", "unit_price", "exchange_rate")},
    "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
    **{key: {"type": "number", "minimum": 0, "description": label + " : montant total hors prix unitaire, dans la devise du devis."} for key, label in COST_FIELDS.items()},
    "other_costs": {"type": "array", "maxItems": 20, "items": {"type": "object", "required": ["label", "amount"], "properties": {"label": {"type": "string", "maxLength": 100}, "amount": {"type": "number", "minimum": 0}}}},
    "actor": {"type": "string", "maxLength": 100, "description": "Auteur déclaré de la modification."},
    "change_reason": {"type": "string", "maxLength": 1000},
    "expected_version": {"type": "integer", "minimum": 1},
})
OPENAPI_SPEC["components"]["schemas"]["QuoteInput"] = {"type": "object", "properties": quote_properties,
    "required": ["product", "supplier", "grade", "specification", "origin", "incoterm", "incoterm_place", "destination", "payment_terms", "quote_date", "valid_until", "delivery_date", "quantity", "unit_price"]}
OPENAPI_SPEC["components"]["schemas"]["QuoteCorrection"] = {"type": "object", "properties": quote_properties, "required": ["expected_version", "change_reason"]}
OPENAPI_SPEC["components"]["schemas"]["QuoteDocumentInput"] = {"type": "object", "required": ["filename", "document_type", "content_base64", "expected_version"], "properties": {
    "filename": {"type": "string", "maxLength": 180}, "document_type": {"type": "string", "enum": list(DOCUMENT_TYPES)},
    "content_base64": {"type": "string", "format": "byte", "description": "PDF, PNG, JPEG, TXT UTF-8, DOCX ou XLSX sans macros; 5 Mo de contenu maximum."},
    "expected_version": {"type": "integer", "minimum": 1}, "actor": {"type": "string", "maxLength": 100}, "change_reason": {"type": "string", "maxLength": 1000}}}
quote_security = [{"ApiKeyHeader": []}, {"BearerAuth": []}]
quote_id_parameter = {"name": "id", "in": "path", "required": True, "schema": {"type": "integer", "minimum": 1}}
quote_document_parameter = {"name": "document_id", "in": "path", "required": True, "schema": {"type": "integer", "minimum": 1}}
quote_responses = {"400": {"description": "Champs invalides"}, "401": {"description": "Authentification requise"}, "404": {"description": "Devis ou document introuvable"}, "409": {"description": "Version modifiée : recharger avant correction"}, "413": {"description": "Document ou requête trop volumineux"}}
def quote_operation(summary, success="200", schema=None, parameters=None):
    operation = {"tags": ["procurement"], "summary": summary, "security": quote_security, "responses": {success: {"description": summary}, **quote_responses}}
    if schema:
        operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/" + schema}}}}
    if parameters:
        operation["parameters"] = parameters
    return operation
OPENAPI_SPEC["paths"].update({
    "/api/quotes": {"description": "Devis privés, distincts des relevés de marché. Même exception de développement local que /api/procurements.",
        "get": quote_operation("Devis paginés", parameters=[{"$ref": "#/components/parameters/Page"}, {"$ref": "#/components/parameters/PageSize"}, {"$ref": "#/components/parameters/Product"}, {"name": "supplier", "in": "query", "schema": {"type": "string"}}]),
        "post": quote_operation("Créer un devis et sa première version", "201", "QuoteInput")},
    "/api/quotes/options": {"get": quote_operation("Listes de choix, coûts et limites documentaires")},
    "/api/quotes/compare": {"get": quote_operation("Comparer 2 à 6 devis équivalents; aucun classement si les conditions ou la validité diffèrent", parameters=[{"name": "ids", "in": "query", "required": True, "schema": {"type": "string"}, "description": "Identifiants distincts séparés par des virgules"}, {"name": "as_of", "in": "query", "schema": {"type": "string", "format": "date"}}])},
    "/api/quotes/{id}": {"get": quote_operation("Lire les conditions, coûts et pièces du devis", parameters=[quote_id_parameter]), "put": quote_operation("Corriger sans écraser une modification concurrente", schema="QuoteCorrection", parameters=[quote_id_parameter])},
    "/api/quotes/{id}/history": {"get": quote_operation("Historique des versions avec auteur déclaré, motif et snapshots avant/après", parameters=[quote_id_parameter])},
    "/api/quotes/{id}/documents": {"post": quote_operation("Joindre un justificatif et créer une version", "201", "QuoteDocumentInput", [quote_id_parameter])},
    "/api/quotes/{id}/documents/{document_id}": {"get": quote_operation("Télécharger une pièce, y compris une pièce retirée conservée dans l’historique", parameters=[quote_id_parameter, quote_document_parameter]), "delete": quote_operation("Retirer une pièce tout en conservant ses octets et son historique", schema="QuoteCorrection", parameters=[quote_id_parameter, quote_document_parameter])},
})
OPENAPI_SPEC["components"]["schemas"]["ProcurementInput"]["properties"].update({
    **{key: quote_properties[key] for key in COST_FIELDS}, "other_costs": quote_properties["other_costs"],
    "quote_id": {"type": "integer", "minimum": 1, "description": "Utilise les montants et conditions du devis; seuls date d’achat, budget et notes restent personnalisables."},
    "quote_version": {"type": "integer", "minimum": 1, "description": "Version actuelle attendue; sa copie reste attachée au budget après les corrections futures."},
})
# A quote-linked estimate supplies commercial fields from its versioned source.
OPENAPI_SPEC["components"]["schemas"]["ProcurementInput"]["required"] = []
OPENAPI_SPEC["components"]["schemas"]["ProcurementInput"]["anyOf"] = [
    {"required": ["quote_id", "quote_version"]}, {"required": ["product", "supplier", "quantity", "unit_price", "currency", "exchange_rate"]}]
