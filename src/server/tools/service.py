import json
import re
from typing import Any

import requests

from src.utils.logging import get_logger
from .base import get_auth_headers
from .remote_identity import get_remote_identity_by_id
import os
from fastmcp.server.context import Context
from typing import Dict, List, Optional

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
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY environment variable is required for validate_query")

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

    sampling_feedback: Dict[str, Any] = {"supported": True, "details": None}
    sampling_allows = True

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
            sampling_allows = bool(parsed.get("allow", True)) and bool(
                parsed.get("read_only", True)
            )
        except json.JSONDecodeError:
            sampling_feedback["supported"] = False
            sampling_feedback["error"] = "Sampling response was not valid JSON."
            sampling_allows = False
    except Exception as exc:  # pragma: no cover - runtime safeguard
        sampling_feedback["supported"] = False
        sampling_feedback["error"] = str(exc)
        sampling_allows = heuristics["read_only"]

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
        logger.error("Validation error: %s; continuing without validation", str(ve))

    headers = get_auth_headers()
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
    response = requests.post(
        f"{SERVICE_API_BASE_URL}/service/query/production/query",
        json=payload,
        headers=headers
    )
    if response.status_code == 200:
        data = response.json().get("data", [])
        return data
    else:
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
) -> dict:
    """
    Get the Amazon API access token for a given remote identity ID. This token may be used for making direct API calls to Amazon Advertising services.
    If the remote identity is not found or the token cannot be retrieved, the function returns None.

    Args:
        remote_identity_id (int): The ID of the remote identity.
    Returns:
        str: The Amazon API access token if available, otherwise returns the error message.
    """
    # TODO: Validate that the remote identity is the correct type?
    # Obtain the AmzAdv access token from the service API
    headers = get_auth_headers()
    response = requests.get(
        f"{SERVICE_API_BASE_URL}/service/amzadv/token/{remote_identity_id}",
        headers=headers
    )
    if response.status_code == 200:
        access_token = response.json().get("data", {}).get('access_token')
        client_id = response.json().get("data", {}).get('client_id')
        logger.debug(f"Retrieved Amazon API access token for remote identity {remote_identity_id}: {access_token}")
    else:
        logger.warning(f"Failed to retrieve Amazon API access token for remote identity {remote_identity_id}: {response.status_code}")
        return str(response.json())
    return {"access_token": access_token, "client_id": client_id}

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
    if not remote_identity:
        logger.warning(f"Remote identity {remote_identity_id} not found. Cannot retrieve advertising profiles.")
        return []
    # Obtain the Amazon Advertising access token
    token_info = get_amazon_api_access_token(remote_identity_id, ctx=ctx)
    if not token_info or 'access_token' not in token_info:
        logger.warning(f"No access token available for remote identity {remote_identity_id}. Cannot retrieve advertising profiles.")
        return []
    # Use the access token to get the advertising profiles
    headers = {
        "Authorization": f"Bearer {token_info['access_token']}",
        "Amazon-Advertising-API-ClientId": token_info['client_id'],
    }
    response = requests.get(
        f"{AMZADV_REGIONAL_BASE_URLS[remote_identity['region']]}/v2/profiles",
        headers=headers
    )
    if response.status_code == 200:
        profiles = response.json()
        logger.debug(f"Retrieved Amazon Advertising profiles for remote identity {remote_identity_id}: {profiles}")
        return profiles
    else:
        logger.warning(f"Failed to retrieve Amazon Advertising profiles for remote identity {remote_identity_id}: {response.status_code}")
        return []


def get_suggested_table_names(
    query: str,
    ctx: Optional[Context] = None,
) -> List[str] | str:
    """
    Given a query string, obtain a list of possible table names from the rules API (through the service API).

    Args:
        query (str): The SQL query to analyze.
    Returns:
        List[str] | str: A list of possible table names found from the query, or an error message if an invalid key is specified.
    """
    headers = get_auth_headers()
    params = {
        "path": query,
        "latest": "true"
    }
    response = requests.get(
        f"{SERVICE_API_BASE_URL}/service/rules/prod/v1/rules/search",
        params=params,
        headers=headers
    )
    # Extract table names from the response
    table_names = []
    for item in response.json().get("data", []):
        if item.get("attributes", {}):
            # Append the table name with '_master' suffix to ensure use of the master view
            table_names.append(item.get("attributes", {}).get("path").split('/')[-1] + '_master')
    if table_names:
        logger.debug(f"Found table names in query '{query}': {table_names}")
        return table_names
    else:
        logger.debug(f"No table names found in query '{query}'")
        return []


def get_table_rules(
    tablename: str,
    ctx: Optional[Context] = None,
) -> Optional[dict]:
    """
    Get the rules for a given table name from the rules API.

    Args:
        tablename (str): The name of the table to get rules for.
    Returns:
        dict: The rules for the table if found, otherwise None.
    """
    headers = get_auth_headers()
    # Remove the '_master' suffix if present to match the rule path
    if tablename.endswith('_master'):
        tablename = tablename[:-7]
    response = requests.get(
        f"{SERVICE_API_BASE_URL}/service/rules/prod/v1/rules/search?path={tablename}&latest=true",
        headers=headers
    )
    if response.status_code == 200:
        rules = response.json().get("data", [])
        if rules:
            if len(rules) > 1:
                logger.warning(f"Multiple rules found for table {tablename}, determining the best match.")
                if any(rule.get("attributes", {}).get("path").endswith(tablename) for rule in rules):
                    rules = [rule for rule in rules if rule.get("attributes", {}).get("path").endswith(tablename)]
            if rules:
                logger.debug(f"Retrieved rules for table {tablename}: {rules[0]}")
                return rules[0]
        else:
            logger.debug(f"No rules found for table {tablename}")
            return None
    else:
        logger.warning(f"Failed to retrieve rules for table {tablename}: {response.status_code}")
        return None
