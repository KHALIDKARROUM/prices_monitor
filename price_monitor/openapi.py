"""OpenAPI 3.0 description for the HTTP API."""

from __future__ import annotations


OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "Price Monitor API",
        "version": "0.1.0",
        "description": "API for tracking diesel, Brent crude, and bitumen prices.",
    },
    "servers": [{"url": "/", "description": "Current server"}],
    "tags": [
        {"name": "health", "description": "Liveness and readiness checks"},
        {"name": "market", "description": "Price data and collection"},
    ],
    "components": {
        "securitySchemes": {
            "ApiKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key", "description": "Personal token. A shared key is allowed only for local personal development."},
            "BearerAuth": {"type": "http", "scheme": "bearer", "description": "Expiring, revocable personal token with the account's current role."},
            "CookieSession": {"type": "apiKey", "in": "cookie", "name": "pm_session", "description": "Individual HttpOnly session. Writes also require X-CSRF-Token and an allowed origin."},
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
                    "exchange_rate": {"type": "number", "exclusiveMinimum": 0, "description": "USD per unit of currency"},
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
                    "direction": {"type": "string", "enum": ["above", "below", "stale"], "description": "stale monitors the age of the live Brent or diesel quote."},
                    "basis": {"type": "string", "enum": ["quote", "index"], "default": "quote", "description": "index monitors only the monthly BLS WPU058 series; quote uses USD prices and supplier quotes."},
                    "threshold": {"type": "number", "exclusiveMinimum": 0, "description": "Price for above/below; whole minutes from 2 to 1,440 for stale."},
                    "channel": {"type": "string", "enum": ["webhook", "email", "both"]},
                    "muted": {"type": "boolean", "default": False},
                },
            },
            "Error": {"type": "object", "properties": {"error": {"type": "string"}}},
        },
    },
    "paths": {
        "/health": {
            "get": {"tags": ["health"], "summary": "Readiness probe", "responses": {"200": {"description": "Service ready"}, "503": {"description": "Dependency unavailable"}}}
        },
        "/health/live": {
            "get": {"tags": ["health"], "summary": "Liveness probe", "responses": {"200": {"description": "Process active"}}}
        },
        "/health/ready": {
            "get": {"tags": ["health"], "summary": "Readiness probe", "responses": {"200": {"description": "Dependencies available"}, "503": {"description": "Dependency unavailable"}}}
        },
        "/metrics": {
            "get": {"tags": ["health"], "summary": "Prometheus metrics", "responses": {"200": {"description": "Metrics in text format"}}}
        },
        "/api/dashboard": {
            "get": {"tags": ["market"], "summary": "Indicators, series, and data quality", "parameters": [{"$ref": "#/components/parameters/Days"}], "responses": {"200": {"description": "Dashboard"}}}
        },
        "/api/source-comparison": {
            "get": {"tags": ["market"], "summary": "API prices and differences between equivalent series", "responses": {"200": {"description": "Latest observation by provider, date, unit, market, availability, and comparison. Only matching series, dates, units, and currencies can reveal a suspicious difference. Missing sources remain unverified."}}}
        },
        "/api/connectivity": {
            "get": {"tags": ["health"], "summary": "Verified internet connection and aggregate provider availability", "responses": {"200": {"description": "Online, offline, or checking state; timestamps; and aggregate success, partial, error, or pending status"}}}
        },
        "/api/collection-schedules": {
            "get": {"tags": ["market"], "summary": "Schedules and quotas for configured providers", "responses": {"200": {"description": "Provider, interval_minutes, status, next_collection_at, requests_last_24h, request_limit_24h (null when no local limit), failures, reason, and last_error. Times are UTC; market closures, quotas, and backoff are considered."}}}
        },
        "/api/forecast": {
            "get": {"tags": ["market"], "summary": "Diesel, Brent, and bitumen index forecasts", "parameters": [{"$ref": "#/components/parameters/HistoryDays"}], "responses": {"200": {"description": "Prices at 7/30/90 days and BLS bitumen index at 1/3/6 months from the latest source date. Each product exposes its unit, frequency, horizons, indicative intervals, persistence comparison, and historical coverage."}, "400": {"description": "Invalid parameter"}}}
        },
        "/api/forecast-performance": {
            "get": {"tags": ["market"], "summary": "Rolling live forecast performance", "parameters": [
                {"name": "window", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 365, "default": 90}},
                {"$ref": "#/components/parameters/Product"}, {"$ref": "#/components/parameters/Instrument"},
                {"name": "horizon", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 365}},
            ], "responses": {"200": {"description": "Persisted issued forecasts, their resolved actuals, rolling MAE/RMSE/MAPE, bias, improvement over the original last-known-price baseline, and interval coverage."}, "400": {"description": "Invalid parameter"}}}
        },
        "/api/vintages": {
            "get": {"tags": ["market"], "summary": "Captured provider value vintages", "parameters": [
                {"$ref": "#/components/parameters/Product"}, {"$ref": "#/components/parameters/Instrument"},
                {"name": "source", "in": "query", "schema": {"type": "string"}},
                {"name": "data_kind", "in": "query", "schema": {"type": "string", "enum": ["quote", "index"]}},
                {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}},
            ], "responses": {"200": {"description": "Immutable provider values with availability timestamp, basis, revision number, and predecessor."}}}
        },
        "/api/vintages/as-of": {
            "get": {"tags": ["market"], "summary": "Point-in-time observed market values", "parameters": [
                {"name": "as_of", "in": "query", "required": True, "schema": {"type": "string", "format": "date-time"}},
                {"$ref": "#/components/parameters/Product"}, {"$ref": "#/components/parameters/Instrument"},
                {"name": "data_kind", "in": "query", "schema": {"type": "string", "enum": ["quote", "index"], "default": "quote"}},
            ], "responses": {"200": {"description": "Canonical provider values captured by this application at the requested instant, including provider availability provenance. Legacy reconstructed imports are deliberately excluded."}, "400": {"description": "Invalid timestamp or filter"}}}
        },
        "/api/alerts": {
            "get": {"tags": ["market"], "summary": "List alert rules and their status", "responses": {"200": {"description": "Active, recovered, or paused rules"}}},
            "post": {"tags": ["market"], "summary": "Create an alert rule", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AlertRuleInput"}}}}, "responses": {"201": {"description": "Rule created"}, "400": {"description": "Invalid rule"}}},
        },
        "/api/alerts/{id}": {
            "put": {"tags": ["market"], "summary": "Update an alert rule", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}], "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/AlertRuleInput"}}}}, "responses": {"200": {"description": "Rule updated"}, "400": {"description": "Invalid rule"}}},
            "delete": {"tags": ["market"], "summary": "Delete an alert rule", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}], "responses": {"200": {"description": "Rule deleted"}, "400": {"description": "Rule not found"}}},
        },
        "/api/observations": {
            "description": "API key authentication. Local development exception without a configured key: loopback server and client, local dashboard requests only.",
            "get": {"tags": ["market"], "summary": "Filtered history", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "parameters": [{"$ref": "#/components/parameters/Product"}, {"$ref": "#/components/parameters/Days"}], "responses": {"200": {"description": "Observations"}, "401": {"description": "Authentication required"}}},
            "post": {"tags": ["market"], "summary": "Add an observation", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ObservationInput"}}}}, "responses": {"201": {"description": "Observation created"}, "400": {"description": "Invalid input", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}}},
        },
        "/api/history": {
            "get": {"tags": ["market"], "summary": "Paginated history with filters", "parameters": [
                {"$ref": "#/components/parameters/Product"},
                {"$ref": "#/components/parameters/Page"},
                {"$ref": "#/components/parameters/PageSize"},
                {"name": "supplier", "in": "query", "schema": {"type": "string"}},
                {"name": "source", "in": "query", "schema": {"type": "string"}},
                {"name": "date_from", "in": "query", "schema": {"type": "string", "format": "date"}},
                {"name": "date_to", "in": "query", "schema": {"type": "string", "format": "date"}},
                {"name": "min_price", "in": "query", "schema": {"type": "number", "minimum": 0}},
                {"name": "max_price", "in": "query", "schema": {"type": "number", "minimum": 0}},
                {"name": "data_kind", "in": "query", "schema": {"type": "string", "enum": ["quote", "index"]}, "description": "Filter prices/quotes or the monthly bitumen index; units remain separate."},
            ], "responses": {"200": {"description": "Filtered, paginated history"}}}
        },
        "/api/history/compare": {
            "get": {"tags": ["market"], "summary": "Compare two authenticated periods", "responses": {"200": {"description": "Comparison by product"}, "400": {"description": "Invalid periods"}}}
        },
        "/api/collect": {
            "post": {"tags": ["market"], "summary": "Collect available providers", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "responses": {"200": {"description": "status success, partial, error, or deferred; rows, message, providers (results and next due times), and source_comparison between equivalent latest publications. Manual collection can advance a healthy provider but respects quotas, backoff, concurrency, and market closures. Deferrals are not new source failures."}, "401": {"description": "Authentication required"}}}
        },
        "/api/logs": {"get": {"tags": ["market"], "summary": "Recent collection logs", "responses": {"200": {"description": "Logs"}}}},
        "/api/benchmarks": {
            "get": {"tags": ["market"], "summary": "Latest non-executable market indicators", "parameters": [{"name": "product", "in": "query", "schema": {"type": "string", "enum": ["gasoil", "bitume"]}}], "responses": {"200": {"description": "Benchmarks separate from supplier quotes"}}}
        },
        "/api/suppliers": {
            "get": {"tags": ["market"], "summary": "Official supplier contact channels", "parameters": [{"name": "product", "in": "query", "schema": {"type": "string", "enum": ["gasoil", "bitume"]}}, {"name": "region", "in": "query", "schema": {"type": "string"}}], "responses": {"200": {"description": "SQL directory of contact channels"}}}
        },
        "/api/procurement-guide": {
            "get": {"tags": ["market"], "summary": "Sourcing guide and purchasing process", "parameters": [{"name": "product", "in": "query", "schema": {"type": "string", "enum": ["gasoil", "bitume"], "default": "gasoil"}}, {"name": "region", "in": "query", "schema": {"type": "string", "default": "all"}}], "responses": {"200": {"description": "Market context, suppliers, RFQ, and verification steps"}, "400": {"description": "Invalid filter"}}}
        },
        "/api/procurements": {
            "description": "Private data: individual session or token in team mode. Local personal development retains loopback access.",
            "get": {"tags": ["market"], "summary": "Purchase history", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "responses": {"200": {"description": "Purchases and calculated impacts"}, "401": {"description": "Authentication required"}}},
            "post": {"tags": ["market"], "summary": "Estimate and save a purchase", "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ProcurementInput"}}}}, "responses": {"201": {"description": "Purchase saved"}, "400": {"description": "Invalid input", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}}},
        },
        "/export.csv": {"get": {"tags": ["market"], "summary": "Export CSV", "responses": {"200": {"description": "CSV file"}}}},
        "/export.xlsx": {"get": {"tags": ["market"], "summary": "Export Excel", "responses": {"200": {"description": "XLSX file"}}}},
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
    "description": "Specific market series; spot, futures, proxy, supplier quotes, and index remain separate.",
}
for name in ("ObservationInput", "AlertRuleInput"):
    OPENAPI_SPEC["components"]["schemas"][name]["properties"]["instrument"] = {
        **instrument_schema,
        "description": "Must match the product, source, and basis. Legacy daily thresholds default to spot; stale uses the live futures feed.",
    }
