"""Configure → import/fetch → analyze → generate a complete local report."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .analysis import analyze_dynamic_records, analyze_records
from .config import load_config, load_yaml, resolve_period
from .exporters import export_report
from .report import build_report_plan
from .sources import load_sources

ROOT = Path(__file__).resolve().parents[1]
MARKETS = {"oppo": ("OPPO", 9), "vivo": ("VIVO", 8), "huawei": ("华为", 6),
           "xiaomi": ("小米", 4), "meizu": ("魅族", 7)}


def qimai_settings(cfg: dict) -> dict:
    app, source = cfg["app"], cfg["sources"]["qimai"]
    platforms = source.get("platforms", ["ios", "android"])
    if not platforms or set(platforms) - {"ios", "android"}:
        raise ValueError("七麦 platforms 必须为 ios/android 的非空列表")
    country = source.get("country", "cn")
    ios_id = str(app.get("ios_app_id", ""))
    android_id = str(source.get("android_app_id", ""))
    if "ios" in platforms and not ios_id:
        raise ValueError("启用七麦 iOS 需填写 app.ios_app_id")
    if "android" in platforms and not android_id:
        raise ValueError("启用七麦 Android 需填写 sources.qimai.android_app_id（七麦内部ID）")
    channels = []
    for item in source.get("android_channels", list(MARKETS)):
        if isinstance(item, str):
            if item not in MARKETS:
                raise ValueError("未知 Android 渠道，请提供 key/name/marketCode 对象")
            name, code = MARKETS[item]
            channels.append({"key": item, "name": name, "marketCode": code})
        else:
            channels.append(item)
    browser = source.get("browser", {})
    return {"app": {"name": app["name"],
                    "ios": {"enabled": "ios" in platforms, "appid": ios_id, "country": country,
                            "commentUrl": f"https://www.qimai.cn/app/comment/appid/{ios_id}/country/{country}"},
                    "android": {"enabled": "android" in platforms, "appid": android_id,
                                "baseCommentUrl": f"https://www.qimai.cn/andapp/comment/appid/{android_id}/market/8",
                                "channels": channels}},
            "qimai": {"platforms": platforms, "headless": browser.get("headless", False),
                      "userDataDir": browser.get("profile", ".browser/qimai-profile"),
                      "manualLoginTimeoutMs": browser.get("manual_login_timeout_ms", 180000),
                      "downloadTimeoutMs": browser.get("download_timeout_ms", 180000),
                      **({"executablePath": browser["executable_path"]} if browser.get("executable_path") else {})}}


def fetch_qimai(cfg: dict, start: str, end: str) -> list[dict]:
    settings = qimai_settings(cfg)
    out = ROOT / "outputs/qimai"
    out.mkdir(parents=True, exist_ok=True)
    node = shutil.which("node")
    if node is None:
        raise ValueError("启用七麦需要 Node.js；请先按 README 安装采集依赖")
    env = {**os.environ, "PYTHON": sys.executable, "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / ".playwright")}
    # Configuration is private to this invocation even before Node acquires
    # its browser/download lock. A concurrent caller cannot change the App.
    with tempfile.TemporaryDirectory(prefix=".runtime-config-", dir=out) as temp:
        runtime_config = Path(temp) / "config.json"
        runtime_config.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        result = subprocess.run([node, str(ROOT / "scripts/fetch-qimai-comments.mjs"),
                                 "--config", str(runtime_config), "--start-date", start, "--end-date", end],
                                cwd=ROOT, env=env, check=False)
        if result.returncode:
            raise ValueError("七麦采集未完整成功，请检查 outputs/qimai 的质量记录；本次不生成完整周报")
    state = json.loads((out / f"qimai_fetch_{start}_{end}.json").read_text())
    entries = []
    for kind, items in [("ios", [state.get("ios")]), ("android", state.get("android", []))]:
        for index, item in enumerate(items):
            if not item:
                continue
            file = item.get("download", {}).get("path")
            if not file:
                raise ValueError("七麦采集记录缺少已校验文件路径")
            entries.append({"id": f"qimai-{kind}-{index}", "path": file, "kind": kind, "header_row": "auto",
                            "platform": item.get("channel", {}).get("name", "iOS") if isinstance(item.get("channel"), dict)
                            else item.get("channel", kind)})
    return entries


def run(config_path: Path, start: str | None = None, end: str | None = None, dry_run: bool = False) -> dict:
    config_path = config_path.resolve()
    cfg = load_config(config_path)
    start, end = resolve_period(cfg.get("period", {}), start, end)
    base = config_path.parent
    mode = cfg.get("analysis", {}).get("mode", "generic")
    rules_path = (base / cfg.get("analysis", {}).get("rules_file", f"config/rules.{mode}.yaml")).resolve()
    rules = load_yaml(rules_path)
    entries = [dict(e) for e in cfg.get("sources", {}).get("files", []) if e.get("enabled", True)]
    qimai_enabled = cfg.get("sources", {}).get("qimai", {}).get("enabled", False)
    if qimai_enabled:
        qimai_settings(cfg)  # Validate enabled credentials/config before any browser starts.
    if not entries and not qimai_enabled:
        raise ValueError("至少启用一个文件来源或七麦采集")
    output_root = (base / cfg.get("output", {}).get("directory", "outputs")).resolve()
    plan = {"app": cfg["app"]["name"], "period": [start, end], "mode": mode,
            "file_sources": [e["id"] for e in entries], "qimai": qimai_enabled,
            "formats": cfg.get("output", {}).get("formats", ["html", "markdown", "xlsx"]),
            "output_directory": str(output_root)}
    if dry_run:
        return {"dry_run": True, **plan}
    if qimai_enabled:
        entries += fetch_qimai(cfg, start, end)
    payload = load_sources(entries, start, end, base)
    analyzer = analyze_dynamic_records if mode == "media" else analyze_records
    analysis = analyzer(payload, rules)
    report = build_report_plan(analysis, cfg.get("privacy", {}), cfg.get("output", {}).get("max_audit_rows", 5))
    report["app"] = cfg["app"]
    quality = {**payload["quality"], "warnings": report["metadata"]["warnings"],
               "period": {"start": start, "end": end},
               "verified_period_records": sum(bool(r.get("period_start")) for r in payload["records"])}
    # Every run has its own directory; a failed export cannot replace an older report.
    period_root = output_root / f"{start}_{end}"
    period_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = period_root / stamp
    with tempfile.TemporaryDirectory(prefix=".building-", dir=period_root) as temp:
        stage = Path(temp)
        outputs = export_report(report, cfg["app"], quality, stage, plan["formats"])
        state = {"status": "complete", **plan, "records": len(report["records"]),
                 "issues": len(report["issues"]), "outputs": outputs,
                 "files": {name: hashlib.sha256((stage / name).read_bytes()).hexdigest() for name in outputs}}
        (stage / "run.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        stage.rename(destination)
    return {"status": "complete", **plan, "records": len(report["records"]),
            "verified_period_records": quality["verified_period_records"], "issues": len(report["issues"]),
            "negative_rate": report["totals"]["negative_rate"], "output_directory": str(destination),
            "outputs": outputs + ["run.json"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="App 用户声音：采集/导入、规则分析和本地周报")
    parser.add_argument("--config", type=Path, default=ROOT / "config.example.yaml")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--dry-run", action="store_true", help="校验配置并打印计划，不写文件或启动浏览器")
    args = parser.parse_args()
    try:
        result = run(args.config, args.start_date, args.end_date, args.dry_run)
    except (ValueError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
