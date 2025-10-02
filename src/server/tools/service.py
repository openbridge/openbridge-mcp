import requests

from src.utils.logging import get_logger
from .base import get_auth_headers
from .remote_identity import get_remote_identity_by_id
from typing import Dict, List, Optional
import os

logger = get_logger("service")


AMZADV_REGIONAL_BASE_URLS = {
    "na": "https://advertising-api.amazon.com",
    "eu": "https://advertising-api-eu.amazon.com",
    "fe": "https://advertising-api-fe.amazon.com",
}

def execute_query(query: str, key_name: str): 
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
        f"{os.getenv('SERVICE_API_BASE_URL')}/service/query/production/query",
        json=payload,
        headers=headers
    )
    print("Response from Service API: " + str(response.status_code) + " - " + response.text)
    if response.status_code == 200:
        data = response.json().get("data", [])
        logger.debug(f"Executed query: {query}. Response: {data}")
        return data
    else:
        logger.warning(f"Failed to execute query: {query}, response: {response.status_code} {response.text}")
        return [{"error": f"Failed to execute query: {response.status_code} {response.text}"}]

# Note: This function should typically be used internally rather than called as a tool.
def get_amazon_api_access_token(remote_identity_id: int) -> dict:
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
        f"{os.getenv('SERVICE_API_BASE_URL')}/service/amzadv/token/{remote_identity_id}",
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

def get_amazon_advertising_profiles(remote_identity_id: int) -> List[dict]:
    """
    List the Amazon Advertising profiles for a given remote identity ID.

    Args:
        remote_identity_id (int): The ID of the remote identity.
    Returns:
        List[dict]: A list of Amazon Advertising profiles.
    """
    # Obtain the remote identity
    remote_identity = get_remote_identity_by_id(remote_identity_id)
    if not remote_identity:
        logger.warning(f"Remote identity {remote_identity_id} not found. Cannot retrieve advertising profiles.")
        return []
    # Obtain the Amazon Advertising access token
    token_info = get_amazon_api_access_token(remote_identity_id)
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


def get_suggested_table_names(query: str) -> List[str] | str:
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
        f"{os.getenv('SERVICE_API_BASE_URL')}/service/rules/prod/v1/rules/search",
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


def get_table_rules(tablename: str) -> Optional[dict]:
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
        print("Stripped '_master' suffix, now looking for rules for table: " + tablename)
    response = requests.get(
        f"{os.getenv('SERVICE_API_BASE_URL')}/service/rules/prod/v1/rules/search?path={tablename}&latest=true",
        headers=headers
    )
    print("Response from Rules API: " + str(response.status_code) + " - " + response.text)
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
