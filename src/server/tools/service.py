import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional

import requests
from fastmcp.server.context import Context

from src.utils.table_resolver import (
    find_matching_rule,
    levenshtein_similarity,
    normalize_lookup_token,
    parse_rule_item,
    rank_suggestions,
)
from src.utils.envelope import auth_error, make_error, not_found
from src.utils.logging import get_logger
from .base import get_api_timeout, get_auth_headers
from .remote_identity import get_remote_identity_by_id

logger = get_logger("service")

SERVICE_API_BASE_URL = os.getenv("SERVICE_API_BASE_URL", 'https://service.api.openbridge.io')

AMZADV_REGIONAL_BASE_URLS = {
    "na": "https://advertising-api.amazon.com",
    "eu": "https://advertising-api-eu.amazon.com",
    "fe": "https://advertising-api-fe.amazon.com",
}

MUTATING_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "merge",
)

LIMIT_PATTERN = re.compile(r"limit\s+\d", re.IGNORECASE)
TRACEBACK_PATTERN = re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE)
VAR_TASK_PATH_PATTERN = re.compile(r"/var/task/[\w/\.]+\.py")
FILE_REF_PATTERN = re.compile(r'File "[^"]+"')


def _safe_json(response: requests.Response) -> Optional[Dict[str, Any]]:
    """Safely parse a JSON response body."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _search_rules_api(
    *,
    query: str,
    headers: Dict[str, str],
    tool: str,
) -> tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
    """Query the rules API and return either data rows or an envelope error."""
    try:
        response = requests.get(
            f"{SERVICE_API_BASE_URL}/service/rules/prod/v1/rules/search",
            params={"path__icontains": query, "latest": "true"},
            headers=headers,
            timeout=get_api_timeout(),
        )
    except requests.RequestException as exc:
        logger.warning("Rules API request failed for query=%s: %s", query, exc)
        return None, make_error(
            tool=tool,
            error_kind="sp_api_client",
            summary=f"Rules API request failed for query {query}",
            error_code="TOOL_EXECUTION_FAILED",
            retryable=True,
            details=[{
                "path": "query",
                "issue": str(exc),
                "received_type": type(exc).__name__,
            }],
        )

    body_preview = response.text[:500] if response.text else "<empty body>"
    if response.status_code != 200:
        logger.warning(
            "Rules API failure for query=%s status=%d body_preview=%s",
            query,
            response.status_code,
            body_preview,
        )
        return None, make_error(
            tool=tool,
            error_kind="sp_api_http",
            summary=f"Rules API failed for query {query}",
            error_code="TOOL_EXECUTION_FAILED",
            retryable=response.status_code >= 500,
            details=[{
                "path": "query",
                "issue": "Rules API returned a non-200 status",
                "received_type": "HTTPStatusError",
                "status": response.status_code,
            }],
        )

    payload = _safe_json(response)
    if payload is None:
        logger.warning(
            "Rules API returned non-JSON payload for query=%s body_preview=%s",
            query,
            body_preview,
        )
        return None, make_error(
            tool=tool,
            error_kind="sp_api_client",
            summary=f"Rules API returned non-JSON payload for query {query}",
            error_code="TOOL_EXECUTION_FAILED",
            retryable=False,
            details=[{
                "path": "query",
                "issue": "Response body was not valid JSON",
                "received_type": "str",
            }],
        )

    return payload.get("data", []), None


def _table_not_found_envelope(
    *,
    table_name: str,
    suggestions: List[Dict[str, Any]],
    tool: str = "get_table_schema",
) -> Dict[str, Any]:
    hint_lines = [
        "Use get_suggested_table_names() with a broader query to discover valid table names.",
        "Use list_product_tables() for a product to inspect available payload-backed and rules-only names.",
    ]
    if suggestions:
        did_you_mean = ", ".join(item["lookup_key"] for item in suggestions)
        hint_lines.insert(0, f"Did you mean: {did_you_mean}?")

    return make_error(
        tool=tool,
        error_kind="mcp_input_validation",
        summary=f"Table {table_name} not found",
        error_code="TABLE_NOT_FOUND",
        retryable=False,
        details=[{
            "path": "table_name",
            "issue": "No matching rules were found",
            "received_type": "str",
        }],
        hints=hint_lines,
        examples=[item["lookup_key"] for item in suggestions] if suggestions else ["sp_orders_report"],
    )


def _is_error_envelope(value: Any) -> bool:
    return isinstance(value, dict) and value.get("_envelope_version") == 1 and "error_kind" in value


def _contains_traceback_leak(value: Any) -> bool:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, default=str)
        except Exception:  # pragma: no cover - defensive
            text = str(value)
    return bool(
        TRACEBACK_PATTERN.search(text)
        or VAR_TASK_PATH_PATTERN.search(text)
        or FILE_REF_PATTERN.search(text)
    )


def _missing_upstream_credential_id(payload: Dict[str, Any]) -> bool:
    ritam_data = payload.get("ritam_data")
    if isinstance(ritam_data, dict) and not ritam_data.get("id"):
        return True
    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("ritam_data")
        if isinstance(nested, dict) and not nested.get("id"):
            return True
    return False


def _find_mutating_keywords(query: str) -> List[str]:
    """Return mutating keywords detected in the SQL string."""
    query_lower = query.lower()
    return [kw for kw in MUTATING_KEYWORDS if re.search(rf"\b{kw}\b", query_lower)]


def _has_limit_clause(query: str) -> bool:
    """Determine whether the SQL string contains a LIMIT clause."""
    return bool(LIMIT_PATTERN.search(query))


async def validate_query(
    query: str,
    key_name: str,
    allow_unbounded: bool = False,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """Use sampling to assess query safety before execution.

    Args:
        query: Fully formed SQL query the caller intends to run.
        key_name: Storage/account mapping key the query targets.
        ctx: FastMCP context providing sampling capabilities.

    Returns:
        Dict[str, Any]: Structured assessment including heuristic findings,
        sampling feedback, and a recommended allow/deny decision.
    """

    if ctx is None:
        raise ValueError("Context is required for validate_query")

    # Check for either API key, matching sampling handler behavior
    api_key = os.getenv("FASTMCP_SAMPLING_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Sampling API key required: set FASTMCP_SAMPLING_API_KEY or OPENAI_API_KEY")

    query_trimmed = query.strip()
    mutating_keywords = _find_mutating_keywords(query_trimmed)
    has_limit = _has_limit_clause(query_trimmed)
    select_star = bool(re.search(r"select\s+\*", query_trimmed, re.IGNORECASE))

    heuristics: Dict[str, Any] = {
        "read_only": not mutating_keywords,
        "has_limit": has_limit,
        "uses_select_star": select_star,
        "allow_unbounded": allow_unbounded,
        "warnings": [],
    }

    if mutating_keywords:
        heuristics["warnings"].append(
            f"Query contains potential mutating keywords: {', '.join(mutating_keywords)}"
        )
    if not has_limit:
        if allow_unbounded:
            heuristics["warnings"].append(
                "Query does not include a LIMIT clause; override allow_unbounded=True permits execution."
            )
        else:
            heuristics["warnings"].append(
                "Query lacks a LIMIT clause and no override was provided; execution will be denied."
            )
    if select_star:
        heuristics["warnings"].append(
            "Query selects all columns; consider projecting specific fields."
        )
    if not key_name:
        heuristics["warnings"].append("No key_name provided.")

    enable_llm = os.getenv("OPENBRIDGE_ENABLE_LLM_VALIDATION", "false").lower() == "true"

    sampling_feedback: Dict[str, Any] = {"supported": False, "details": None}
    sampling_allows = heuristics["read_only"]

    if enable_llm:
        logger.warning("Sending SQL to OpenAI for validation (see SECURITY.md)")
        system_prompt = (
            "You evaluate SQL queries for a read-only analytics service. "
            "Return JSON with: read_only (bool), risk_level (low|medium|high), "
            "issues (list of strings), recommendations (list of strings), "
            "and allow (bool) indicating whether to proceed."
        )
        user_prompt = (
            "Account mapping key: {key}\nSQL Query:\n{query}".format(
                key=key_name or "<missing>", query=query_trimmed
            )
        )

        try:
            response = await ctx.sample(
                messages=[user_prompt],
                system_prompt=system_prompt,
                temperature=0,
                max_tokens=400,
            )
            raw_text = response.text.strip()
            sampling_feedback["raw"] = raw_text
            try:
                parsed = json.loads(raw_text)
                sampling_feedback["details"] = parsed
                sampling_feedback["supported"] = True
                sampling_allows = bool(parsed.get("allow", False)) and bool(
                    parsed.get("read_only", False)
                )
            except json.JSONDecodeError:
                sampling_feedback["error"] = "Sampling response was not valid JSON."
                sampling_allows = False
        except Exception as exc:  # pragma: no cover - runtime safeguard
            sampling_feedback["error"] = str(exc)
            sampling_allows = heuristics["read_only"]
    else:
        logger.debug("LLM validation disabled; using heuristics only")

    limit_ok = has_limit or allow_unbounded

    overall_allowed = heuristics["read_only"] and sampling_allows and limit_ok

    result = {
        "query": query_trimmed,
        "key_name": key_name,
        "decision": {
            "allowed": overall_allowed,
            "heuristics_read_only": heuristics["read_only"],
            "sampling_allows": sampling_allows,
            "limit_ok": limit_ok,
        },
        "heuristics": heuristics,
        "sampling": sampling_feedback,
    }

    if overall_allowed and not has_limit:
        result.setdefault("notes", []).append(
            "Query approved but lacks LIMIT; monitor downstream result size."
        )

    logger.debug("validate_query result: %s", result)
    return result

async def execute_query(
    query: str,
    key_name: str,
    allow_unbounded: bool = False,
    ctx: Optional[Context] = None,
):
    """
    Execute a SQL query in the query API (proxied through the service API) and return the results.

    Args:
        query (str): The SQL query to execute.
        key_name (str): The key name to extract from the response data.
    Returns:
        List[dict]: A list of dictionaries containing the query results.


    Example API call:
    POST {{api-service-local}}/query/dev/query
    Authorization: Bearer {{token}}

    {
        "data": {
            "type": "Query",
            "attributes": {
                "query": "SELECT * FROM mytestingset.ob_test_master",
                "accmapping": "{{bq-accmapping-dev}}",
                "run_async": false,
                "direct_results": true,
                "response_format": "csv"
            }
        }
    }
    """
    if ctx is None:
        raise ValueError("Context is required for execute_query")

    try:
        validation = await validate_query(
            query,
            key_name,
            allow_unbounded=allow_unbounded,
            ctx=ctx,
        )
        if not validation["decision"]["allowed"]:
            logger.warning(
                "Query validation failed; denying execution. decision=%s",
                validation["decision"],
            )
            return make_error(
                tool="execute_query",
                error_kind="mcp_input_validation",
                summary="Query validation failed",
                error_code="INPUT_VALIDATION_FAILED",
                retryable=False,
                details=[{
                    "path": "query",
                    "issue": "Query did not satisfy read-only validation safeguards",
                    "received_type": "str",
                }],
                meta={"validation": validation},
            )
    except ValueError as ve:
        # Fail-closed: do not execute query if validation cannot be performed
        logger.error("Validation error: %s; denying execution", str(ve))
        return make_error(
            tool="execute_query",
            error_kind="internal_error",
            summary=f"Query validation unavailable: {ve}",
            error_code="INTERNAL_ERROR",
            retryable=False,
            details=[{
                "path": "query",
                "issue": "Validation pre-check failed before execution",
                "received_type": "str",
            }],
            meta={"validation": "unavailable"},
        )

    headers = get_auth_headers(ctx)
    payload = {
        "data": {
            "type": "Query",
            "attributes": {
                "query": query,
                "accmapping": key_name,
                "run_async": False,
                "direct_results": False,
                "response_format": "csv"
            }
        }
    }
    try:
        response = await asyncio.to_thread(
            requests.post,
            f"{SERVICE_API_BASE_URL}/service/query/production/query",
            json=payload,
            headers=headers,
            timeout=get_api_timeout(),
        )
    except requests.RequestException as exc:
        logger.warning("Execute query request failed: %s", exc)
        return make_error(
            tool="execute_query",
            error_kind="sp_api_client",
            summary="Query execution failed",
            error_code="TOOL_EXECUTION_FAILED",
            retryable=True,
            details=[{
                "path": "query",
                "issue": str(exc),
                "received_type": type(exc).__name__,
            }],
            meta={"status": None, "validation": validation},
        )
    if response.status_code == 200:
        response_payload = _safe_json(response)
        if response_payload is None:
            return make_error(
                tool="execute_query",
                error_kind="sp_api_client",
                summary="Failed to parse query response",
                error_code="TOOL_EXECUTION_FAILED",
                retryable=True,
                details=[{
                    "path": "",
                    "issue": "Response body was not valid JSON",
                    "received_type": "str",
                }],
                meta={"status": 200, "validation": validation},
            )
        data = response_payload.get("data", [])
        return data

    logger.warning(
        "Failed to execute query: status=%s error=%s",
        response.status_code,
        response.text,
    )
    return make_error(
        tool="execute_query",
        error_kind="sp_api_http",
        summary="Failed to execute query",
        error_code="TOOL_EXECUTION_FAILED",
        retryable=response.status_code >= 500,
        details=[{
            "path": "",
            "issue": "Query API returned a non-200 status",
            "received_type": "HTTPStatusError",
            "status": response.status_code,
        }],
        meta={"status": response.status_code, "validation": validation},
    )

def get_amazon_api_access_token(
    remote_identity_id: int,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Get the Amazon API access token for a given remote identity ID. This token may be used for making direct API calls to Amazon Advertising services.
    If the remote identity is not found or the token cannot be retrieved, the function returns None.

    Args:
        remote_identity_id (int): The ID of the remote identity.
    Returns:
        Dict[str, Any]: Access token and client ID when successful, or an error payload.
    """
    # TODO: Validate that the remote identity is the correct type?
    # Obtain the AmzAdv access token from the service API
    headers = get_auth_headers(ctx)
    try:
        response = requests.get(
            f"{SERVICE_API_BASE_URL}/service/amzadv/token/{remote_identity_id}",
            headers=headers,
            timeout=get_api_timeout(),
        )
    except requests.RequestException as exc:
        logger.warning(
            "Amazon API access token request failed for remote identity %s: %s",
            remote_identity_id,
            exc,
        )
        return make_error(
            tool="get_amazon_api_access_token",
            error_kind="sp_api_client",
            summary="Amazon API access token request failed",
            error_code="TOOL_EXECUTION_FAILED",
            retryable=True,
            details=[{
                "path": "",
                "issue": str(exc),
                "received_type": type(exc).__name__,
            }],
            meta={"status": None},
        )
    response_payload = _safe_json(response)
    if isinstance(response_payload, dict) and _missing_upstream_credential_id(response_payload):
        logger.warning(
            "Remote identity %s is missing upstream credential record id. payload=%s",
            remote_identity_id,
            response_payload,
        )
        return auth_error(
            tool="get_amazon_api_access_token",
            summary="Remote identity is missing required upstream credential record",
            details=[{
                "path": "ritam_data.id",
                "issue": "Required upstream credential id is missing",
                "received_type": "missing",
            }],
        )

    if response.status_code == 200 and response_payload is not None:
        data = response_payload.get("data") if isinstance(response_payload, dict) else None
        data = data if isinstance(data, dict) else {}
        access_token = data.get("access_token")
        client_id = data.get("client_id")
        # Contract: missing/null access_token is an error. Returning
        # {"access_token": None} would let downstream callers treat the
        # response as success because the key is present.
        if not access_token:
            logger.warning(
                "Amazon API access token missing from response for remote identity %s",
                remote_identity_id,
            )
            return auth_error(
                tool="get_amazon_api_access_token",
                summary="Amazon API access token missing from response",
                details=[{
                    "path": "data.access_token",
                    "issue": "Upstream response did not include a non-empty access token",
                    "received_type": type(access_token).__name__,
                }],
            )
        logger.debug(
            "Retrieved Amazon API access token for remote identity %s (length: %d)",
            remote_identity_id,
            len(access_token),
        )
        return {"access_token": access_token, "client_id": client_id}

    details: Any
    if response_payload is None:
        details = response.text
    else:
        details = response_payload
    logger.warning(
        "Amazon access token upstream error for remote identity %s status=%s payload=%s",
        remote_identity_id,
        response.status_code,
        details,
    )
    if _contains_traceback_leak(details):
        return make_error(
            tool="get_amazon_api_access_token",
            error_kind="internal_error",
            summary="Failed to retrieve Amazon API access token",
            error_code="INTERNAL_ERROR",
            retryable=response.status_code >= 500,
            details=[{
                "path": "",
                "issue": "Upstream error details were sanitized",
                "received_type": type(details).__name__,
            }],
            meta={"sanitized": True, "upstream_status": response.status_code},
        )
    logger.warning(
        "Failed to retrieve Amazon API access token for remote identity %s: %s",
        remote_identity_id,
        response.status_code,
    )
    return make_error(
        tool="get_amazon_api_access_token",
        error_kind="sp_api_http",
        summary="Failed to retrieve Amazon API access token",
        error_code="TOOL_EXECUTION_FAILED",
        retryable=response.status_code >= 500,
        details=[{
            "path": "",
            "issue": "Token API returned a non-200 status",
            "received_type": "HTTPStatusError",
            "status": response.status_code,
        }],
        meta={"status": response.status_code},
    )

