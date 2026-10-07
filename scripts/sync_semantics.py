#!/usr/bin/env python3
"""Compare local semantic snapshots; copy only with an explicit --write."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True, help="Semantic JSON snapshot destination")
    parser.add_argument("--cases-target", type=Path, help="Optional semantic case JSON destination")
    parser.add_argument("--write", action="store_true", help="Explicitly replace destination snapshots")
    args = parser.parse_args(argv)
    source_files = (ROOT / "feedback_pipeline/semantic_definitions.json", ROOT / "tests/semantic_cases.json")
    pairs = [(source_files[0], args.target)]
    if args.cases_target:
        pairs.append((source_files[1], args.cases_target))

    try:
        # Read and validate every source before making any destination changes.
        snapshots = [(source, target, source.read_bytes()) for source, target in pairs]
        documents = [json.loads(data) for _, _, data in snapshots]
        version = documents[0].get("version") if isinstance(documents[0], dict) else None
        if not isinstance(version, str) or not version:
            raise ValueError("Semantic snapshot must have a nonempty string version")
        targets = [target.resolve() for _, target, _ in snapshots]
        if len(set(targets)) != len(targets):
            raise ValueError("Snapshot destinations must be distinct")
        if args.write and set(targets) & {source.resolve() for source in source_files}:
            raise ValueError("Destinations must not overwrite the source snapshots")

        different = False
        for source, target, data in snapshots:
            same = target.is_file() and target.read_bytes() == data
            status = "same" if same else "different" if target.exists() else "missing"
            digest = hashlib.sha256(data).hexdigest()
            print(f"version={version} sha256={digest} {source.name}: {status} -> {target}")
            different |= not same
            if args.write and not same:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                        temporary = Path(handle.name)
                        handle.write(data)
                    temporary.replace(target)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                print(f"synced: {target}")
        return int(different and not args.write)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
