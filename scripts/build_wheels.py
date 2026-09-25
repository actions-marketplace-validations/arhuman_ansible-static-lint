#!/usr/bin/env python3
"""Pack the released astl binaries into PyPI wheels.

Reads the GoReleaser archives (already verified against the cosign-signed
checksums.txt by the caller) and writes one wheel per platform tag. The binary
is byte-identical to the one in the archive: nothing is rebuilt here, so the
wheel ships what cosign signed.

Stdlib only, on purpose. This runs in the release path with PyPI upload rights
next to it, and an unpinned third-party dependency there would undo the
provenance the rest of the release chain establishes.

Usage:
    python scripts/build_wheels.py --version 0.5.1 \
        --archives dist/archives --out dist/wheels

--version takes a PEP 440 version with no leading v. The git tag has one and
the binary reports one (.goreleaser.yaml re-adds it so all three install paths
print the same string); the wheel filename must not. Strip it in the caller.

--archive-version exists for the TestPyPI rehearsal alone, where a wheel is
repacked under a suffixed version (0.5.1rc1) from an archive still named for
the released one (0.5.1). Production passes neither.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import os
import re
import stat
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

DISTRIBUTION = "ansible-static-lint"
# PEP 503/427: the wheel filename and .dist-info use the escaped name.
ESCAPED = DISTRIBUTION.replace("-", "_")
SUMMARY = (
    "A fast, offline static companion to ansible-lint, for editor-on-save, "
    "pre-commit, or a CI pre-check."
)
HOMEPAGE = "https://github.com/arhuman/ansible-static-lint"
# Deliberately wide: astl is a static binary and needs no Python at all, so the
# floor exists only to keep old CI images (RHEL 8 era, Python 3.8) installable.
REQUIRES_PYTHON = ">=3.8"

# GoReleaser's goos_goarch pair -> the wheel platform tags it satisfies. The
# linux binaries are CGO_ENABLED=0 static, so one build serves glibc and musl
# alike and each yields two wheels.
PLATFORM_TAGS: dict[tuple[str, str], tuple[str, ...]] = {
    ("linux", "amd64"): ("manylinux_2_17_x86_64", "musllinux_1_2_x86_64"),
    ("linux", "arm64"): ("manylinux_2_17_aarch64", "musllinux_1_2_aarch64"),
    ("darwin", "amd64"): ("macosx_10_9_x86_64",),
    ("darwin", "arm64"): ("macosx_11_0_arm64",),
    ("windows", "amd64"): ("win_amd64",),
    ("windows", "arm64"): ("win_arm64",),
}

ARCHIVE_RE = re.compile(
    r"^" + re.escape(DISTRIBUTION) + r"_(?P<version>[^_]+)"
    r"_(?P<goos>[a-z0-9]+)_(?P<goarch>[a-z0-9]+)\.(?P<ext>tar\.gz|zip)$"
)


@dataclass(frozen=True)
class Payload:
    """One platform's binary, plus the SBOM covering the archive it came from."""

    goos: str
    goarch: str
    binary: bytes
    sbom: bytes | None


def _read_member(archive: Path, name: str) -> bytes:
    """Extract one named member's bytes, without writing it to disk.

    Refuses a member that is not a regular file: a symlink or device node in a
    release archive has no business becoming the shipped executable.
    """
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            info = zf.getinfo(name)
            if info.is_dir():
                raise ValueError(f"{archive.name}: {name} is a directory")
            return zf.read(info)
    with tarfile.open(archive, "r:gz") as tf:
        member = tf.getmember(name)
        if not member.isfile():
            raise ValueError(f"{archive.name}: {name} is not a regular file")
        extracted = tf.extractfile(member)
        if extracted is None:
            raise ValueError(f"{archive.name}: {name} has no content")
        return extracted.read()


def collect(archives: Path, version: str) -> list[Payload]:
    """Pair each platform archive with its sidecar SBOM."""
    payloads: list[Payload] = []
    for path in sorted(archives.iterdir()):
        match = ARCHIVE_RE.match(path.name)
        if match is None:
            continue
        if match["version"] != version:
            raise SystemExit(
                f"{path.name}: version {match['version']!r} does not match {version!r}"
            )
        goos, goarch = match["goos"], match["goarch"]
        if (goos, goarch) not in PLATFORM_TAGS:
            raise SystemExit(f"{path.name}: no wheel platform tag for {goos}/{goarch}")
        binary_name = "astl.exe" if goos == "windows" else "astl"
        sbom_path = path.with_name(path.name + ".sbom.json")
        payloads.append(
            Payload(
                goos=goos,
                goarch=goarch,
                binary=_read_member(path, binary_name),
                sbom=sbom_path.read_bytes() if sbom_path.is_file() else None,
            )
        )
    return payloads