for path in ("/api/history", "/api/history/compare", "/api/observations", "/export.csv", "/export.xlsx"):
    OPENAPI_SPEC["paths"][path]["get"].setdefault("parameters", []).append({"$ref": "#/components/parameters/Instrument"})
for product, choices in (("brent", ["brent_spot", "brent_futures"]), ("gasoil", ["diesel_spot", "heating_oil_futures"])):
    OPENAPI_SPEC["paths"]["/api/dashboard"]["get"]["parameters"].append({
        "name": f"{product}_instrument", "in": "query", "schema": {"type": "string", "enum": choices},
        "description": "Selects the same series for the card, statistics, and chart. Defaults to saved futures data, then spot.",
    })
OPENAPI_SPEC["paths"]["/api/forecast"]["get"]["description"] = "Brent and diesel forecasts use only spot series, with stable FRED, EIA, then Alpha Vantage priority. Futures and the heating oil proxy are excluded from this history."
OPENAPI_SPEC["paths"]["/api/forecast"]["get"]["description"] += " One point per source date is selected before limiting; the latest 5,000 aggregated points are kept in chronological order."
OPENAPI_SPEC["paths"]["/api/forecast"]["get"]["description"] += " Each selected endpoint is persisted once with its model version, original baseline, and interval."
OPENAPI_SPEC["paths"]["/api/forecast-performance"]["get"]["description"] = "Scores only the same instrument: a futures or heating-oil observation can never resolve a spot forecast. Daily targets accept the first published actual on the target date through four days later; the monthly BLS index requires the target month."
OPENAPI_SPEC["paths"]["/api/vintages"]["get"]["description"] = "Each revision is append-only. available_at records the provider timestamp when supplied; captured_at records when this application received the value."
OPENAPI_SPEC["paths"]["/api/observations"]["get"]["description"] = "The 600 most recent raw observations in the period are returned in chronological order. Use paginated history or exports to browse all observations."
OPENAPI_SPEC["paths"]["/api/history/compare"]["get"]["description"] = "Compares all data matching the filters, with no 10,000-row cap per period; daily series are aggregated by instrument and date."
for path in ("/export.csv", "/export.xlsx"):
    OPENAPI_SPEC["paths"][path]["get"]["description"] = "Exports all observations matching the history filters, without pagination or a 10,000-row cap."
    if path == "/export.xlsx":
        OPENAPI_SPEC["paths"][path]["get"]["description"] += " Data continues on a new sheet after 1,048,575 rows, with headers on every sheet."
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
    **{key: {"type": "number", "minimum": 0, "description": label + ": total amount excluding unit price, in the quote currency."} for key, label in COST_FIELDS.items()},
    "other_costs": {"type": "array", "maxItems": 20, "items": {"type": "object", "required": ["label", "amount"], "properties": {"label": {"type": "string", "maxLength": 100}, "amount": {"type": "number", "minimum": 0}}}},
    "actor": {"type": "string", "maxLength": 100, "description": "In individual account mode, replaced with the authenticated account name. Declared author is used only for local personal access."},
    "change_reason": {"type": "string", "maxLength": 1000},
    "expected_version": {"type": "integer", "minimum": 1},
})
OPENAPI_SPEC["components"]["schemas"]["QuoteInput"] = {"type": "object", "properties": quote_properties,
    "required": ["product", "supplier", "grade", "specification", "origin", "incoterm", "incoterm_place", "destination", "payment_terms", "quote_date", "valid_until", "delivery_date", "quantity", "unit_price"]}
