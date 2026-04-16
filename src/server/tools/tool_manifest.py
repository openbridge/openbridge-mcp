from typing import Dict


TOOL_MANIFEST: Dict[str, Dict[str, str]] = {
    "get_capabilities": {
        "category": "meta",
        "description": "Return current tool availability, required environment variables, and LLM opt-in behavior.",
    },
    "get_remote_identities": {
        "category": "remote_identity",
        "description": "Retrieve remote identities for the current user. Returns list of remote identity names.",
    },
    "get_remote_identity_by_id": {
        "category": "remote_identity",
        "description": "Retrieve a specific remote identity by its ID. Returns remote identity details if found.",
    },
    "validate_query": {
        "category": "query_validation",
        "description": "Analyze SQL with sampling, requiring a LIMIT unless allow_unbounded=True.",
    },
    "execute_query": {
        "category": "query_validation",
        "description": "Validate then execute a SQL query; defaults to requiring LIMIT unless allow_unbounded=True.",
    },
    "get_amazon_api_access_token": {
        "category": "service",
        "description": "Get the Amazon API access token for a given remote identity ID. Returns the access token if available.",
    },
    "get_amazon_advertising_profiles": {
        "category": "service",
        "description": "List the Amazon Advertising profiles for a given remote identity ID. Returns a list of profiles.",
    },
    "get_table_schema": {
        "category": "service",
        "description": "Get the schema/rules for a given table name from the rules API. Use table names from list_product_tables output. Returns the schema if found.",
    },
    "get_suggested_table_names": {
        "category": "service",
        "description": "Given a query string, obtain a list of possible table names from the rules API (through the service API).",
    },
    "get_healthchecks": {
        "category": "healthchecks",
        "description": "Get the healthchecks related to the current user. Returns a list of healthchecks.",
    },
    "get_jobs": {
        "category": "jobs",
        "description": "Fetch jobs from the Openbridge API.",
    },
    "get_job_by_id": {
        "category": "jobs",
        "description": "Fetch a single job by job ID.",
    },
    "get_history_by_id": {
        "category": "jobs",
        "description": "Fetch a single history transaction by history ID.",
    },
    "update_history_status": {
        "category": "jobs",
        "description": "Update a history transaction status by history ID.",
    },
    "create_job": {
        "category": "jobs",
        "description": "Create a job for a given subscription.",
    },
    "get_subscriptions": {
        "category": "subscriptions",
        "description": "Get the subscriptions related to the current user. Returns a list of subscriptions.",
    },
    "get_subscription_by_id": {
        "category": "subscriptions",
        "description": "Retrieve a specific subscription by ID.",
    },
    "create_subscription": {
        "category": "subscriptions",
        "description": "Create a subscription with JSON:API attributes payload.",
    },
    "update_subscription": {
        "category": "subscriptions",
        "description": "Update a subscription with JSON:API attributes payload.",
    },
    "cancel_subscription": {
        "category": "subscriptions",
        "description": "Cancel a subscription by setting status to cancelled.",
    },
    "get_storage_subscriptions": {
        "category": "subscriptions",
        "description": "Get the storage subscriptions related to the current user. Returns a list of storage subscriptions.",
    },
    "get_product_stage_ids": {
        "category": "products",
        "description": "Get the stage IDs for a specific product. Returns a list of stage IDs associated with the product.",
    },
    "search_products": {
        "category": "products",
        "description": "Search for Openbridge data products by name (fuzzy matching with ranking).",
    },
    "list_product_tables": {
        "category": "products",
        "description": "List all tables (data payloads) available for a specific product.",
    },
}

