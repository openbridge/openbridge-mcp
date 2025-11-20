from types import SimpleNamespace

from src.server.tools import products


def test_search_products_finds_matches(monkeypatch):
    """Test that search_products finds matching products."""
    monkeypatch.setattr(products, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})
    monkeypatch.setattr(products, "PRODUCT_API_BASE_URL", "https://product.test")

    def fake_get(url, params=None, headers=None, timeout=None):
        assert url == "https://product.test?page_size=1000" or url == "https://product.test"
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "data": [
                    {
                        "id": "50",
                        "attributes": {
                            "name": "Amazon Ads - Sponsored Brands",
                            "worker_name": "amzadsponsoredbrands",
                        }
                    },
                    {
                        "id": "48",
                        "attributes": {
                            "name": "Amazon Ads - Sponsored Products",
                            "worker_name": "amzadsponsoredproducts",
                        }
                    },
                    {
                        "id": "2",
                        "attributes": {
                            "name": "Facebook Page Insights",
                            "worker_name": "fbinsights",
                        }
                    },
                ],
                "links": {"next": None}
            },
        )

    monkeypatch.setattr(products.requests, "get", fake_get)

    results = products.search_products("Amazon Ads Sponsored")

    assert len(results) == 2
    assert results[0]["id"] == 50
    assert results[0]["name"] == "Amazon Ads - Sponsored Brands"
    assert results[1]["id"] == 48


def test_search_products_case_insensitive(monkeypatch):
    """Test that search is case-insensitive."""
    monkeypatch.setattr(products, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})
    monkeypatch.setattr(products, "PRODUCT_API_BASE_URL", "https://product.test")

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "data": [
                    {
                        "id": "50",
                        "attributes": {
                            "name": "Amazon Ads - Sponsored Brands",
                            "worker_name": "amzadsponsoredbrands",
                        }
                    },
                ],
                "links": {"next": None}
            },
        )

    monkeypatch.setattr(products.requests, "get", fake_get)

    # Test lowercase query
    results = products.search_products("amazon ads")
    assert len(results) == 1

    # Test uppercase query
    results = products.search_products("AMAZON ADS")
    assert len(results) == 1

    # Test mixed case
    results = products.search_products("AmAzOn AdS")
    assert len(results) == 1


def test_search_products_no_matches(monkeypatch):
    """Test that search_products returns empty list when no matches."""
    monkeypatch.setattr(products, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})
    monkeypatch.setattr(products, "PRODUCT_API_BASE_URL", "https://product.test")

    def fake_get(url, params=None, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "data": [
                    {
                        "id": "2",
                        "attributes": {
                            "name": "Facebook Page Insights",
                            "worker_name": "fbinsights",
                        }
                    },
                ],
                "links": {"next": None}
            },
        )

    monkeypatch.setattr(products.requests, "get", fake_get)

    results = products.search_products("NonExistentProduct")

    assert results == []


def test_list_product_tables_by_product_id(monkeypatch):
    """Test listing tables for a product by ID."""
    monkeypatch.setattr(products, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})
    monkeypatch.setattr(products, "PRODUCT_API_BASE_URL", "https://product.test")

    def fake_get(url, params=None, headers=None, timeout=None):
        assert url == "https://product.test/50/payloads"
        assert params == {"stage_id__gte": 1000}
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "data": [
                    {
                        "id": "2184",
                        "attributes": {
                            "name": "amzn_ads_sb_campaigns",
                            "stage_id": 1004,
                        }
                    },
                    {
                        "id": "2185",
                        "attributes": {
                            "name": "amzn_ads_sb_adgroups",
                            "stage_id": 1004,
                        }
                    },
                ]
            },
        )

    monkeypatch.setattr(products.requests, "get", fake_get)

    results = products.list_product_tables(product_id=50)

    assert len(results) == 2
    assert results[0]["name"] == "amzn_ads_sb_campaigns"
    assert results[0]["stage_id"] == 1004
    assert results[0]["id"] == 2184


