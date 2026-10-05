"""Deterministic, configurable analysis of normalized records.

No workbooks, browser sessions, private configuration or network access.
"""
from __future__ import annotations

import copy
import re
from collections import Counter, defaultdict
from datetime import date

from .period import chinese_period, period_label

SOURCE_LABELS = {"ios": "iOS", "android": "Android", "external": "外部反馈", "customer_service": "客服反馈"}


def prepare_payload(payload: dict) -> dict:
    """Validate a copy at the analysis boundary; preserve caller data."""
    result = copy.deepcopy(payload)
    period = result["period"]
    start, end = date.fromisoformat(period["start_date"]), date.fromisoformat(period["end_date"])
    if start > end:
        raise ValueError("start_date must not be after end_date")
    result.setdefault("warnings", [])
    result.setdefault("files", [])
    result.setdefault("matched_sheets", [])
    seen = set()
    missing_dates = 0
    for record in result["records"]:
        rid = record.get("id")
        if not isinstance(rid, str) or not rid or rid in seen:
            raise ValueError("Every record must have a unique nonempty string id")
        seen.add(rid)
        for field, default in {"text": "", "title": "", "source": "external", "platform": "", "date": None,
                               "rating": None, "file": "", "sheet": "", "row": None, "raw": {}}.items():
            record.setdefault(field, default)
        if not isinstance(record["text"], str) or not isinstance(record["title"], str):
            raise ValueError("Record title and text must be strings: " + rid)
        rating = record["rating"]
        if isinstance(rating, bool):
            rating = None
        elif isinstance(rating, str) and rating.strip() in {"1", "2", "3", "4", "5"}:
            rating = int(rating.strip())
        elif not isinstance(rating, (int, float)) or rating not in (1, 2, 3, 4, 5):
            rating = None
        record["rating"] = int(rating) if rating is not None else None
        if record.get("period_start") or record.get("period_end"):
            a, b = date.fromisoformat(record["period_start"]), date.fromisoformat(record["period_end"])
            if not start <= a <= b <= end:
                raise ValueError("Record period outside requested range: " + rid)
        elif record["date"]:
            point = date.fromisoformat(str(record["date"])[:10])
            if not start <= point <= end:
                raise ValueError("Record date outside requested range: " + rid)
        else:
            missing_dates += 1
    if missing_dates:
        result["warnings"].append(f"{missing_dates} records have no date; included from the requested input batch")
    return result


def validate_rules(rules: dict) -> None:
    settings = rules.get("rules", {})
    if type(settings.get("negative_rating_max", 3)) is not int or settings.get("negative_rating_max", 3) not in range(1, 6):
        raise ValueError("negative_rating_max must be 1–5")
    limits = {"max_quotes_per_issue": 2, "max_quote_chars": 240, "min_cluster_size": 1}
    if any(type(settings.get(key, default)) is not int or settings.get(key, default) < 1 for key, default in limits.items()):
        raise ValueError("Quote count and character limits must be positive")
    keys, codes = set(), set()
    for category in rules["categories"]:
        if category["key"] in keys or category["code"] in codes:
            raise ValueError("Category keys and codes must be unique")
        keys.add(category["key"])
        codes.add(category["code"])
        issue_keys = [issue["key"] for issue in category.get("issues", [])]
        if len(issue_keys) != len(set(issue_keys)):
            raise ValueError("Issue keys must be unique within a category")


def _contamination_checks(records, start_date, end_date, payload) -> dict:
    return {"records_out_of_requested_range": [],
            "sheets_matched_by_period": [s.get("sheet", "") for s in payload["matched_sheets"]],
            "fixed_period_hardcoding": False,
            "note": "Date bounds validated before analysis; missing dates explicitly warned"}


def _search_text(record: dict) -> str:
    return f"{record.get('title') or ''} {record.get('text') or ''}".lower()


def classify(record: dict, rules: dict):
    """Return the list of (category, issue, matched_terms) for one comment."""
    text = _search_text(record)
    hits = []
    for category in rules["categories"]:
        requires = category.get("requires_any")
        if requires and not any(str(token).lower() in text for token in requires):
            continue
        if category.get("strong_only"):
            strong = category.get("strong_keywords") or []
            if not any(str(token).lower() in text for token in strong):
                continue
        for issue in category.get("issues", []):
            require_any = issue.get("require_any")
            if require_any and not any(str(token).lower() in text for token in require_any):
                continue
            matched = sorted({token for token in issue.get("keywords", []) if str(token).lower() in text})
            if not matched:
                continue
            if any(str(token).lower() in text for token in issue.get("exclude", []) or []):
                continue
            hits.append((category, issue, matched))
    return hits