def get_amazon_advertising_profiles(
    remote_identity_id: int,
    ctx: Optional[Context] = None,
) -> List[dict] | Dict[str, Any]:
    """
    List the Amazon Advertising profiles for a given remote identity ID.

    Args:
        remote_identity_id (int): The ID of the remote identity.
    Returns:
        List[dict]: A list of Amazon Advertising profiles.
    """
    # Obtain the remote identity
    remote_identity = get_remote_identity_by_id(remote_identity_id, ctx=ctx)
    if not remote_identity or _is_error_envelope(remote_identity):
        logger.warning(f"Remote identity {remote_identity_id} not found. Cannot retrieve advertising profiles.")
        return remote_identity if _is_error_envelope(remote_identity) else not_found(
            tool="get_amazon_advertising_profiles",
            resource_type="remote_identity",
            resource_id=remote_identity_id,
            error_code="REMOTE_IDENTITY_NOT_FOUND",
        )
    # Obtain the Amazon Advertising access token. Token helper returns an
    # error dict when the token is missing/null — check explicitly.
    token_info = get_amazon_api_access_token(remote_identity_id, ctx=ctx)
    if not token_info or _is_error_envelope(token_info) or not token_info.get('access_token'):
        logger.warning(
            "No access token available for remote identity %s. Cannot retrieve advertising profiles.",
            remote_identity_id,
        )
        if _is_error_envelope(token_info):
            return token_info
        return auth_error(
            tool="get_amazon_advertising_profiles",
            summary="No access token available for remote identity",
        )
    # Resolve regional base URL; unknown region is a soft failure.
    region = remote_identity.get('region')
    base_url = AMZADV_REGIONAL_BASE_URLS.get(region) if isinstance(region, str) else None
    if not base_url:
        logger.warning(
            "Remote identity %s has unknown region %r; cannot route advertising profile lookup.",
            remote_identity_id,
            region,
        )
        return make_error(
            tool="get_amazon_advertising_profiles",
            error_kind="mcp_input_validation",
            summary=f"Remote identity {remote_identity_id} has unsupported region '{region}'",
            error_code="INPUT_VALIDATION_FAILED",
            retryable=False,
            details=[{
                "path": "remote_identity.region",
                "issue": "Unsupported Amazon Advertising region",
                "received_type": type(region).__name__,
            }],
        )
    headers = {
        "Authorization": f"Bearer {token_info['access_token']}",
        "Amazon-Advertising-API-ClientId": token_info.get('client_id'),
    }
    try:
        response = requests.get(
            f"{base_url}/v2/profiles",
            headers=headers,
            timeout=get_api_timeout(),
        )
    except requests.RequestException as exc:
        logger.warning(
            "Amazon Advertising profiles request failed for remote identity %s: %s",
            remote_identity_id,
            exc,
        )
        return make_error(
            tool="get_amazon_advertising_profiles",
            error_kind="sp_api_client",
            summary="Amazon Advertising profiles request failed",
            error_code="TOOL_EXECUTION_FAILED",
            retryable=True,
            details=[{
                "path": "",
                "issue": str(exc),
                "received_type": type(exc).__name__,
            }],
        )
    if response.status_code == 200:
        try:
            profiles = response.json()
        except ValueError:
            logger.warning(
                "Amazon Advertising profiles response was not JSON for remote identity %s",
                remote_identity_id,
            )
            return make_error(
                tool="get_amazon_advertising_profiles",
                error_kind="sp_api_client",
                summary="Amazon Advertising profiles response was not JSON",
                error_code="TOOL_EXECUTION_FAILED",
                retryable=True,
                details=[{
                    "path": "",
                    "issue": "Response body was not valid JSON",
                    "received_type": "str",
                }],
            )
        logger.debug(f"Retrieved Amazon Advertising profiles for remote identity {remote_identity_id}: {profiles}")
        return profiles
    logger.warning(f"Failed to retrieve Amazon Advertising profiles for remote identity {remote_identity_id}: {response.status_code}")
    return make_error(
        tool="get_amazon_advertising_profiles",
        error_kind="sp_api_http",
        summary="Failed to retrieve Amazon Advertising profiles",
        error_code="TOOL_EXECUTION_FAILED",
        retryable=response.status_code >= 500,
        details=[{
            "path": "",
            "issue": "Amazon Advertising API returned a non-200 status",
            "received_type": "HTTPStatusError",
            "status": response.status_code,
        }],
    )


