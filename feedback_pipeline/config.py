"""Load user configuration and resolve one complete reporting period."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


def load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"配置必须是 YAML 对象：{path.name}")
    return data


def resolve_period(settings: dict, start: str | None = None, end: str | None = None,
                   now: datetime | None = None) -> tuple[str, str]:
    timezone = ZoneInfo(settings.get("timezone", "Asia/Shanghai"))
    if start is None and end is None:
        start, end = settings.get("start"), settings.get("end")
    if start is not None or end is not None:
        if not start or not end:
            raise ValueError("起止日期必须同时填写")
        a, b = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    else:
        days = settings.get("last_complete_days", 7)
        if type(days) is not int or days < 1:
            raise ValueError("last_complete_days 必须为正整数")
        current = (now or datetime.now(timezone)).astimezone(timezone)
        b = current.date() - timedelta(days=1)
        a = b - timedelta(days=days - 1)
    if a > b:
        raise ValueError("开始日期不能晚于结束日期")
    return a.isoformat(), b.isoformat()


def load_config(path: Path) -> dict:
    cfg = load_yaml(path)
    if not isinstance(cfg.get("app"), dict) or not cfg["app"].get("name"):
        raise ValueError("请填写 app.name")
    sources = cfg.get("sources", {})
    files = sources.get("files", [])
    if not isinstance(files, list):
        raise ValueError("sources.files 必须为列表")
    ids = []
    for item in files:
        if not isinstance(item, dict) or not item.get("id") or not item.get("path"):
            raise ValueError("每个文件来源需要 id 和 path")
        ids.append(item["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("文件来源 id 不得重复")
    formats = cfg.get("output", {}).get("formats", ["html", "markdown", "xlsx"])
    if not isinstance(formats, list) or not formats or set(formats) - {"html", "markdown", "xlsx"}:
        raise ValueError("output.formats 当前支持 html、markdown、xlsx")
    mode = cfg.get("analysis", {}).get("mode", "generic")
    if mode not in {"generic", "media"}:
        raise ValueError("analysis.mode 当前支持 generic、media")
    if cfg.get("notifications", {}).get("enabled", False):
        raise ValueError("当前 Candidate 尚未实现通知，请保持 notifications.enabled: false")
    return cfg