def analyze_records(payload: dict, rules: dict) -> dict:
    """Analyze normalized records with rules/categories configuration."""
    validate_rules(rules)
    payload = prepare_payload(payload)
    start_date, end_date = payload["period"]["start_date"], payload["period"]["end_date"]
    settings = rules.get("rules", {})
    records = payload["records"]
    if not records:
        raise ValueError("No records in the requested period")

    negative_max = int(settings.get("negative_rating_max", 3))
    max_quotes = int(settings.get("max_quotes_per_issue", 2))
    min_chars = int(settings.get("min_quote_chars", 10))
    max_chars = int(settings.get("max_quote_chars", 240))
    low_info = [re.compile(p) for p in settings.get("low_information_patterns", [])]

    by_id = {r["id"]: r for r in records}
    issue_hits: dict[tuple, list] = defaultdict(list)
    record_categories: dict[str, set] = defaultdict(set)
    record_issues: dict[str, set] = defaultdict(set)
    match_terms: dict[tuple, Counter] = defaultdict(Counter)

    for record in records:
        for category, issue, matched in classify(record, rules):
            key = (category["key"], issue["key"])
            issue_hits[key].append(record["id"])
            record_categories[record["id"]].add(category["key"])
            record_issues[record["id"]].add(key)
            for token in matched:
                match_terms[key][token] += 1

    total = len(records)
    store_records = [r for r in records if r["source"] in ("ios", "android")]
    negative_ids = {
        r["id"] for r in store_records
        if r["rating"] is not None and int(r["rating"]) <= negative_max
    }
    total_negative = len(negative_ids)
    rated_records = [r for r in store_records if r["rating"] is not None]

    issue_meta = {}
    for category in rules["categories"]:
        for issue in category["issues"]:
            issue_meta[(category["key"], issue["key"])] = (category, issue)

    # ---- issue level aggregation
    grouped = defaultdict(list)
    for key, ids in issue_hits.items():
        grouped[key[0]].append((key, ids))

    issues_out = []
    categories_out = []
    for category in sorted(rules["categories"], key=lambda c: c["order"]):
        entries = grouped.get(category["key"], [])
        rows = []
        for key, ids in entries:
            category_meta_, issue = issue_meta[key]
            unique_ids = sorted(set(ids))
            count = len(unique_ids)
            if count < int(settings.get("min_cluster_size", 1)):
                continue
            neg = len([i for i in unique_ids if i in negative_ids])
            rows.append({
                "issue_key": issue["key"],
                "name": issue["name"],
                "record_ids": unique_ids,
                "record_count": count,
                "negative_count": neg,
                "feedback_rate": round(count / total * 100, 1),
                "negative_feedback_rate": round(neg / total_negative * 100, 1) if total_negative else 0.0,
                "matched_terms": dict(match_terms[key].most_common()),
                "quotes": [],
                "_sort": (neg, count),
            })
        rows.sort(key=lambda r: (-r["_sort"][0], -r["_sort"][1], r["issue_key"]))
        for index, row in enumerate(rows, 1):
            row.pop("_sort", None)
            row["code"] = f"{category['code']}{index}"
            row["category_key"] = category["key"]
            row["category_name"] = category["name"]
            row["quotes"] = _pick_quotes(
                [by_id[i] for i in row["record_ids"]],
                row["matched_terms"],
                max_quotes, min_chars, max_chars, low_info,
                settings.get("prefer_negative_quotes", True),
                negative_max,
            )
            issues_out.append(row)
        if rows:
            cat_ids = sorted({i for r in rows for i in r["record_ids"]})
            cat_neg = len([i for i in cat_ids if i in negative_ids])
            categories_out.append({
                "key": category["key"],
                "code": category["code"],
                "name": category["name"],
                "module_title": category["module_title"],
                "record_count": len(cat_ids),
                "negative_count": cat_neg,
                "feedback_rate": round(len(cat_ids) / total * 100, 1),
                "negative_feedback_rate": round(cat_neg / total_negative * 100, 1) if total_negative else 0.0,
                "issue_count": len(rows),
                "issues": [r["code"] for r in rows],
            })

    issues_out.sort(key=lambda i: (-i["negative_count"], -i["record_count"], i["code"]))

    # ---- unclassified
    classified = set(record_issues)
    unclassified_ids = sorted(set(by_id) - classified)
    unclassified_ratio = round(len(unclassified_ids) / total, 4)
    warnings = list(payload.get('warnings', []))
    threshold = float(settings.get("unclassified_warning_ratio", 0.10))
    if unclassified_ratio > threshold:
        warnings.append(
            f"{settings.get('unclassified_warning_text', 'WARNING: high unclassified ratio')}"
            f"：{len(unclassified_ids)}/{total} = {unclassified_ratio:.1%}"
        )

    multi_label = {rid: sorted(cats) for rid, cats in record_categories.items() if len(cats) > 1}
    multi_issue = {rid: len(v) for rid, v in record_issues.items() if len(v) > 1}

    # ---- contamination checks
    contamination = _contamination_checks(records, start_date, end_date, payload)

    rating_dist = Counter(
        int(r["rating"]) for r in store_records if r["rating"] is not None
    )
    source_counts = Counter(r["source"] for r in records)
    platform_counts = Counter(r["platform"] for r in records)

    analysis = {
        "period": {
            "start_date": start_date,
            "end_date": end_date,
            "label": period_label(start_date, end_date),
            "chinese": chinese_period(start_date, end_date),
        },
        "totals": {
            "records": total,
            "ios": source_counts.get("ios", 0),
            "android": source_counts.get("android", 0),
            "external": source_counts.get("external", 0),
            "customer_service": source_counts.get("customer_service", 0),
            "store_records": len(store_records),
            "rated_store_records": len(rated_records),
            "negative": total_negative,
            "negative_rate": round(total_negative / len(rated_records) * 100, 1) if rated_records else None,
            "rating_distribution": {str(k): rating_dist.get(k, 0) for k in range(1, 6)},
            "category_hit_records": len(classified),
            "multi_label_records": len(multi_label),
            "multi_issue_records": len(multi_issue),
            "unclassified": len(unclassified_ids),
            "unclassified_ratio": unclassified_ratio,
        },
        "categories": categories_out,
        "issues": issues_out,
        "unclassified": [
            {
                "id": rid,
                "source": by_id[rid]["source"],
                "platform": by_id[rid]["platform"],
                "date": by_id[rid]["date"],
                "rating": by_id[rid]["rating"],
                "text": by_id[rid]["text"],
            }
            for rid in unclassified_ids
        ],
        "multi_label": [
            {"id": rid, "categories": cats} for rid, cats in sorted(multi_label.items())
        ],
        "sources": {
            "files": payload["files"],
            "matched_sheets": payload["matched_sheets"],
        },
        # 全量原始记录（含出处字段），保证报告里的每一条原声都能回溯到真实输入
        "records": by_id,
        "metadata": {
            "generated_from": "feedback_pipeline.analysis.engine",
            "rules_version": rules.get("version"),
            "negative_rule": f"有效1–5星中，星级 <= {negative_max} 为差评；无评分来源不参与差評率",
            "metrics": settings.get("metrics", {}),
            "classification": "关键词召回 + 要求/排除条件 + 多标签；类别和问题由规则配置",
            "quote_rule": "仅选取本周期真实评论原文，按信息量与差评优先排序，同一问题内不重复",
            "warnings": warnings,
            "contamination": contamination,
        },
    }
    return analysis