def get_suggested_table_names(
    query: str,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Return structured table-name candidates from the rules API.

    Args:
        query (str): Discovery query, usually a table intent or report phrase.
    Returns:
        Dict[str, Any]: On success, ``{"query": ..., "candidates": [...]}``.
        On no-match/failure, returns a v1 error envelope.
    """
    headers = get_auth_headers(ctx)
    rules, error = _search_rules_api(
        query=query,
        headers=headers,
        tool="get_suggested_table_names",
    )
    if error is not None:
        return error

    parsed = [row for row in (parse_rule_item(item) for item in (rules or [])) if row is not None]
    if not parsed:
        logger.info("No suggested tables found for query=%s", query)
        return make_error(
            tool="get_suggested_table_names",
            error_kind="mcp_input_validation",
            summary=f"No suggested tables found for query {query}",
            error_code="TABLE_NOT_FOUND",
            retryable=False,
            details=[{
                "path": "query",
                "issue": "No matching rules were found",
                "received_type": "str",
            }],
            hints=[
                "Try a broader query term (for example: 'orders report' instead of a full key).",
                "Use list_product_tables() for a product to discover payload-backed names.",
                "Use get_table_schema() directly if you already know the likely table key.",
            ],
            examples=["sp_orders_report", "sp_orders_pii_master"],
        )

    candidates: List[Dict[str, Any]] = []
    seen = set()
    for row in sorted(parsed, key=lambda item: item["lookup_key"]):
        if row["lookup_key"] in seen:
            continue
        seen.add(row["lookup_key"])
        candidates.append({
            "lookup_key": row["lookup_key"],
            "aliases": row["aliases"],
            "destination_table": row.get("destination_table"),
            "rules_path": row.get("rules_path"),
            "source": row.get("source", "rules"),
            "confidence": round(
                levenshtein_similarity(
                    normalize_lookup_token(query),
                    normalize_lookup_token(row["lookup_key"]),
                ),
                3,
            ),
        })

    return {
        "query": query,
        "candidates": candidates,
    }


def get_table_schema(
    table_name: str,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Resolve a table alias and return canonical rules metadata.

    Args:
        table_name (str): Table key in bare, ``_master``, or ``_vNN`` form.
    Returns:
        Dict[str, Any]: On success returns canonical table metadata and schema.
        On not-found/failure returns a v1 error envelope.
    """
    headers = get_auth_headers(ctx)
    query = normalize_lookup_token(table_name) or table_name.strip().lower()
    rules, error = _search_rules_api(
        query=query,
        headers=headers,
        tool="get_table_schema",
    )
    if error is not None:
        return error

    parsed = [row for row in (parse_rule_item(item) for item in (rules or [])) if row is not None]
    match = find_matching_rule(table_name, parsed)
    if match is not None:
        response = {
            "lookup_key": match["lookup_key"],
            "resolved_alias": table_name,
            "aliases": match["aliases"],
            "rules_path": match.get("rules_path"),
            "schema": match["rule"],
        }
        destination_table = match.get("destination_table")
        if destination_table is not None:
            response["destination_table"] = destination_table
        return response

    # Fallback lookup terms for typo recovery/suggestions.
    fallback_terms: List[str] = []
    stripped_digits = re.sub(r"\d+$", "", query)
    if stripped_digits and stripped_digits != query:
        fallback_terms.append(stripped_digits)
    if "_" in stripped_digits:
        parent = stripped_digits.rsplit("_", 1)[0]
        if parent and parent != stripped_digits:
            fallback_terms.append(parent)

    suggestion_rows = list(parsed)
    for term in fallback_terms:
        extra_rules, extra_error = _search_rules_api(
            query=term,
            headers=headers,
            tool="get_table_schema",
        )
        if extra_error is not None:
            logger.debug("Fallback rules lookup failed for term=%s", term)
            continue
        suggestion_rows.extend(
            row for row in (parse_rule_item(item) for item in (extra_rules or [])) if row is not None
        )

    suggestions = rank_suggestions(
        table_name,
        [row["lookup_key"] for row in suggestion_rows],
        limit=5,
        min_similarity=0.6,
    )
    return _table_not_found_envelope(
        table_name=table_name,
        suggestions=suggestions,
    )
