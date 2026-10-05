"""One report plan for statistics, representative voices and row-level audit."""
from __future__ import annotations

import copy
import ipaddress
import re
from html import escape
from urllib.parse import urlsplit

CONTACT_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
CONTACT_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def full_text(record: dict) -> str:
    if record.get("title"):
        return f"标题：{record['title']}\n内容：{record['text']}"
    return record["text"]


def provenance(record: dict) -> str:
    locations = record.get("locations") or [{"file": record["file"], "sheet": record.get("sheet", ""),
                                            "row": record["row"], "row_end": record["row"]}]
    parts = []
    for loc in locations:
        row = str(loc["row"])
        if loc.get("row_end", loc["row"]) != loc["row"]:
            row += "–" + str(loc["row_end"])
        sheet = f" / {loc['sheet']}" if loc.get("sheet") else ""
        parts.append(f"{loc['file']}{sheet}，第 {row} 行")
    return "；".join(parts)


def validate_analysis(analysis: dict) -> None:
    records = analysis["records"]
    codes = set()
    for issue in analysis["issues"]:
        if issue["code"] in codes:
            raise ValueError("报告问题编号重复")
        codes.add(issue["code"])
        ids = issue.get("matched_record_ids") or issue["record_ids"]
        if len(ids) != len(set(ids)) or any(rid not in records for rid in ids):
            raise ValueError("问题记录集合无效")
        if issue["record_count"] != len(ids):
            raise ValueError("问题计数与记录集合不一致")
        for quote in issue.get("quotes", []):
            rid, text = quote["record_id"], quote["quote"]
            if rid not in ids or not text or text not in full_text(records[rid]):
                raise ValueError("原声必须来自该问题的输入原文")


def _redact(text: str) -> str:
    return CONTACT_PHONE.sub("[联系方式已遮盖]", CONTACT_EMAIL.sub("[邮箱已遮盖]", text))


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
        return ""
    if host == "localhost" or "." not in host or host.endswith((".local", ".internal", ".lan")):
        return ""
    try:
        if not ipaddress.ip_address(host).is_global:
            return ""
    except ValueError:
        pass
    return url


def build_report_plan(analysis: dict, privacy: dict, max_audit_rows: int = 5) -> dict:
    validate_analysis(analysis)
    if type(max_audit_rows) is not int or max_audit_rows < 1:
        raise ValueError("max_audit_rows 必须是正整数")
    plan = copy.deepcopy(analysis)
    for issue in plan["issues"]:
        ids = issue.get("matched_record_ids") or issue["record_ids"]
        chosen = []
        for quote in issue.get("quotes", []):
            if quote["record_id"] not in chosen:
                chosen.append(quote["record_id"])
        channels = {plan["records"][rid]["platform"] for rid in chosen}
        for rid in ids:
            platform = plan["records"][rid]["platform"]
            if platform not in channels and len(chosen) < max_audit_rows:
                chosen.append(rid)
                channels.add(platform)
        chosen.extend(rid for rid in ids if rid not in chosen)
        issue["audit_record_ids"] = chosen[:max_audit_rows]
        issue["record_ids"] = list(ids)
    if privacy.get("redact_contacts", True):
        # A length-limited quote can cut a phone/email in half. Expand only
        # overlapping contact boundaries so masking still matches source text.
        for issue in plan["issues"]:
            for quote in issue.get("quotes", []):
                source = full_text(plan["records"][quote["record_id"]])
                start = source.index(quote["quote"])
                end = start + len(quote["quote"])
                for pattern in (CONTACT_PHONE, CONTACT_EMAIL):
                    for match in pattern.finditer(source):
                        if match.start() < end and match.end() > start:
                            start, end = min(start, match.start()), max(end, match.end())
                quote["quote"] = source[start:end]
        text_fields = {"text", "quote", "full_text", "title", "内容", "标题", "name", "raw_name",
                       "module_title", "fragment", "description"}
        # Evidence lists and record-keyed audit maps also contain source text.
        # Redact values in these containers, preserving IDs and structural keys.
        text_containers = {"evidence_samples", "audit_texts", "origin_issue_names", "from"}
        def walk(value, field="", text_values=False):
            text_values = text_values or field in text_containers
            if isinstance(value, str):
                return _redact(value) if text_values or field in text_fields else value
            if isinstance(value, dict):
                return {k: walk(v, k, text_values) for k, v in value.items()}
            if isinstance(value, list):
                return [walk(v, field, text_values) for v in value]
            return value
        plan = walk(plan)
        validate_analysis(plan)
    for record in plan["records"].values():
        record["url"] = safe_url(record.get("url", "")) if privacy.get("include_source_urls", False) else ""
    plan["privacy"] = {"redact_contacts": privacy.get("redact_contacts", True),
                       "include_source_urls": privacy.get("include_source_urls", False)}
    return plan


def html_text(value) -> str:
    return escape(str(value)).replace("\n", "<br>")
