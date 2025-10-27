from src.utils.logging import get_logger
from .base import get_auth_headers
from typing import List, Optional
import requests
import os
from fastmcp.server.context import Context

logger = get_logger("products")

PRODUCT_API_BASE_URL = os.getenv("PRODUCT_API_BASE_URL", 'https://service.api.openbridge.io/service/products/product')

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
    response = requests.get(f"{PRODUCT_API_BASE_URL}/{product_id}/payloads", headers=headers, params=params)
    if response.status_code == 200:
        product_stage_ids = response.json().get("data", [])
        logger.debug(f"Retrieved product stage IDs for {product_id}: {product_stage_ids}")
        return product_stage_ids
    else:
        logger.warning(f"Failed to retrieve product stage IDs for {product_id}: {response.status_code}")
        return [{"error": f"Failed to retrieve product stage IDs: {response.status_code} {response.text}"}]
