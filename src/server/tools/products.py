import json
import os
from typing import Dict, List, Optional, Tuple

import requests
from fastmcp.server.context import Context

from src.utils.logging import get_logger
from .base import get_api_timeout, get_auth_headers

logger = get_logger("products")

PRODUCT_API_BASE_URL = os.getenv("PRODUCT_API_BASE_URL", 'https://service.api.openbridge.io/service/products/product')
SUBSCRIPTIONS_API_BASE_URL = os.getenv("SUBSCRIPTIONS_API_BASE_URL", 'https://subscriptions.api.openbridge.io')
MAX_PAGES = 100  # Safety limit for pagination

def get_product_stage_ids(
    product_id: Optional[str],
    ctx: Optional[Context] = None,
) -> List[dict]:
    """
    Get the stage IDs for a specific product.
    Args:
        product_id (Optional[str]): The ID of the product to retrieve stage IDs for.
    Returns:
        List[dict]: A list of stage IDs associated with the product, or an error message if the request fails.
    """
    headers = get_auth_headers(ctx)
    params = {
        "stage_id__gte": 1000,  # Assuming stage IDs start from 1000
    }
    response = requests.get(
        f"{PRODUCT_API_BASE_URL}/{product_id}/payloads",
        headers=headers,
        params=params,
        timeout=get_api_timeout(),
    )
    if response.status_code == 200:
        product_stage_ids = response.json().get("data", [])
        logger.debug(f"Retrieved product stage IDs for {product_id}: {product_stage_ids}")
        return product_stage_ids
    else:
        logger.warning(f"Failed to retrieve product stage IDs for {product_id}: {response.status_code}")
        return [{"error": f"Failed to retrieve product stage IDs: {response.status_code} {response.text}"}]


# Helper functions for new table discovery tools

def _fetch_all_products(headers: Dict[str, str]) -> List[dict]:
    """Fetch all products from the Products API with pagination."""
    all_products = []
    page = 1
    current_url = f"{PRODUCT_API_BASE_URL}?page_size=1000"

    while page <= MAX_PAGES:
        try:
            response = requests.get(
                current_url,
                headers=headers,
                timeout=get_api_timeout(),
            )
            response.raise_for_status()
            data = response.json()

            products = data.get("data", [])
            if products:
                all_products.extend(products)

            # Check for next page
            next_url = data.get("links", {}).get("next")
            if next_url and products:
                current_url = next_url
                page += 1
            else:
                break

        except requests.exceptions.RequestException as exc:
            logger.error(f"Failed to fetch products on page {page}: {exc}")
            break

    logger.debug(f"Fetched {len(all_products)} total products")
    return all_products


def _fuzzy_match_products(products: List[dict], query: str) -> List[dict]:
    """
    Fuzzy match products by name with scoring-based ranking.

    Scoring:
    - Exact substring match: 100 points
    - Each matching word: 10 points
    - Minimum threshold: 30% of query words must match
    """
    query_lower = query.lower().strip()
    query_words = query_lower.split()
    scored_matches = []

    for product in products:
        attributes = product.get("attributes", {})
        name = attributes.get("name", "").lower()
        worker_name = attributes.get("worker_name", "").lower() if attributes.get("worker_name") else ""

        # Calculate score for name
        name_score = 0
        if query_lower in name:
            name_score = 100  # Exact substring match
        else:
            matched_words = sum(1 for word in query_words if word in name)
            name_score = matched_words * 10

        # Calculate score for worker_name
        worker_score = 0
        if worker_name:
            if query_lower in worker_name:
                worker_score = 100
            else:
                matched_words = sum(1 for word in query_words if word in worker_name)
                worker_score = matched_words * 10

        # Use best score
        best_score = max(name_score, worker_score)

        # Include if at least 30% of query words matched (or exact match)
        min_score = max(30, len(query_words) * 3)  # At least 30 or 30% of words
        if best_score >= min_score:
            scored_matches.append({
                "score": best_score,
                "id": int(product.get("id")),
                "name": attributes.get("name"),
                "worker_name": attributes.get("worker_name"),
            })

    # Sort by score (highest first) and remove score from output
    scored_matches.sort(key=lambda x: x["score"], reverse=True)
    matches = [
        {"id": m["id"], "name": m["name"], "worker_name": m["worker_name"]}
        for m in scored_matches
    ]

    logger.debug(f"Found {len(matches)} products matching '{query}' (top score: {scored_matches[0]['score'] if scored_matches else 0})")
    return matches


