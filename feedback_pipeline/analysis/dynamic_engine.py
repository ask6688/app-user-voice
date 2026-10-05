"""Optional media/download profile; modules are explicitly P/W/M/D/N."""
from __future__ import annotations

import re
from collections import Counter

from .dynamic import MODULES, build_dynamic_issues, route_record
from .eligibility import check_eligibility
from .engine import analyze_records
from .media_report import IssueReportProcessor


def analyze_dynamic_records(payload: dict, rules: dict) -> dict:
    """Reuse local character clustering, with exact evidence and full counts.

    This optional profile targets playback, browser/search, membership,
    download and version feedback. It is not an arbitrary-app classifier.
    """
    settings = dict(rules.get("rules", {}))
    base_settings = {**settings, "unclassified_warning_ratio": 1.0}
    result = analyze_records(payload, {"version": rules.get("version"),
                                      "rules": base_settings, "categories": []})
    records = result["records"]
    negative_max = int(settings.get("negative_rating_max", 3))
    negative_ids = {rid for rid, r in records.items() if r["source"] in ("ios", "android")
                    and r["rating"] is not None and r["rating"] <= negative_max}
    patterns = [re.compile(p, re.I) for p in settings.get("low_information_patterns", [])]
    assignments, rejected, eligible_ids = [], [], set()
    for rid, record in records.items():
        eligible, reason = check_eligibility(record, patterns)
        if not eligible:
            rejected.append({"id": rid, "reason": reason, **{k: record[k] for k in ("source", "platform", "date", "text")}})
            continue
        eligible_ids.add(rid)
        source = (record["title"] + " " + record["text"]).strip()
        for item in route_record(record):
            if item["fragment"] not in source:
                raise ValueError("Routed fragment is not literal source text: " + rid)
            assignments.append(item)

    issues, backends = build_dynamic_issues(assignments, negative_ids)
    audit = None
    if settings.get("media_report_quality", True):
        # Analysis keeps every validated scene; display caps belong to exporters.
        processor = IssueReportProcessor(records, assignments, max_issues=max(1, len(assignments)),
                                         negative_ids=negative_ids)
        issues = [issue for key in MODULES
                  for issue in processor.process([i for i in issues if i["category_key"] == key], key)]
        audit = processor.audit

    total, total_negative = len(records), len(negative_ids)
    max_quotes = int(settings.get("max_quotes_per_issue", 2))
    max_chars = int(settings.get("max_quote_chars", 240))
    categories, classified, record_categories = [], set(), {}
    for key, meta in sorted(MODULES.items(), key=lambda row: row[1]["order"]):
        rows = [issue for issue in issues if issue["category_key"] == key]
        rows.sort(key=lambda issue: (-issue.get("quality_score", 0), -len(set(issue["record_ids"])), issue["issue_key"]))
        for index, issue in enumerate(rows, 1):
            ids = sorted(set(issue["record_ids"]))
            if any(rid not in records for rid in ids):
                raise ValueError("Issue refers to an unknown record")
            issue.update(record_ids=ids, record_count=len(ids), negative_count=len(set(ids) & negative_ids),
                         code=f'{meta["code"]}{index}', category_name=meta["name"],
                         feedback_rate=round(len(ids) / total * 100, 1),
                         negative_feedback_rate=round(len(set(ids) & negative_ids) / total_negative * 100, 1) if total_negative else 0.0)
            source_counts = Counter(records[rid]["source"] for rid in ids)
            issue.update(sources=sorted(source_counts), source_counts=dict(source_counts))
            quotes, seen = [], set()
            for quote in issue.get("quotes", []):
                rid, text = quote["record_id"], quote["quote"]
                if rid not in ids or not text:
                    raise ValueError("Quote lacks issue membership or text")
                record = records[rid]
                if text not in record["text"] and text not in record["title"]:
                    # Joining title/body for routing must not create a new quote.
                    body = text.removeprefix(record["title"]).strip() if record["title"] else text
                    if not body or body not in record["text"]:
                        raise ValueError("Quote is not literal source text: " + rid)
                    text = body
                text = text[:max_chars]
                if (rid, text) in seen:
                    continue
                seen.add((rid, text))
                quotes.append({**quote, "quote": text,
                               **{k: record[k] for k in ("source", "platform", "date", "rating", "file", "sheet", "row")}})
                if len(quotes) >= max_quotes:
                    break
            if not quotes and ids:
                rid = ids[0]
                record = records[rid]
                text = record["text"] or record["title"]
                quotes = [{"record_id": rid, "quote": text[:max_chars],
                           **{k: record[k] for k in ("source", "platform", "date", "rating", "file", "sheet", "row")}}]
            issue["quotes"] = quotes
            classified.update(ids)
            for rid in ids:
                record_categories.setdefault(rid, set()).add(key)
        if rows:
            ids = set().union(*(set(issue["record_ids"]) for issue in rows))
            categories.append({"key": key, **meta, "record_count": len(ids), "negative_count": len(ids & negative_ids),
                               "feedback_rate": round(len(ids) / total * 100, 1),
                               "negative_feedback_rate": round(len(ids & negative_ids) / total_negative * 100, 1) if total_negative else 0.0,
                               "issue_count": len(rows), "issues": [issue["code"] for issue in rows]})
    unclassified_ids = sorted(set(records) - classified)
    ratio = round(len(unclassified_ids) / total, 4)
    result.update(categories=categories, issues=issues,
                  rejected=rejected, dynamic_routing=assignments,
                  eligibility={"eligible": len(eligible_ids), "rejected": len(rejected),
                               "rejected_by_reason": dict(Counter(row["reason"] for row in rejected))},
                  architecture={"profile": "media", "fixed_modules": [m["code"] for m in MODULES.values()],
                                "dynamic_issue_discovery": True, "cluster_backends": backends},
                  unclassified=[{"id": rid, **{k: records[rid][k] for k in ("source", "platform", "date", "rating", "text")}}
                                for rid in unclassified_ids],
                  multi_label=[{"id": rid, "categories": sorted(keys)} for rid, keys in sorted(record_categories.items()) if len(keys) > 1])
    result["totals"].update(eligible_records=len(eligible_ids), rejected_records=len(rejected),
                            category_hit_records=len(classified), multi_label_records=len(result["multi_label"]),
                            multi_issue_records=sum(count > 1 for count in Counter(rid for issue in issues for rid in issue["record_ids"]).values()),
                            unclassified=len(unclassified_ids), unclassified_ratio=ratio,
                            dynamic_issue_count=len(issues))
    if ratio > float(settings.get("unclassified_warning_ratio", 0.10)):
        result["metadata"]["warnings"].append(f"High unclassified ratio: {len(unclassified_ids)}/{total} ({ratio:.1%})")
    result["metadata"].update(generated_from="feedback_pipeline.analysis.dynamic_engine", profile="media",
                              classification="Optional five-module media routing + local character clustering + evidence QA")
    if audit is not None:
        result["report_processing"] = audit
    return result