def _metadata(version: str, description: str) -> str:
    headers = (
        "Metadata-Version: 2.4",
        f"Name: {DISTRIBUTION}",
        f"Version: {version}",
        f"Summary: {SUMMARY}",
        "Author: Arnaud Assad",
        "License-Expression: MIT",
        "License-File: LICENSE",
        f"Project-URL: Homepage, {HOMEPAGE}",
        f"Project-URL: Source, {HOMEPAGE}",
        f"Project-URL: Changelog, {HOMEPAGE}/blob/main/CHANGELOG.md",
        f"Project-URL: Issues, {HOMEPAGE}/issues",
        "Classifier: Development Status :: 4 - Beta",
        "Classifier: Environment :: Console",
        "Classifier: Intended Audience :: Developers",
        "Classifier: Intended Audience :: System Administrators",
        "Classifier: Operating System :: OS Independent",
        "Classifier: Programming Language :: Go",
        "Classifier: Topic :: Software Development :: Quality Assurance",
        "Classifier: Topic :: System :: Systems Administration",
        f"Requires-Python: {REQUIRES_PYTHON}",
        "Description-Content-Type: text/markdown",
    )
    # The blank line is the metadata/body separator: everything after it is the
    # long description PyPI renders on the project page.
    return "".join(f"{line}\n" for line in headers) + "\n" + description


def _record_line(name: str, data: bytes) -> tuple[str, str, int]:
    digest = hashlib.sha256(data).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return (name, f"sha256={encoded}", len(data))


def build_wheel(
    payload: Payload,
    tag: str,
    version: str,
    description: str,
    license_text: bytes,
    out: Path,
) -> Path:
    data_dir = f"{ESCAPED}-{version}.data/scripts"
    info_dir = f"{ESCAPED}-{version}.dist-info"
    binary_name = "astl.exe" if payload.goos == "windows" else "astl"

    entries: list[tuple[str, bytes, int]] = [
        # Explicitly 0o755: zipfile does not preserve a mode for bytes written
        # via writestr, and pip copies the recorded mode through, so without
        # this the installed astl is not executable.
        (f"{data_dir}/{binary_name}", payload.binary, 0o755),
        (
            f"{info_dir}/WHEEL",
            (
                "Wheel-Version: 1.0\n"
                "Generator: astl build_wheels.py\n"
                # false: the payload is a platform binary, not pure Python.
                "Root-Is-Purelib: false\n"
                f"Tag: py3-none-{tag}\n"
            ).encode(),
            0o644,
        ),
        (
            f"{info_dir}/METADATA",
            _metadata(version, description).encode(),
            0o644,
        ),
        (f"{info_dir}/licenses/LICENSE", license_text, 0o644),
    ]
    if payload.sbom is not None:
        entries.append((f"{info_dir}/sboms/astl.spdx.json", payload.sbom, 0o644))

    records = [_record_line(name, data) for name, data, _ in entries]
    records.append((f"{info_dir}/RECORD", "", ""))
    record_buf = io.StringIO()
    csv.writer(record_buf, lineterminator="\n").writerows(records)
    entries.append((f"{info_dir}/RECORD", record_buf.getvalue().encode(), 0o644))

    out.mkdir(parents=True, exist_ok=True)
    wheel_path = out / f"{ESCAPED}-{version}-py3-none-{tag}.whl"
    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data, mode in entries:
            # A fixed timestamp keeps the wheel reproducible: same inputs, same
            # bytes, whatever the clock says on the runner.
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            # S_IFREG must be OR'd in, not just the permission bits: pip reads
            # the whole high half as st_mode, and a value without the file-type
            # bits reads as mode 0, silently installing the binary 0o644.
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.create_system = 3  # Unix, so the mode above is honoured.
            zf.writestr(info, data, zipfile.ZIP_DEFLATED)
    return wheel_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        required=True,
        help="PEP 440 version, without the tag's leading v (0.5.1, not v0.5.1)",
    )
    parser.add_argument(
        "--archive-version",
        help=(
            "version in the archive filenames, when it differs from --version. "
            "Only the TestPyPI rehearsal needs this, to repack a released "
            "archive under a suffixed version it has not already published."
        ),
    )
    parser.add_argument(
        "--archives",
        type=Path,
        required=True,
        help="directory holding the verified GoReleaser archives",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="directory to write the wheels into"
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "README.md",
        help="long description for the PyPI project page",
    )
    parser.add_argument(
        "--license",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "LICENSE",
        help="license file to embed in dist-info/licenses/",
    )
    args = parser.parse_args(argv)

    if args.version.startswith("v"):
        parser.error(
            f"--version {args.version!r} is not PEP 440: strip the leading v "
            '(use "${GITHUB_REF_NAME#v}")'
        )

    payloads = collect(args.archives, args.archive_version or args.version)
    if not payloads:
        parser.error(f"no {DISTRIBUTION} archives found in {args.archives}")

    missing_sboms = [f"{p.goos}/{p.goarch}" for p in payloads if p.sbom is None]
    if missing_sboms:
        # Not fatal: a wheel without its SBOM still installs and runs. But the
        # release is meant to carry one, so say so rather than drop it quietly.
        print(
            f"warning: no SBOM alongside {', '.join(missing_sboms)}; "
            "those wheels ship without dist-info/sboms/",
            file=sys.stderr,
        )

    description = args.readme.read_text(encoding="utf-8")
    license_text = args.license.read_bytes()

    built = 0
    for payload in payloads:
        for tag in PLATFORM_TAGS[(payload.goos, payload.goarch)]:
            path = build_wheel(
                payload, tag, args.version, description, license_text, args.out
            )
            print(f"{path.name}  {os.path.getsize(path):>9} bytes")
            built += 1

    expected = sum(len(tags) for tags in PLATFORM_TAGS.values())
    if built != expected:
        print(
            f"warning: built {built} wheels, expected {expected} "
            "(a platform archive is missing)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
