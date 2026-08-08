#!/usr/bin/env python3
"""Build and verify a deterministic, text-only double-blind artifact ZIP.

The identified repository remains the archival source of record.  This tool
selects the review-relevant source and data, removes non-auditable binaries and
release material, sanitizes identity-bearing text, emits a content manifest,
and verifies the completed ZIP before it is admitted as output.
"""

from __future__ import annotations

import argparse
import fnmatch
import getpass
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Pattern


POLICY_VERSION = "govdrift-anonymous-artifact/v1"
ARCHIVE_ROOT = "governance-drift-anonymous"
MANIFEST_NAME = "ANONYMOUS_ARTIFACT_MANIFEST.json"
MANIFEST_HASH_NAME = "ANONYMOUS_ARTIFACT_MANIFEST.sha256"
NOTICE_NAME = "ANONYMIZATION_NOTICE.md"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

ROOT_FILES = (
    "LICENSE",
    "README.md",
    "THIRD_PARTY_NOTICES.md",
    "main.tex",
    "refs.bib",
)
ROOT_DIRS = (
    "code",
    "data",
    "figs",
    "lab",
    "literature",
    "scripts",
    "sections",
    "submission",
)
REQUIRED_PATHS = (
    "LICENSE",
    "README.md",
    "main.tex",
    "refs.bib",
    "code/detector_study.py",
    "lab/README.md",
    "scripts/verify_artifact.py",
    "sections/01-intro.tex",
    "submission/main-anonymous.tex",
)

EXCLUDED_TOP_LEVEL = (
    ".git",
    "build",
    "originais",
    "output",
    "qa",
    "release",
    "tmp",
)
EXCLUDED_DIR_NAMES = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "node_modules",
    "originais",
    "output",
    "qa",
    "release",
    "runtime",
    "tmp",
    "venv",
}
EXCLUDED_PATH_GLOBS = (
    "lab/results_cross_stack/_superseded_*",
    "lab/results_cross_stack_superseded*",
    "lab/results_extension_smoke*",
    "lab/results_extension_superseded*",
    "lab/results_superseded*",
    "lab/results_trace/_*",
    "lab/results_transition_pilot*",
)
EXCLUDED_BINARY_SUFFIXES = {
    ".7z",
    ".bundle",
    ".dmg",
    ".gz",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".pyc",
    ".pyo",
    ".tar",
    ".tgz",
    ".xz",
    ".zip",
}
EXCLUDED_TRANSIENT_SUFFIXES = {
    ".aux",
    ".bbl",
    ".blg",
    ".fdb_latexmk",
    ".fls",
    ".log",
    ".out",
    ".xdv",
}
EXCLUDED_EXACT_FILES = {
    "scripts/build_anonymous_artifact.py",  # external verifier; contains policy inputs
}
SECRET_PATH_RE = re.compile(
    r"(?:^|/)(?:"
    r"\.env(?:\..*)?|\.netrc|\.npmrc|\.pypirc|"
    r"id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?|"
    r"credentials?(?:\..*)?|kubeconfig(?:\..*)?|"
    r"secrets?(?:\..*)?|[^/]+\.(?:key|pem|p12|pfx)"
    r")$",
    re.IGNORECASE,
)

SECRET_CONTENT_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
)


class BuildError(RuntimeError):
    """Raised when the bundle cannot be made safely and deterministically."""


@dataclass(frozen=True)
class ReplacementRule:
    category: str
    pattern: Pattern[str]
    replacement: str


@dataclass(frozen=True)
class PreparedFile:
    path: str
    data: bytes
    mode: int
    sanitizations: dict[str, int]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def safe_dynamic_values() -> tuple[list[str], list[str], list[str]]:
    homes: list[str] = []
    hosts: list[str] = []
    users: list[str] = []
    home = str(Path.home())
    if home not in {"", "/", "/root"}:
        homes.append(home)
    for value in (socket.gethostname(), socket.getfqdn()):
        if value and value.lower() not in {"localhost", "localhost.localdomain"}:
            hosts.append(value)
    user = getpass.getuser()
    if len(user) >= 4 and user.lower() not in {"root", "user", "runner"}:
        users.append(user)
    return sorted(set(homes)), sorted(set(hosts)), sorted(set(users))