OPENAPI_SPEC["components"]["schemas"]["QuoteCorrection"] = {"type": "object", "properties": quote_properties, "required": ["expected_version", "change_reason"]}
OPENAPI_SPEC["components"]["schemas"]["QuoteDocumentInput"] = {"type": "object", "required": ["filename", "document_type", "content_base64", "expected_version"], "properties": {
    "filename": {"type": "string", "maxLength": 180}, "document_type": {"type": "string", "enum": list(DOCUMENT_TYPES)},
    "content_base64": {"type": "string", "format": "byte", "description": "PDF, PNG, JPEG, UTF-8 TXT, DOCX, or XLSX without macros; maximum 5 MB of content."},
    "expected_version": {"type": "integer", "minimum": 1}, "actor": {"type": "string", "maxLength": 100}, "change_reason": {"type": "string", "maxLength": 1000}}}
quote_security = [{"ApiKeyHeader": []}, {"BearerAuth": []}]
quote_id_parameter = {"name": "id", "in": "path", "required": True, "schema": {"type": "integer", "minimum": 1}}
quote_document_parameter = {"name": "document_id", "in": "path", "required": True, "schema": {"type": "integer", "minimum": 1}}
quote_responses = {"400": {"description": "Invalid fields"}, "401": {"description": "Authentication required"}, "404": {"description": "Quote or document not found"}, "409": {"description": "Version changed: reload before correcting"}, "413": {"description": "Document or request too large"}}
def quote_operation(summary, success="200", schema=None, parameters=None):
    operation = {"tags": ["procurement"], "summary": summary, "security": quote_security, "responses": {success: {"description": summary}, **quote_responses}}
    if schema:
        operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/" + schema}}}}
    if parameters:
        operation["parameters"] = parameters
    return operation