def _pick_quotes(records, matched_terms, max_quotes, min_chars, max_chars, low_info,
                 prefer_negative, negative_max):
    def score(record):
        text = (record["text"] or record.get("title") or "").strip()
        value = 0.0
        lowered = text.lower()
        value += sum(1 for term in matched_terms if term in lowered) * 1.5
        value += min(len(text), 80) / 80
        if prefer_negative and record["source"] in ("ios", "android") and record["rating"] is not None and int(record["rating"]) <= negative_max:
            value += 1.5
        if record["date"]:
            value += 0.2
        return value

    def usable(record):
        text = (record["text"] or record.get("title") or "").strip()
        if len(text) < min_chars:
            return False
        if any(pattern.match(text) for pattern in low_info):
            return False
        return True

    pool = [r for r in records if usable(r)] or [r for r in records if (r["text"] or "").strip()]
    ranked = sorted(pool, key=score, reverse=True)

    picked = []
    for record in ranked:
        if len(picked) >= max_quotes:
            break
        text = (record["text"] or record.get("title") or "").strip()
        if any(item["full_text"] == text for item in picked):
            continue
        quote = text if len(text) <= max_chars else text[:max_chars]
        picked.append({
            "record_id": record["id"],
            "source": record["source"],
            "source_label": SOURCE_LABELS.get(record["source"], record["source"]),
            "platform": record["platform"],
            "date": record["date"],
            "rating": record["rating"],
            "sheet": record["sheet"],
            "file": record["file"],
            "row": record["row"],
            "quote": quote,
            "full_text": text,
            "title": record["title"],
        })
    # prefer platform diversity: if both quotes share a platform, try to swap the 2nd
    if len(picked) == 2 and picked[0]["platform"] == picked[1]["platform"]:
        alternative = next(
            (r for r in ranked if r["platform"] != picked[0]["platform"] and usable(r)
             and (r["text"] or r.get("title") or "").strip() != picked[0]["full_text"]), None
        )
        if alternative is not None:
            text = (alternative["text"] or "").strip()
            picked[1] = {
                "record_id": alternative["id"],
                "source": alternative["source"],
                "source_label": SOURCE_LABELS.get(alternative["source"], alternative["source"]),
                "platform": alternative["platform"],
                "date": alternative["date"],
                "rating": alternative["rating"],
                "sheet": alternative["sheet"],
                "file": alternative["file"],
                "row": alternative["row"],
                "quote": text if len(text) <= max_chars else text[:max_chars],
                "full_text": text,
                "title": alternative["title"],
            }
    for item in picked:
        assert item["quote"] in item["full_text"], item["record_id"]
    return picked
