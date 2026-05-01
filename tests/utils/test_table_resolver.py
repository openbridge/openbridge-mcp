import json
from pathlib import Path

from src.utils.table_resolver import (
    canonical_aliases,
    merge_payloads_and_rules,
    parse_rule_item,
    rank_suggestions,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "resolver"


def _load_json(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_parse_rule_item_uses_rules_leaf_as_lookup_key():
    payload = _load_json("rules_search_sp_orders.json")
    parsed = parse_rule_item(payload["data"][1])

    assert parsed["lookup_key"] == "sp_orders_report"
    assert parsed["destination_table"] == "sp_orders_report_v14"
    assert parsed["rules_path"] == "selling-partner/reports-sales/sp_orders_report"


def test_merge_payloads_and_rules_includes_rules_only_entries_without_payload_ids():
    payloads = _load_json("payloads_product_78.json")["data"]
    rules = _load_json("rules_search_sp_orders.json")["data"]

    merged = merge_payloads_and_rules(payloads=payloads, rules=rules)

    by_key = {row["lookup_key"]: row for row in merged}

    assert "sp_orders" in by_key
    assert by_key["sp_orders"]["stage_id"] == 1000
    assert by_key["sp_orders"]["id"] == 2333

    # rules-only entries exist and omit payload-only keys
    assert "sp_orders_report" in by_key
    assert "stage_id" not in by_key["sp_orders_report"]
    assert "id" not in by_key["sp_orders_report"]


def test_rank_suggestions_caps_and_applies_similarity_floor():
    candidates = [
        "sp_orders",
        "sp_orders_report",
        "sp_orders_pii_master",
        "sp_order_items",
        "sp_order_status",
        "sp_ordr",  # typo-near
        "totally_different_table",
    ]

    ranked = rank_suggestions("sp_orders2", candidates, limit=5, min_similarity=0.6)

    assert len(ranked) <= 5
    assert all(item["similarity"] >= 0.6 for item in ranked)
    assert all(item["lookup_key"] != "totally_different_table" for item in ranked)


def test_canonical_aliases_include_master_and_version_forms():
    aliases = canonical_aliases("sp_orders_report", destination_table="sp_orders_report_v14")

    assert "sp_orders_report" in aliases
    assert "sp_orders_report_master" in aliases
    assert "sp_orders_report_v14" in aliases
