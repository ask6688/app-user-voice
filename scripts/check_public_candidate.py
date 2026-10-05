#!/usr/bin/env python3
"""Check the explicit publication manifest without printing matched values."""
from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path, PurePosixPath
import re
from typing import NamedTuple
from urllib.parse import urlsplit


class Finding(NamedTuple):
    path: str
    line: int
    category: str


# These directories may be generated locally, but never belong in the manifest.
RUNTIME_DIRS = {
    ".git", ".browser", ".runtime", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", "outputs", "output", "data", "cache",
    "caches", "artifacts", ".cache", ".npm-cache", ".playwright", ".playwright-browsers", "markdown",
}
TEXT_SUFFIXES = {".py", ".mjs", ".js", ".json", ".yaml", ".yml", ".md", ".txt", ".csv", ".tsv", ".toml", ".html", ".lock"}
TEXT_NAMES = {".gitignore", ".env.example", "LICENSE", "COPYING", "NOTICE"}
PUBLIC_IMAGES = {"docs/assets/report-overview.png", "docs/assets/report-evidence.png"}
LOCAL_PATH = re.compile(r"/(?:Users|home)/|[A-Za-z]:\\(?:Users|Documents)\\")
PRIVATE_RUNTIME = re.compile(r"[\\/]\.(?:codex|agents)[\\/]|/(?:workspace-deps|bundled)[\\/]")
PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
EMAIL = re.compile(r"(?<![\w.])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
SECRET_ASSIGNMENT = re.compile(r"""(?ix)(?<![\w])(?:[\"']?)(?:[A-Za-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|app[_-]?key|app[_-]?secret|private[_-]?key|client[_-]?secret|token|cookie|password|passwd|secret|webhook)(?:[\"']?)\s*[:=]\s*[\"']([^\"'\r\n]+)[\"']""")
BARE_SECRET = re.compile(r"(?im)^\s*(?:[A-Za-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|app[_-]?secret|private[_-]?key|client[_-]?secret|token|password|cookie|webhook)\s*[:=]\s*([A-Za-z0-9_+/=.:-]{8,})\s*$")
TOKEN_VALUE = re.compile(r"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|LTAI[A-Za-z0-9]{16,}|(?i:access_token|api_key|token)=[A-Za-z0-9_+/=-]{12,})")
PLACEHOLDER = re.compile(r"^(?:YOUR_|CHANGE_ME|EXAMPLE_|DEMO_|FIXTURE[-_]|\$\{|<)", re.IGNORECASE)


def private_host(url: str, allow_loopback: bool = False) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if host in {"localhost", "localhost.localdomain"}:
        return not allow_loopback
    if host.endswith((".internal", ".local")):
        return True
    if any(part in host for part in ("jira", "confluence", "alidocs")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback and allow_loopback:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def scan_text(path: str, text: str) -> list[Finding]:
    findings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        categories = set()
        for category, pattern in [("absolute-local-path", LOCAL_PATH), ("private-runtime-path", PRIVATE_RUNTIME), ("phone-value", PHONE), ("email-value", EMAIL), ("credential-value", TOKEN_VALUE)]:
            if pattern.search(line):
                categories.add(category)
        if any(private_host(match.group(), allow_loopback=path.startswith("tests/")) for match in URL.finditer(line)):
            categories.add("private-url")
        for pattern in (SECRET_ASSIGNMENT, BARE_SECRET):
            for match in pattern.finditer(line):
                value = match.group(1)
                if value.strip() and not PLACEHOLDER.match(value.strip()):
                    categories.add("credential-literal")
        findings.extend(Finding(path, line_number, category) for category in sorted(categories))
    return findings


def private_file(name: str) -> bool:
    return (name.startswith(".env") and name != ".env.example"
            or name.endswith((".local.yaml", ".local.yml", ".local.json")))


def png_without_metadata(data: bytes) -> bool:
    """Accept screenshot PNG chunks only; pixels still need human review."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset, kinds = 8, set()
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        if kind not in {b"IHDR", b"IDAT", b"IEND"}:
            return False
        offset += size + 12
        if offset > len(data):
            return False
        kinds.add(kind)
        if kind == b"IEND":
            return offset == len(data) and kinds == {b"IHDR", b"IDAT", b"IEND"}
    return False


def scan_candidate(root: Path) -> list[Finding]:
    root = root.absolute()
    if root.is_symlink():
        return [Finding(".", 0, "publication-symlink")]
    findings = []
    manifest = root / "PUBLIC_FILES.txt"
    if manifest.is_symlink():
        return [Finding("PUBLIC_FILES.txt", 0, "publication-symlink")]
    if not manifest.is_file():
        return [Finding("PUBLIC_FILES.txt", 0, "missing-manifest")]
    allowed = set()
    for line_number, entry in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        entry = entry.strip()
        if not entry or entry.startswith("#"):
            continue
        path = PurePosixPath(entry)
        if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in entry or path.as_posix() != entry:
            findings.append(Finding("PUBLIC_FILES.txt", line_number, "invalid-manifest-path"))
            continue
        if any(part in RUNTIME_DIRS for part in path.parts) or private_file(path.name):
            findings.append(Finding("PUBLIC_FILES.txt", line_number, "private-manifest-entry"))
            continue
        if entry in allowed:
            findings.append(Finding("PUBLIC_FILES.txt", line_number, "duplicate-manifest-entry"))
        allowed.add(entry)
    if "PUBLIC_FILES.txt" not in allowed:
        findings.append(Finding("PUBLIC_FILES.txt", 0, "manifest-not-listed"))
    for directory, directories, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in directories[:]:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                findings.append(Finding(relative, 0, "publication-symlink"))
                directories.remove(name)
            elif name in RUNTIME_DIRS:
                directories.remove(name)
        for name in files:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                findings.append(Finding(relative, 0, "publication-symlink"))
            elif not private_file(name) and relative not in allowed:
                findings.append(Finding(relative, 0, "unlisted-file"))
    for relative in sorted(allowed):
        path = root / relative
        if any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(root)) or path.is_symlink():
            findings.append(Finding(relative, 0, "publication-symlink"))
            continue
        if not path.is_file():
            findings.append(Finding(relative, 0, "missing-public-file"))
            continue
        if relative in PUBLIC_IMAGES:
            if not png_without_metadata(path.read_bytes()):
                findings.append(Finding(relative, 0, "invalid-or-metadata-public-image"))
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in TEXT_NAMES:
            findings.append(Finding(relative, 0, "unsupported-public-file"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(Finding(relative, 0, "non-text-public-file"))
            continue
        findings.extend(scan_text(relative, text))
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    findings = scan_candidate(args.directory)
    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.category}")
    if findings:
        print(f"Publication check failed: {len(findings)} finding(s).")
        return 1
    print("Publication check passed: all public files are listed and no configured privacy rules matched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
