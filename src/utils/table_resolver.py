from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

_VERSION_SUFFIX_RE = re.compile(r"_v\d+$", re.IGNORECASE)


def normalize_lookup_token(name: str) -> str:
    """Normalize table identifiers for alias matching.

    Removes transport variations commonly seen by callers:
    - trailing version suffixes like ``_v14``
    - trailing ``_master``
    """
    value = (name or "").strip().lower()
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    value = _VERSION_SUFFIX_RE.sub("", value)
    if value.endswith("_master"):
        value = value[:-7]
    return value


def canonical_aliases(
    lookup_key: str,
    *,
    destination_table: Optional[str] = None,
    payload_name: Optional[str] = None,
) -> List[str]:
    """Build deterministic alias set for a canonical lookup key."""
    aliases = set()

    def _add(value: Optional[str]) -> None:
        if not value:
            return
        v = value.strip().lower()
        if not v:
            return
        aliases.add(v)

    _add(lookup_key)
    _add(destination_table)
    _add(payload_name)

    normalized = normalize_lookup_token(lookup_key)
    _add(normalized)
    if normalized:
        _add(f"{normalized}_master")

    for value in (destination_table, payload_name):
        if not value:
            continue
        normalized_value = normalize_lookup_token(value)
        _add(normalized_value)
        if normalized_value:
            _add(f"{normalized_value}_master")

    return sorted(aliases)