OPENAPI_SPEC["paths"].update({
    "/api/quotes": {"description": "Private quotes, separate from market observations. Same local development exception as /api/procurements.",
        "get": quote_operation("Paginated quotes", parameters=[{"$ref": "#/components/parameters/Page"}, {"$ref": "#/components/parameters/PageSize"}, {"$ref": "#/components/parameters/Product"}, {"name": "supplier", "in": "query", "schema": {"type": "string"}}]),
        "post": quote_operation("Create a quote and its first version", "201", "QuoteInput")},
    "/api/quotes/options": {"get": quote_operation("Choice lists, costs, and document limits")},
    "/api/quotes/compare": {"get": quote_operation("Compare 2 to 6 equivalent quotes; no ranking when terms or validity differ", parameters=[{"name": "ids", "in": "query", "required": True, "schema": {"type": "string"}, "description": "Distinct comma-separated IDs"}, {"name": "as_of", "in": "query", "schema": {"type": "string", "format": "date"}}])},
    "/api/quotes/{id}": {"get": quote_operation("Read quote terms, costs, and documents", parameters=[quote_id_parameter]), "put": quote_operation("Correct without overwriting a concurrent change", schema="QuoteCorrection", parameters=[quote_id_parameter])},
    "/api/quotes/{id}/history": {"get": quote_operation("Version history with declared author, reason, and before/after snapshots", parameters=[quote_id_parameter])},
    "/api/quotes/{id}/documents": {"post": quote_operation("Attach a supporting document and create a version", "201", "QuoteDocumentInput", [quote_id_parameter])},
    "/api/quotes/{id}/documents/{document_id}": {"get": quote_operation("Download a document, including a withdrawn document retained in history", parameters=[quote_id_parameter, quote_document_parameter]), "delete": quote_operation("Withdraw a document while retaining its bytes and history", schema="QuoteCorrection", parameters=[quote_id_parameter, quote_document_parameter])},
})
OPENAPI_SPEC["components"]["schemas"]["ProcurementInput"]["properties"].update({
    **{key: quote_properties[key] for key in COST_FIELDS}, "other_costs": quote_properties["other_costs"],
    "quote_id": {"type": "integer", "minimum": 1, "description": "Uses amounts and terms from the quote; only purchase date, budget, and notes remain customizable."},
    "quote_version": {"type": "integer", "minimum": 1, "description": "Expected current version; its snapshot remains attached to the budget after later corrections."},
})
# A quote-linked estimate supplies commercial fields from its versioned source.
OPENAPI_SPEC["components"]["schemas"]["ProcurementInput"]["required"] = []
OPENAPI_SPEC["components"]["schemas"]["ProcurementInput"]["anyOf"] = [
    {"required": ["quote_id", "quote_version"]}, {"required": ["product", "supplier", "quantity", "unit_price", "currency", "exchange_rate"]}]

