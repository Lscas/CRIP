"""Rebuild or verify the source-bundle manifest without reading runtime data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "BUNDLE_MANIFEST.json"
IGNORED_DIRS = {
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "data",
    "uploads",
    "secrets",
    ".cache",
    ".wrangler",
    "playwright-report",
    "outputs",
    "dist",
}


def ignored_dir(relative: Path) -> bool:
    if relative.parts[:2] == ("reports", "local"):
        return True
    return any(part in IGNORED_DIRS or part == ".local" or part.startswith(".local-") or part.endswith(".egg-info")
               for part in relative.parts)


def ignored_file(relative: Path) -> bool:
    name = relative.name
    if relative == Path("BUNDLE_MANIFEST.json") or ignored_dir(relative.parent):
        return True
    if name == ".env.example":
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    return (name == ".coverage" or name.endswith((".pyc", ".key", ".pem", ".log", ".db"))
            or ".sqlite" in name)


def source_files(root: Path = ROOT) -> list[Path]:
    result: list[Path] = []
    for current, directories, files in os.walk(root, topdown=True):
        current_path = Path(current)
        directories[:] = sorted(
            directory for directory in directories
            if not ignored_dir((current_path / directory).relative_to(root))
        )
        for name in sorted(files):
            path = current_path / name
            if not ignored_file(path.relative_to(root)):
                result.append(path)
    return sorted(result, key=lambda path: path.relative_to(root).as_posix())


def build(root: Path = ROOT) -> dict:
    entries = []
    for path in source_files(root):
        raw = path.read_bytes()
        entries.append({
            "path": path.relative_to(root).as_posix(),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    return {
        "version": version,
        "artifact": "cirp-prototype-source",
        "not_application": False,
        "maturity": "local_live_partial",
        "file_count_excluding_manifest": len(entries),
        "files": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    expected = build()
    if args.check:
        try:
            actual = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print("[FAIL] Bundle manifest is missing or invalid.")
            return 1
        if actual != expected:
            print("[FAIL] Bundle manifest does not match the current source files.")
            return 1
        print(f"[PASS] Bundle manifest: v{expected['version']}, {expected['file_count_excluding_manifest']} files")
        return 0
    temporary = MANIFEST.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(MANIFEST)
    print(f"[WRITE] Bundle manifest: v{expected['version']}, {expected['file_count_excluding_manifest']} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