def replacement_rules() -> tuple[ReplacementRule, ...]:
    homes, hosts, users = safe_dynamic_values()
    rules: list[ReplacementRule] = [
        ReplacementRule(
            "public_repository",
            re.compile(
                r"(?:https?://github\.com/|git@github\.com:)"
                r"obedebessa/governance-drift(?:\.git)?",
                re.IGNORECASE,
            ),
            "https://example.invalid/anonymous/repository",
        ),
        ReplacementRule(
            "public_repository",
            re.compile(r"github\.com/obedebessa(?:/governance-drift)?", re.IGNORECASE),
            "example.invalid/anonymous/repository",
        ),
        ReplacementRule(
            "archive_identifier",
            re.compile(
                r"https?://zenodo\.org/badge/DOI/10\.5281/zenodo\.\d+\.svg",
                re.IGNORECASE,
            ),
            "https://example.invalid/anonymous/archive-badge.svg",
        ),
        ReplacementRule(
            "archive_identifier",
            re.compile(
                r"https?://(?:doi\.org/)?10\.5281/zenodo\.\d+",
                re.IGNORECASE,
            ),
            "https://example.invalid/anonymous/archive",
        ),
        ReplacementRule(
            "archive_identifier",
            re.compile(r"10\.5281/zenodo\.\d+", re.IGNORECASE),
            "10.0000/anonymous",
        ),
        ReplacementRule(
            "email",
            re.compile(r"obedebessa@gmail\.com", re.IGNORECASE),
            "anonymous@example.invalid",
        ),
        ReplacementRule(
            "identity",
            re.compile(
                r"(?:Obede Bessa Rocha da Silva|Bessa Rocha da Silva, Obede|"
                r"Obede Bessa|Bessa, Obede)",
                re.IGNORECASE,
            ),
            "Anonymous Author",
        ),
        ReplacementRule(
            "capture_hostname",
            re.compile(r"Obedes-MacBook-Pro(?:\.local)?", re.IGNORECASE),
            "anonymous-host",
        ),
        ReplacementRule(
            "capture_home",
            re.compile(r"/Users/obede(?=/|\b)", re.IGNORECASE),
            "/home/anonymous",
        ),
        ReplacementRule(
            "identity",
            re.compile(r"(?<![A-Za-z0-9])obede(?![A-Za-z0-9])", re.IGNORECASE),
            "anonymous",
        ),
    ]
    for home in homes:
        rules.append(
            ReplacementRule("capture_home", re.compile(re.escape(home)), "/home/anonymous")
        )
    for host in hosts:
        rules.append(
            ReplacementRule(
                "capture_hostname", re.compile(re.escape(host), re.IGNORECASE), "anonymous-host"
            )
        )
    for user in users:
        rules.append(
            ReplacementRule(
                "identity",
                re.compile(
                    rf"(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])",
                    re.IGNORECASE,
                ),
                "anonymous",
            )
        )
    return tuple(rules)


def forbidden_patterns() -> tuple[Pattern[str], ...]:
    homes, hosts, users = safe_dynamic_values()
    values = [
        r"/Users/obede(?=/|\b)",
        r"Obedes-MacBook-Pro(?:\.local)?",
        r"obedebessa@gmail\.com",
        r"(?:Obede Bessa Rocha da Silva|Bessa Rocha da Silva, Obede|Obede Bessa)",
        r"(?<![A-Za-z0-9])obede(?![A-Za-z0-9])",
        r"10\.5281/zenodo\.\d+",
        r"github\.com/obedebessa(?:/governance-drift)?",
        r"git@github\.com:obedebessa/governance-drift(?:\.git)?",
    ]
    values.extend(re.escape(value) for value in homes + hosts)
    values.extend(
        rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])" for value in users
    )
    return tuple(re.compile(value, re.IGNORECASE) for value in values)


