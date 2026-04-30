import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional

import requests
from fastmcp.server.context import Context

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


def _safe_json(response: requests.Response) -> Optional[Dict[str, Any]]:
    """Safely parse a JSON response body."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


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
            return [{"error": "Query validation failed", "validation": validation}]
    except ValueError as ve:
        # Fail-closed: do not execute query if validation cannot be performed
        logger.error("Validation error: %s; denying execution", str(ve))
        return [{"error": f"Query validation unavailable: {ve}", "validation": "unavailable"}]

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
        return [{
            "error": "Query execution failed",
            "status": None,
            "details": str(exc),
            "validation": validation,
        }]
    if response.status_code == 200:
        response_payload = _safe_json(response)
        if response_payload is None:
            return [
                {
                    "error": "Failed to parse query response",
                    "status": 200,
                    "details": "Response body was not valid JSON",
                    "validation": validation,
                }
            ]
        data = response_payload.get("data", [])
        return data

    logger.warning(
        "Failed to execute query: status=%s error=%s",
        response.status_code,
        response.text,
    )
    return [
        {
            "error": "Failed to execute query",
            "status": response.status_code,
            "details": response.text,
            "validation": validation,
        }
    ]

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
        return {
            "error": "Amazon API access token request failed",
            "status": None,
            "details": str(exc),
        }
    response_payload = _safe_json(response)
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
            return {
                "error": "Amazon API access token missing from response",
                "status": 200,
                "details": response_payload,
            }
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
        "Failed to retrieve Amazon API access token for remote identity %s: %s",
        remote_identity_id,
        response.status_code,
    )
    return {
        "error": "Failed to retrieve Amazon API access token",
        "status": response.status_code,
        "details": details,
    }

def get_amazon_advertising_profiles(
    remote_identity_id: int,
    ctx: Optional[Context] = None,
) -> List[dict]:
    """
    List the Amazon Advertising profiles for a given remote identity ID.

    Args:
        remote_identity_id (int): The ID of the remote identity.
    Returns:
        List[dict]: A list of Amazon Advertising profiles.
    """
    # Obtain the remote identity
    remote_identity = get_remote_identity_by_id(remote_identity_id, ctx=ctx)
    if not remote_identity or ('error' in remote_identity):
        logger.warning(f"Remote identity {remote_identity_id} not found. Cannot retrieve advertising profiles.")
        return []
    # Obtain the Amazon Advertising access token. Token helper returns an
    # error dict when the token is missing/null — check explicitly.
    token_info = get_amazon_api_access_token(remote_identity_id, ctx=ctx)
    if not token_info or 'error' in token_info or not token_info.get('access_token'):
        logger.warning(
            "No access token available for remote identity %s. Cannot retrieve advertising profiles.",
            remote_identity_id,
        )
        return []
    # Resolve regional base URL; unknown region is a soft failure.
    region = remote_identity.get('region')
    base_url = AMZADV_REGIONAL_BASE_URLS.get(region) if isinstance(region, str) else None
    if not base_url:
        logger.warning(
            "Remote identity %s has unknown region %r; cannot route advertising profile lookup.",
            remote_identity_id,
            region,
        )
        return []
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
        return []
    if response.status_code == 200:
        try:
            profiles = response.json()
        except ValueError:
            logger.warning(
                "Amazon Advertising profiles response was not JSON for remote identity %s",
                remote_identity_id,
            )
            return []
        logger.debug(f"Retrieved Amazon Advertising profiles for remote identity {remote_identity_id}: {profiles}")
        return profiles
    logger.warning(f"Failed to retrieve Amazon Advertising profiles for remote identity {remote_identity_id}: {response.status_code}")
    return []


def get_suggested_table_names(
    query: str,
    ctx: Optional[Context] = None,
) -> List[str]:
    """
    Given a query string, obtain a list of possible table names from the rules API (through the service API).

    Args:
        query (str): The SQL query to analyze.
    Returns:
        List[str]: A list of possible table names found from the query.
    """
    headers = get_auth_headers(ctx)
    # Rules API stores hierarchical paths (e.g. "amazon-ads/<table>"), so a
    # bare search term needs substring matching via path__icontains. The
    # exact `path=` filter only matches full hierarchical paths.
    params = {
        "path__icontains": query,
        "latest": "true",
    }
    try:
        response = requests.get(
            f"{SERVICE_API_BASE_URL}/service/rules/prod/v1/rules/search",
            params=params,
            headers=headers,
            timeout=get_api_timeout(),
        )
    except requests.RequestException as exc:
        logger.warning("Failed to retrieve suggested table names: %s", exc)
        return []

    body_preview = response.text[:500] if response.text else "<empty body>"
    if response.status_code != 200:
        logger.warning(
            "Rules API failure for suggested names query=%s status=%d body_preview=%s",
            query,
            response.status_code,
            body_preview,
        )
        return []

    response_payload = _safe_json(response)
    if response_payload is None:
        logger.warning(
            "Rules API returned non-JSON payload for suggested names query=%s body_preview=%s",
            query,
            body_preview,
        )
        return []

    # Extract table names from the response
    table_names = []
    for item in response_payload.get("data", []):
        if item.get("attributes", {}):
            # Append the table name with '_master' suffix to ensure use of the master view
            table_names.append(item.get("attributes", {}).get("path").split('/')[-1] + '_master')
    if table_names:
        logger.debug(f"Found table names in query '{query}': {table_names}")
        return table_names
    logger.info(
        "Rules API returned 200 with no matches for suggested names query=%s body_preview=%s",
        query,
        body_preview,
    )
    return []


def get_table_schema(
    table_name: str,
    ctx: Optional[Context] = None,
) -> Optional[dict]:
    """
    Get the rules for a given table name from the rules API.

    Args:
        table_name (str): The name of the table to get rules for.
    Returns:
        dict: The rules for the table if found, otherwise None.
    """
    headers = get_auth_headers(ctx)
    # Remove the '_master' suffix if present to match the rule path
    if table_name.endswith('_master'):
        table_name = table_name[:-7]
    # The Rules API stores paths as "<source>/<table>" (e.g.
    # "amazon-ads/amzn_ads_sp_advertised_products"). `path=` requires an
    # exact match, which fails when callers pass a bare table name.
    # `path__icontains=` (Django-style) does substring matching; the
    # endswith() tie-break below disambiguates when multiple rules share
    # a suffix across different sources.
    url = (
        f"{SERVICE_API_BASE_URL}/service/rules/prod/v1/rules/search"
        f"?path__icontains={table_name}&latest=true"
    )
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=get_api_timeout(),
        )
    except requests.RequestException as exc:
        logger.warning("Rules API request failed for table=%s: %s", table_name, exc)
        return None
    body_preview = response.text[:500] if response.text else "<empty body>"
    if response.status_code == 200:
        payload = _safe_json(response) or {}
        rules = payload.get("data", [])
        if rules:
            if len(rules) > 1:
                exact_matches = [
                    rule for rule in rules
                    if isinstance(rule.get("attributes"), dict)
                    and isinstance(rule["attributes"].get("path"), str)
                    and rule["attributes"]["path"].endswith(table_name)
                ]
                if not exact_matches:
                    # Ambiguous match: refuse to guess. Contract requires
                    # None so callers cannot silently use the wrong rule.
                    logger.warning(
                        "Rules API returned %d matches for table=%s but none ended with the bare name; returning None.",
                        len(rules),
                        table_name,
                    )
                    return None
                rules = exact_matches
            logger.debug(f"Retrieved rules for table {table_name}: {rules[0]}")
            return rules[0]
        logger.info(
            "Rules API returned 200 with no matches for table=%s url=%s body_preview=%s",
            table_name,
            url,
            body_preview,
        )
        return None
    logger.warning(
        "Rules API failure for table=%s status=%d url=%s body_preview=%s",
        table_name,
        response.status_code,
        url,
        body_preview,
    )
    return None