def parse_rule_item(rule: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a rules API row into canonical discovery shape."""
    attributes = rule.get("attributes") if isinstance(rule, dict) else None
    if not isinstance(attributes, dict):
        return None

    path = attributes.get("path")
    if not isinstance(path, str) or not path.strip():
        return None

    lookup_key = path.rsplit("/", 1)[-1].strip().lower()
    destination = attributes.get("destination")
    destination_table: Optional[str] = None
    if isinstance(destination, dict):
        tablename = destination.get("tablename")
        if isinstance(tablename, str) and tablename.strip():
            destination_table = tablename.strip().lower()

    return {
        "lookup_key": lookup_key,
        "destination_table": destination_table,
        "rules_path": path,
        "aliases": canonical_aliases(lookup_key, destination_table=destination_table),
        "rule": rule,
        "source": "rules",
    }


def _parse_payload_item(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize product payload rows from either raw or preformatted shape."""
    name: Optional[str] = None
    stage_id: Optional[int] = None
    payload_id: Optional[int] = None

    if isinstance(payload.get("attributes"), dict):
        attributes = payload["attributes"]
        raw_name = attributes.get("name")
        if isinstance(raw_name, str):
            name = raw_name.strip().lower()
        if isinstance(attributes.get("stage_id"), int):
            stage_id = attributes["stage_id"]
        raw_id = payload.get("id")
        if raw_id is not None:
            try:
                payload_id = int(raw_id)
            except (TypeError, ValueError):
                payload_id = None
    else:
        raw_name = payload.get("name")
        if isinstance(raw_name, str):
            name = raw_name.strip().lower()
        if isinstance(payload.get("stage_id"), int):
            stage_id = payload["stage_id"]
        raw_id = payload.get("id")
        if raw_id is not None:
            try:
                payload_id = int(raw_id)
            except (TypeError, ValueError):
                payload_id = None

    if not name:
        return None

    row: Dict[str, Any] = {
        "lookup_key": name,
        "aliases": canonical_aliases(name, payload_name=name),
        "source": "payload",
    }
    if stage_id is not None:
        row["stage_id"] = stage_id
    if payload_id is not None:
        row["id"] = payload_id
    return row


def merge_payloads_and_rules(
    payloads: Iterable[Dict[str, Any]],
    rules: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge payload and rules discovery into one table list.

    Rules rows without payload backing are included as rules-only discoverables.
    """
    payload_rows = [row for row in (_parse_payload_item(p) for p in payloads) if row is not None]
    rule_rows = [row for row in (parse_rule_item(r) for r in rules) if row is not None]

    rules_by_lookup = {row["lookup_key"]: row for row in rule_rows}
    rules_by_token: Dict[str, List[Dict[str, Any]]] = {}
    for row in rule_rows:
        token = normalize_lookup_token(row["lookup_key"])
        rules_by_token.setdefault(token, []).append(row)

    merged: List[Dict[str, Any]] = []
    matched_rule_keys = set()

    for payload_row in payload_rows:
        token = normalize_lookup_token(payload_row["lookup_key"])
        same_token = rules_by_token.get(token, [])

        chosen_rule: Optional[Dict[str, Any]] = None
        if payload_row["lookup_key"] in rules_by_lookup:
            chosen_rule = rules_by_lookup[payload_row["lookup_key"]]
        elif same_token:
            chosen_rule = sorted(same_token, key=lambda item: item["lookup_key"])[0]

        merged_row = dict(payload_row)
        if chosen_rule is not None:
            matched_rule_keys.add(chosen_rule["lookup_key"])
            merged_row["lookup_key"] = chosen_rule["lookup_key"]
            merged_row["aliases"] = sorted(set(payload_row["aliases"]) | set(chosen_rule["aliases"]))
            merged_row["rules_path"] = chosen_rule.get("rules_path")
            destination_table = chosen_rule.get("destination_table")
            if destination_table is not None:
                merged_row["destination_table"] = destination_table
            merged_row["source"] = "payload+rules"

        merged.append(merged_row)

    for rule_row in rule_rows:
        if rule_row["lookup_key"] in matched_rule_keys:
            continue
        rules_only = {
            "lookup_key": rule_row["lookup_key"],
            "aliases": rule_row["aliases"],
            "rules_path": rule_row.get("rules_path"),
            "source": "rules",
        }
        destination_table = rule_row.get("destination_table")
        if destination_table is not None:
            rules_only["destination_table"] = destination_table
        merged.append(rules_only)

    return sorted(merged, key=lambda item: item["lookup_key"])


def levenshtein_similarity(left: str, right: str) -> float:
    """Return normalized Levenshtein similarity in [0, 1]."""
    a = (left or "").strip().lower()
    b = (right or "").strip().lower()

    if a == b:
        return 1.0
    if not a or not b:
        return 0.0

    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            insertions = previous[j] + 1
            deletions = current[j - 1] + 1
            substitutions = previous[j - 1] + (0 if char_a == char_b else 1)
            current.append(min(insertions, deletions, substitutions))
        previous = current

    distance = previous[-1]
    max_len = max(len(a), len(b))
    return 1.0 - (distance / max_len)


def rank_suggestions(
    query: str,
    candidates: Iterable[str],
    *,
    limit: int = 5,
    min_similarity: float = 0.6,
) -> List[Dict[str, Any]]:
    """Rank candidate lookup keys for typo recovery."""
    normalized_query = normalize_lookup_token(query)
    seen = set()
    ranked: List[Dict[str, Any]] = []

    for candidate in candidates:
        key = (candidate or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)

        similarity = levenshtein_similarity(normalized_query, normalize_lookup_token(key))
        if similarity < min_similarity:
            continue

        ranked.append({
            "lookup_key": key,
            "similarity": round(similarity, 3),
        })

    ranked.sort(key=lambda item: (-item["similarity"], item["lookup_key"]))
    return ranked[:limit]


def find_matching_rule(
    requested_table_name: str,
    parsed_rules: Iterable[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Find deterministic best rule match for input alias."""
    requested = (requested_table_name or "").strip().lower()
    requested_token = normalize_lookup_token(requested)
    rows = list(parsed_rules)

    exact_alias_matches = [
        row
        for row in rows
        if requested in {alias.lower() for alias in row.get("aliases", [])}
    ]
    if exact_alias_matches:
        exact_alias_matches.sort(key=lambda item: item["lookup_key"])
        return exact_alias_matches[0]

    token_matches = [
        row for row in rows if normalize_lookup_token(row.get("lookup_key", "")) == requested_token
    ]
    if len(token_matches) == 1:
        return token_matches[0]

    if len(token_matches) > 1:
        strict_lookup = [row for row in token_matches if row.get("lookup_key") == requested]
        if strict_lookup:
            strict_lookup.sort(key=lambda item: item["lookup_key"])
            return strict_lookup[0]

    return None