def sanitize_text(text: str, rules: Iterable[ReplacementRule]) -> tuple[str, dict[str, int]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    counts: Counter[str] = Counter()
    for rule in rules:
        normalized, count = rule.pattern.subn(rule.replacement, normalized)
        if count:
            counts[rule.category] += count
    return normalized, dict(sorted(counts.items()))


def assert_clean_text(text: str, location: str) -> None:
    for pattern in forbidden_patterns():
        match = pattern.search(text)
        if match:
            raise BuildError(f"identity leak in {location}: pattern {pattern.pattern!r}")
    for pattern in SECRET_CONTENT_PATTERNS:
        if pattern.search(text):
            raise BuildError(f"credential-like content in {location}: {pattern.pattern!r}")


def path_exclusion_reason(relative: str, *, is_dir: bool = False) -> str | None:
    posix = PurePosixPath(relative)
    if relative in EXCLUDED_EXACT_FILES:
        return "external-builder"
    if any(part in EXCLUDED_DIR_NAMES for part in posix.parts):
        return "generated-or-private-directory"
    if any(fnmatch.fnmatch(relative, pattern) for pattern in EXCLUDED_PATH_GLOBS):
        return "pilot-or-superseded-result"
    if SECRET_PATH_RE.search(relative):
        return "secret-bearing-path"
    if is_dir:
        return None
    if posix.name in {".DS_Store", MANIFEST_NAME, MANIFEST_HASH_NAME}:
        return "generated-file"
    if posix.suffix.lower() in EXCLUDED_TRANSIENT_SUFFIXES:
        return "transient-log-or-build-product"
    if posix.suffix.lower() in EXCLUDED_BINARY_SUFFIXES:
        return "non-auditable-binary-or-archive"
    return None


def iter_selected_files(root: Path) -> tuple[list[Path], Counter[str]]:
    selected: list[Path] = []
    excluded: Counter[str] = Counter()
    for required in REQUIRED_PATHS:
        if not (root / required).exists():
            raise BuildError(f"required artifact path is missing: {required}")

    for relative in ROOT_FILES:
        path = root / relative
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise BuildError(f"root artifact path is not a regular file: {relative}")
        selected.append(path)

    for directory in ROOT_DIRS:
        start = root / directory
        if not start.exists():
            continue
        if start.is_symlink() or not start.is_dir():
            raise BuildError(f"artifact root is not a regular directory: {directory}")
        for current, dirnames, filenames in os.walk(start, followlinks=False):
            current_path = Path(current)
            retained_dirs: list[str] = []
            for name in sorted(dirnames):
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                reason = path_exclusion_reason(relative, is_dir=True)
                if reason:
                    excluded[reason] += 1
                elif path.is_symlink():
                    raise BuildError(f"symlinked directory is not allowed: {relative}")
                else:
                    retained_dirs.append(name)
            dirnames[:] = retained_dirs
            for name in sorted(filenames):
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                reason = path_exclusion_reason(relative)
                if reason:
                    excluded[reason] += 1
                    continue
                if path.is_symlink() or not path.is_file():
                    raise BuildError(f"non-regular file is not allowed: {relative}")
                selected.append(path)
    selected.sort(key=lambda path: path.relative_to(root).as_posix())
    return selected, excluded


def sanitize_relative_path(relative: str, rules: Iterable[ReplacementRule]) -> str:
    value, _ = sanitize_text(relative, rules)
    posix = PurePosixPath(value)
    if posix.is_absolute() or ".." in posix.parts or not posix.parts:
        raise BuildError(f"unsafe sanitized path: {relative!r} -> {value!r}")
    assert_clean_text(value, f"archive path derived from {relative}")
    return posix.as_posix()


def prepare_payload(root: Path) -> tuple[dict[str, PreparedFile], Counter[str]]:
    rules = replacement_rules()
    paths, excluded = iter_selected_files(root)
    prepared: dict[str, PreparedFile] = {}
    casefolded: dict[str, str] = {}
    for source in paths:
        relative = source.relative_to(root).as_posix()
        target = sanitize_relative_path(relative, rules)
        raw = source.read_bytes()
        if b"\x00" in raw:
            raise BuildError(f"unexpected binary file (NUL byte): {relative}")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise BuildError(f"unexpected non-UTF-8 binary file: {relative}") from exc
        sanitized, counts = sanitize_text(text, rules)
        assert_clean_text(sanitized, relative)
        data = sanitized.encode("utf-8")
        mode = 0o755 if source.stat().st_mode & 0o111 else 0o644
        if target in prepared:
            raise BuildError(f"sanitized path collision: {relative} -> {target}")
        folded = target.casefold()
        if folded in casefolded:
            raise BuildError(
                f"case-insensitive path collision: {casefolded[folded]} and {target}"
            )
        casefolded[folded] = target
        prepared[target] = PreparedFile(target, data, mode, counts)

    notice = (
        "# Anonymous review artifact\n\n"
        "This text-only bundle was generated for double-blind review. Identity, "
        "capture-host paths, capture-host names, project archive identifiers, and "
        "the identified public repository were replaced. Generated outputs, raw "
        "release packages, version-control history, secrets, bytecode, and "
        "non-auditable binary containers were excluded.\n\n"
        "Sanitization changes byte-level hashes. Nested transport manifests that "
        "cover sanitized text were regenerated over the anonymous payload; the "
        "top-level manifest is authoritative for this review bundle. Scientific "
        "values, scenario labels, timestamps, and class-set observations are not "
        "intentionally altered.\n"
    ).encode("utf-8")
    prepared[NOTICE_NAME] = PreparedFile(NOTICE_NAME, notice, 0o644, {})
    refresh_trace_manifests(prepared)
    return prepared, excluded


def refresh_trace_manifests(prepared: dict[str, PreparedFile]) -> None:
    manifests = sorted(
        path
        for path in prepared
        if path.startswith("lab/results_trace/") and path.endswith("/manifest.sha256")
    )
    for manifest_path in manifests:
        parent = manifest_path.rsplit("/", 1)[0]
        prefix = parent + "/"
        rows: list[str] = []
        for path, item in sorted(prepared.items()):
            if not path.startswith(prefix) or path == manifest_path:
                continue
            local = path[len(prefix) :]
            rows.append(f"{sha256_bytes(item.data)}  {local}")
        data = ("\n".join(rows) + "\n").encode("utf-8")
        prepared[manifest_path] = PreparedFile(
            manifest_path,
            data,
            prepared[manifest_path].mode,
            {"nested_manifest_regenerated": 1},
        )


def payload_manifest(prepared: dict[str, PreparedFile]) -> tuple[bytes, bytes]:
    entries = []
    aggregate: Counter[str] = Counter()
    for path, item in sorted(prepared.items()):
        aggregate.update(item.sanitizations)
        entries.append(
            {
                "mode": f"{item.mode:04o}",
                "path": path,
                "sanitizations": item.sanitizations,
                "sha256": sha256_bytes(item.data),
                "size": len(item.data),
                "type": "text",
            }
        )
    tree_material = "".join(
        f"{entry['path']}\0{entry['sha256']}\0{entry['size']}\0{entry['mode']}\n"
        for entry in entries
    ).encode("utf-8")
    value = {
        "archive_root": ARCHIVE_ROOT,
        "entries": entries,
        "payload_files": len(entries),
        "payload_tree_sha256": sha256_bytes(tree_material),
        "policy": POLICY_VERSION,
        "sanitizations_by_category": dict(sorted(aggregate.items())),
        "selection": {
            "excluded_binary_suffixes": sorted(EXCLUDED_BINARY_SUFFIXES),
            "excluded_directory_names": sorted(EXCLUDED_DIR_NAMES),
            "excluded_exact_files": sorted(EXCLUDED_EXACT_FILES),
            "excluded_path_globs": list(EXCLUDED_PATH_GLOBS),
            "excluded_top_level": list(EXCLUDED_TOP_LEVEL),
            "excluded_transient_suffixes": sorted(EXCLUDED_TRANSIENT_SUFFIXES),
            "root_directories": list(ROOT_DIRS),
            "root_files": list(ROOT_FILES),
            "text_only": True,
        },
    }
    manifest = canonical_json(value)
    checksum = f"{sha256_bytes(manifest)}  {MANIFEST_NAME}\n".encode("utf-8")
    return manifest, checksum


def zip_info(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (mode & 0xFFFF) << 16
    info.extra = b""
    info.comment = b""
    return info


def write_archive(
    output: Path,
    prepared: dict[str, PreparedFile],
    manifest: bytes,
    manifest_checksum: bytes,
    *,
    force: bool,
) -> None:
    if output.exists() and not force:
        raise BuildError(f"output exists; use --force to replace it: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    entries: dict[str, tuple[bytes, int]] = {
        f"{ARCHIVE_ROOT}/{path}": (item.data, item.mode)
        for path, item in prepared.items()
    }
    entries[f"{ARCHIVE_ROOT}/{MANIFEST_NAME}"] = (manifest, 0o644)
    entries[f"{ARCHIVE_ROOT}/{MANIFEST_HASH_NAME}"] = (manifest_checksum, 0o644)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False
    )
    temporary = Path(handle.name)
    handle.close()
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.comment = b""
            for name, (data, mode) in sorted(entries.items()):
                archive.writestr(zip_info(name, mode), data)
        verify_archive(temporary)
        os.replace(temporary, output)
        output.chmod(0o644)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_manifest(archive: zipfile.ZipFile) -> tuple[dict[str, object], bytes]:
    manifest_path = f"{ARCHIVE_ROOT}/{MANIFEST_NAME}"
    checksum_path = f"{ARCHIVE_ROOT}/{MANIFEST_HASH_NAME}"
    try:
        manifest_bytes = archive.read(manifest_path)
        checksum_bytes = archive.read(checksum_path)
    except KeyError as exc:
        raise BuildError(f"archive lacks anonymous manifest: {exc}") from exc
    expected_checksum = f"{sha256_bytes(manifest_bytes)}  {MANIFEST_NAME}\n".encode(
        "utf-8"
    )
    if checksum_bytes != expected_checksum:
        raise BuildError("anonymous manifest checksum is invalid")
    try:
        value = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("anonymous manifest is not canonical UTF-8 JSON") from exc
    if canonical_json(value) != manifest_bytes:
        raise BuildError("anonymous manifest is not in canonical form")
    if not isinstance(value, dict):
        raise BuildError("anonymous manifest root is not an object")
    return value, manifest_bytes


def verify_archive(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise BuildError(f"archive does not exist: {path}")
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if names != sorted(names) or len(names) != len(set(names)):
            raise BuildError("archive entries are unsorted or duplicated")
        if archive.comment:
            raise BuildError("archive comment must be empty")
        for info in infos:
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or info.is_dir():
                raise BuildError(f"unsafe or unexpected ZIP entry: {info.filename}")
            if info.date_time != FIXED_ZIP_TIME or info.compress_type != zipfile.ZIP_STORED:
                raise BuildError(f"non-deterministic ZIP metadata: {info.filename}")
            assert_clean_text(info.filename, "ZIP entry name")

        manifest, _ = read_manifest(archive)
        if manifest.get("policy") != POLICY_VERSION:
            raise BuildError("unexpected anonymous artifact policy version")
        raw_entries = manifest.get("entries")
        if not isinstance(raw_entries, list):
            raise BuildError("manifest entries are not a list")
        expected_names = {
            f"{ARCHIVE_ROOT}/{MANIFEST_NAME}",
            f"{ARCHIVE_ROOT}/{MANIFEST_HASH_NAME}",
        }
        tree_material = bytearray()
        for entry in raw_entries:
            if not isinstance(entry, dict):
                raise BuildError("manifest contains a non-object entry")
            relative = str(entry.get("path", ""))
            expected_names.add(f"{ARCHIVE_ROOT}/{relative}")
            data = archive.read(f"{ARCHIVE_ROOT}/{relative}")
            if sha256_bytes(data) != entry.get("sha256") or len(data) != entry.get("size"):
                raise BuildError(f"manifest mismatch for {relative}")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BuildError(f"non-text payload survived policy: {relative}") from exc
            assert_clean_text(text, relative)
            info = archive.getinfo(f"{ARCHIVE_ROOT}/{relative}")
            mode = (info.external_attr >> 16) & 0o777
            if f"{mode:04o}" != entry.get("mode"):
                raise BuildError(f"mode mismatch for {relative}")
            tree_material.extend(
                f"{relative}\0{entry['sha256']}\0{entry['size']}\0{entry['mode']}\n".encode(
                    "utf-8"
                )
            )
        if set(names) != expected_names:
            missing = sorted(expected_names - set(names))
            extra = sorted(set(names) - expected_names)
            raise BuildError(f"ZIP/manifest coverage mismatch: missing={missing}, extra={extra}")
        if sha256_bytes(bytes(tree_material)) != manifest.get("payload_tree_sha256"):
            raise BuildError("payload tree digest is invalid")
        if int(manifest.get("payload_files", -1)) != len(raw_entries):
            raise BuildError("payload file count is invalid")

    raw_archive = path.read_bytes()
    raw_text = raw_archive.decode("latin-1")
    for pattern in forbidden_patterns():
        if pattern.search(raw_text):
            raise BuildError(f"identity token remains in raw ZIP bytes: {pattern.pattern!r}")
    return manifest


def destination_is_release_path(root: Path, output: Path) -> bool:
    resolved = output.resolve(strict=False)
    for directory in (root / "submission", root / "release"):
        try:
            resolved.relative_to(directory.resolve())
            return True
        except ValueError:
            continue
    return False


def require_clean_final_tree(root: Path, output: Path, final: bool) -> None:
    release_path = destination_is_release_path(root, output)
    if release_path and not final:
        raise BuildError(
            "writing under submission/ or release/ requires --final after results are frozen"
        )
    if final and not release_path:
        raise BuildError("--final output must be under submission/ or release/")
    if not final:
        return
    output_relative = output.resolve(strict=False).relative_to(root.resolve()).as_posix()
    result = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            ".",
            f":(exclude,top,literal){output_relative}",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise BuildError(f"cannot establish clean source tree: {result.stderr.strip()}")
    if result.stdout.strip():
        raise BuildError("refusing final bundle from a dirty or changing source tree")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: parent of scripts/)",
    )
    value.add_argument(
        "--output",
        type=Path,
        help="ZIP destination (default: submission/governance-drift-anonymous-artifact.zip)",
    )
    value.add_argument("--verify-only", type=Path, help="verify an existing ZIP and exit")
    value.add_argument(
        "--final",
        action="store_true",
        help="permit a clean-tree build under submission/ or release/",
    )
    value.add_argument("--force", action="store_true", help="replace an existing output")
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        if args.verify_only:
            if args.output or args.final or args.force:
                raise BuildError("--verify-only cannot be combined with build options")
            manifest = verify_archive(args.verify_only.resolve())
            print(
                f"PASS: anonymous artifact verified; "
                f"files={manifest['payload_files']} "
                f"tree_sha256={manifest['payload_tree_sha256']}"
            )
            return 0

        root = args.source_root.resolve()
        output = (
            args.output.resolve()
            if args.output
            else root / "submission/governance-drift-anonymous-artifact.zip"
        )
        require_clean_final_tree(root, output, args.final)
        prepared, excluded = prepare_payload(root)
        manifest, checksum = payload_manifest(prepared)
        # Catch edits that raced with payload preparation before admitting a
        # final in-repository archive. The output itself is the sole exception.
        require_clean_final_tree(root, output, args.final)
        write_archive(output, prepared, manifest, checksum, force=args.force)
        verified = verify_archive(output)
        print(
            f"PASS: wrote deterministic anonymous artifact {output}\n"
            f"payload_files={verified['payload_files']}\n"
            f"payload_tree_sha256={verified['payload_tree_sha256']}\n"
            f"archive_sha256={sha256_bytes(output.read_bytes())}\n"
            f"excluded_counts_by_policy={json.dumps(dict(sorted(excluded.items())), sort_keys=True)}"
        )
        return 0
    except (BuildError, OSError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