# Account access applies equally to reads, exports and mutations.
OPENAPI_SPEC["tags"].append({"name": "accounts", "description": "Accounts, sessions, personal tokens, and audit"})
OPENAPI_SPEC["info"]["description"] += " In team mode, business data, history, and exports are private. Viewer: read/export; editor: write/collect; administrator: accounts/audit/metrics. Local personal development allows loopback access without an account."
OPENAPI_SPEC["components"]["schemas"].update({
    "LoginInput": {"type": "object", "required": ["username", "password"], "properties": {
        "username": {"type": "string"}, "password": {"type": "string", "format": "password"}}},
    "PasswordChange": {"type": "object", "required": ["current_password", "new_password"], "properties": {
        "current_password": {"type": "string", "format": "password"}, "new_password": {"type": "string", "format": "password", "minLength": 12, "maxLength": 128}}},
    "UserInput": {"type": "object", "required": ["username", "password", "role"], "properties": {
        "username": {"type": "string", "minLength": 3, "maxLength": 80}, "full_name": {"type": "string", "maxLength": 100},
        "password": {"type": "string", "format": "password", "minLength": 12, "maxLength": 128},
        "role": {"type": "string", "enum": ["viewer", "editor", "admin"]}}},
    "UserCorrection": {"type": "object", "required": ["version"], "properties": {
        "version": {"type": "integer", "minimum": 1}, "full_name": {"type": "string", "maxLength": 100},
        "role": {"type": "string", "enum": ["viewer", "editor", "admin"]}, "active": {"type": "boolean"},
        "password": {"type": "string", "format": "password", "minLength": 12, "maxLength": 128}}},
    "PersonalTokenInput": {"type": "object", "required": ["label"], "properties": {
        "label": {"type": "string", "maxLength": 100}, "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30}}},
})

def account_operation(summary, success="200", schema=None, parameters=None):
    operation = {"tags": ["accounts"], "summary": summary, "responses": {success: {"description": summary},
        "400": {"description": "Invalid fields"}, "409": {"description": "Concurrent version or existing account"},
        "429": {"description": "Too many attempts; honor Retry-After"}}}
    if schema:
        operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/" + schema}}}}
    if parameters:
        operation["parameters"] = parameters
    return operation

OPENAPI_SPEC["paths"].update({
    "/api/auth/session": {"get": account_operation("Access mode, user, role, and CSRF token for the current session")},
    "/api/auth/login": {"post": account_operation("Open a session; set an HttpOnly/SameSite=Strict cookie and return user/csrf_token", schema="LoginInput")},
    "/api/auth/logout": {"post": account_operation("Revoke the current session and remove its cookie")},
    "/api/auth/password": {"post": account_operation("Change your password; revoke all sessions and tokens for the account", schema="PasswordChange")},
    "/api/auth/tokens": {"get": account_operation("List your personal tokens without their secrets"),
        "post": account_operation("Create a personal token; its value is returned only once", "201", "PersonalTokenInput")},
    "/api/auth/tokens/{id}": {"delete": account_operation("Revoke one of your personal tokens", parameters=[quote_id_parameter])},
    "/api/users": {"get": account_operation("List accounts without password hashes (administrator)"),
        "post": account_operation("Create an individual account (administrator)", "201", "UserInput")},
    "/api/users/{id}": {"put": account_operation("Change role, name, active status, or password; protect the last administrator", schema="UserCorrection", parameters=[quote_id_parameter])},
    "/api/audit": {"get": account_operation("Paginated events with actor, action, resource, and request context (administrator)",
        parameters=[{"$ref": "#/components/parameters/Page"}, {"$ref": "#/components/parameters/PageSize"}])},
})
public_paths = {"/api/auth/session", "/api/auth/login", "/api/connectivity", "/health", "/healthz", "/health/live", "/health/ready", "/readyz", "/docs", "/openapi.json"}
account_security = [{"CookieSession": []}, {"BearerAuth": []}, {"ApiKeyHeader": []}]
for path, operations in OPENAPI_SPEC["paths"].items():
    for method, operation in operations.items():
        if method not in {"get", "post", "put", "delete", "head"}:
            continue
        operation["security"] = [] if path in public_paths else account_security
        if path not in public_paths or path == "/api/auth/login":
            operation["responses"].setdefault("401", {"description": "Authentication required or invalid credentials"})
        if path not in public_paths or path.startswith("/api/auth/"):
            operation["responses"].setdefault("403", {"description": "Invalid origin, HTTPS, CSRF, or insufficient permission"})
        if method in {"post", "put", "delete"} and path != "/api/auth/login":
            operation.setdefault("parameters", []).append({"name": "X-CSRF-Token", "in": "header", "schema": {"type": "string"},
                "description": "Required for a cookie session; obtained from /api/auth/session. Not required with a personal token."})
OPENAPI_SPEC["paths"]["/api/history"]["get"]["summary"] = "Private filtered, paginated history"
OPENAPI_SPEC["paths"]["/api/quotes"]["description"] = "Private quotes and supporting documents. Individual session or token in team mode; personal loopback access in development."
OPENAPI_SPEC["paths"]["/api/quotes/{id}/history"]["get"]["summary"] = "Version history with authenticated author, reason, and before/after snapshots"