def _fetch_subscription_stage_ids(subscription_id: int, headers: Dict[str, str]) -> Tuple[int, List[int]]:
    """
    Fetch product_id and stage_ids for a subscription.
    Returns: (product_id, [stage_ids])
    """
    # First try to get stage_ids from subscription product meta
    try:
        response = requests.get(
            f"{SUBSCRIPTIONS_API_BASE_URL}/spm",
            params={"subscription": subscription_id, "data_key": "stage_ids"},
            headers=headers,
            timeout=get_api_timeout(),
        )
        response.raise_for_status()
        data = response.json()

        spm_data = data.get("data", [])
        if spm_data:
            # Extract product_id and stage_ids from first result
            attributes = spm_data[0].get("attributes", {})
            product_id = attributes.get("product", {}).get("id")

            # Parse stage_ids from JSON string
            stage_ids_str = attributes.get("data_value", "[]")
            try:
                stage_ids = json.loads(stage_ids_str)
                logger.debug(f"Found stage_ids {stage_ids} for subscription {subscription_id}")
                return product_id, stage_ids
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse stage_ids for subscription {subscription_id}")

    except requests.exceptions.RequestException as exc:
        logger.debug(f"Failed to get stage_ids from /spm for subscription {subscription_id}: {exc}")

    # Fallback: Get product_id from subscription, assume stage_id=0 (legacy)
    try:
        response = requests.get(
            f"{SUBSCRIPTIONS_API_BASE_URL}/sub/{subscription_id}",
            headers=headers,
            timeout=get_api_timeout(),
        )
        response.raise_for_status()
        data = response.json()

        product_id = data.get("data", {}).get("attributes", {}).get("product_id")
        if product_id:
            logger.debug(f"Using legacy path: product_id={product_id}, stage_id=0 for subscription {subscription_id}")
            return product_id, [0]

    except requests.exceptions.RequestException as exc:
        logger.error(f"Failed to get subscription {subscription_id}: {exc}")
        raise ValueError(f"Could not fetch subscription {subscription_id}: {exc}")

    raise ValueError(f"Could not determine product_id for subscription {subscription_id}")


def _fetch_product_payloads(
    product_id: int,
    stage_ids: Optional[List[int]],
    headers: Dict[str, str]
) -> List[dict]:
    """
    Fetch payloads for a product, optionally filtered by stage_ids.
    Returns list of payload dictionaries with name, stage_id, and id.
    """
    try:
        # Add stage_id__gte=1000 filter to avoid duplicates (per product-tables.md)
        params = {"stage_id__gte": 1000}

        response = requests.get(
            f"{PRODUCT_API_BASE_URL}/{product_id}/payloads",
            params=params,
            headers=headers,
            timeout=get_api_timeout(),
        )
        response.raise_for_status()
        data = response.json()

        payloads = data.get("data", [])

        # Filter by stage_ids if provided
        if stage_ids is not None:
            payloads = [
                p for p in payloads
                if p.get("attributes", {}).get("stage_id") in stage_ids
            ]

        # Format results
        results = []
        for payload in payloads:
            attributes = payload.get("attributes", {})
            results.append({
                "name": attributes.get("name"),
                "stage_id": attributes.get("stage_id"),
                "id": int(payload.get("id")),
            })

        logger.debug(f"Found {len(results)} payloads for product {product_id}")
        return results

    except requests.exceptions.RequestException as exc:
        logger.error(f"Failed to fetch payloads for product {product_id}: {exc}")
        return []


# Public MCP tools

def search_products(
    query: str,
    ctx: Optional[Context] = None,
) -> List[dict]:
    """
    Search for Openbridge products by name.

    Performs case-insensitive substring matching against product names.
    Returns matching products with their IDs, which can be used with
    list_product_tables to see available tables.

    Args:
        query: Search term to match against product names (e.g., "Amazon Ads", "Google Analytics")

    Returns:
        List of matching products with id, name, and worker_name fields.

    Example:
        search_products("Amazon Ads Sponsored")
        → [{"id": 50, "name": "Amazon Ads - Sponsored Brands", "worker_name": "amzadsponsoredbrands"}, ...]
    """
    headers = get_auth_headers(ctx)

    try:
        all_products = _fetch_all_products(headers)
        matches = _fuzzy_match_products(all_products, query)

        if not matches:
            logger.info(f"No products found matching '{query}'")
            return []

        return matches

    except Exception as exc:
        logger.error(f"Error searching products: {exc}")
        return [{"error": str(exc)}]


def list_product_tables(
    product_id: int,
    subscription_id: Optional[int] = None,
    ctx: Optional[Context] = None,
) -> List[dict]:
    """
    List tables (payloads) available for a product.

    If subscription_id is provided, filters tables to only those
    enabled for that subscription (based on stage_ids).

    Returns table names that can be used with get_table_schema to
    retrieve detailed schema information.

    Args:
        product_id: The product ID (from search_products)
        subscription_id: Optional subscription ID to filter by stage_ids

    Returns:
        List of tables with name, stage_id, and id fields.

    Example:
        list_product_tables(product_id=50)
        → [{"name": "amzn_ads_sb_campaigns", "stage_id": 1004, "id": 2184}, ...]

        list_product_tables(product_id=50, subscription_id=128853)
        → [{"name": "amzn_ads_sb_campaigns", "stage_id": 1004, "id": 2184}, ...]
          (filtered by subscription's stage_ids)
    """
    headers = get_auth_headers(ctx)

    try:
        # If subscription_id provided, get stage_ids to filter payloads
        stage_ids = None
        if subscription_id is not None:
            try:
                sub_product_id, stage_ids = _fetch_subscription_stage_ids(subscription_id, headers)
                # Verify product_id matches subscription's product
                if sub_product_id != product_id:
                    logger.warning(
                        f"Product ID {product_id} does not match subscription {subscription_id}'s "
                        f"product ID {sub_product_id}. Using subscription's product ID."
                    )
                    product_id = sub_product_id
            except ValueError as exc:
                return [{"error": str(exc)}]

        # Fetch payloads (tables)
        payloads = _fetch_product_payloads(product_id, stage_ids, headers)

        if not payloads:
            logger.info(f"No tables found for product {product_id}")
            return []

        return payloads

    except Exception as exc:
        logger.error(f"Error listing product tables: {exc}")
        return [{"error": str(exc)}]