def test_list_product_tables_with_subscription(monkeypatch):
    """Test listing tables filtered by subscription's stage_ids."""
    monkeypatch.setattr(products, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})
    monkeypatch.setattr(products, "PRODUCT_API_BASE_URL", "https://product.test")
    monkeypatch.setattr(products, "SUBSCRIPTIONS_API_BASE_URL", "https://subscriptions.test")

    call_count = {"spm": 0, "payloads": 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        if "spm" in url:
            call_count["spm"] += 1
            assert params == {"subscription": 128853, "data_key": "stage_ids"}
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {
                    "data": [
                        {
                            "attributes": {
                                "product": {"id": 50},
                                "data_value": "[1004, 1005]",
                            }
                        }
                    ]
                },
            )
        elif "payloads" in url:
            call_count["payloads"] += 1
            assert url == "https://product.test/50/payloads"
            assert params == {"stage_id__gte": 1000}
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {
                    "data": [
                        {
                            "id": "2184",
                            "attributes": {
                                "name": "amzn_ads_sb_campaigns",
                                "stage_id": 1004,
                            }
                        },
                        {
                            "id": "2185",
                            "attributes": {
                                "name": "amzn_ads_sb_adgroups",
                                "stage_id": 1005,
                            }
                        },
                        {
                            "id": "2186",
                            "attributes": {
                                "name": "amzn_ads_sb_keywords",
                                "stage_id": 9999,  # Not in subscription's stage_ids
                            }
                        },
                    ]
                },
            )

    monkeypatch.setattr(products.requests, "get", fake_get)

    results = products.list_product_tables(product_id=50, subscription_id=128853)

    assert call_count["spm"] == 1
    assert call_count["payloads"] == 1
    assert len(results) == 2  # Only 1004 and 1005, not 9999
    assert results[0]["name"] == "amzn_ads_sb_campaigns"
    assert results[1]["name"] == "amzn_ads_sb_adgroups"


def test_list_product_tables_subscription_fallback(monkeypatch):
    """Test fallback to legacy subscription path when /spm returns empty."""
    monkeypatch.setattr(products, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})
    monkeypatch.setattr(products, "PRODUCT_API_BASE_URL", "https://product.test")
    monkeypatch.setattr(products, "SUBSCRIPTIONS_API_BASE_URL", "https://subscriptions.test")

    call_count = {"spm": 0, "sub": 0, "payloads": 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        if "spm" in url:
            call_count["spm"] += 1
            # Return empty data to trigger fallback
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {"data": []},
            )
        elif "/sub/" in url:
            call_count["sub"] += 1
            assert url == "https://subscriptions.test/sub/100239"
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {
                    "data": {
                        "attributes": {
                            "product_id": 2,
                        }
                    }
                },
            )
        elif "payloads" in url:
            call_count["payloads"] += 1
            assert url == "https://product.test/2/payloads"
            assert params == {"stage_id__gte": 1000}
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {
                    "data": [
                        {
                            "id": "100",
                            "attributes": {
                                "name": "fb_insights_page",
                                "stage_id": 0,
                            }
                        },
                    ]
                },
            )

    monkeypatch.setattr(products.requests, "get", fake_get)

    results = products.list_product_tables(product_id=2, subscription_id=100239)

    assert call_count["spm"] == 1
    assert call_count["sub"] == 1
    assert call_count["payloads"] == 1
    assert len(results) == 1
    assert results[0]["name"] == "fb_insights_page"
    assert results[0]["stage_id"] == 0


def test_list_product_tables_error_handling(monkeypatch):
    """Test error handling when API calls fail."""
    monkeypatch.setattr(products, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})
    monkeypatch.setattr(products, "PRODUCT_API_BASE_URL", "https://product.test")

    def fake_get_fails(url, headers=None, timeout=None):
        raise Exception("API Error")

    monkeypatch.setattr(products.requests, "get", fake_get_fails)

    results = products.list_product_tables(product_id=50)

    assert len(results) == 1
    assert "error" in results[0]


def test_search_products_pagination(monkeypatch):
    """Test that search_products handles pagination correctly."""
    monkeypatch.setattr(products, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})
    monkeypatch.setattr(products, "PRODUCT_API_BASE_URL", "https://product.test")

    call_count = {"page": 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        call_count["page"] += 1

        if call_count["page"] == 1:
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {
                    "data": [
                        {
                            "id": "1",
                            "attributes": {
                                "name": "Amazon Product 1",
                                "worker_name": "amz1",
                            }
                        },
                    ],
                    "links": {"next": "https://product.test?page=2"}
                },
            )
        else:
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {
                    "data": [
                        {
                            "id": "2",
                            "attributes": {
                                "name": "Amazon Product 2",
                                "worker_name": "amz2",
                            }
                        },
                    ],
                    "links": {"next": None}
                },
            )

    monkeypatch.setattr(products.requests, "get", fake_get)

    results = products.search_products("Amazon")

    assert call_count["page"] == 2
    assert len(results) == 2
