"""Test sandbox: no test may ever touch the real data directory.

Every test must call `sandbox.activate()` BEFORE importing anything from
`wordgrab`, and call `sandbox.verify()` at the end. verify() re-reads the real
library and raises loudly if anything changed.

This exists because a test run once deleted the user's actual vocabulary
database: the suite assumed data/ was disposable build output.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_DATA = ROOT / "data"
SENTINEL = ".wordgrab-real-data"


class SandboxViolation(RuntimeError):
    """A test tried to modify the real vocabulary database."""


_state: dict = {}


def activate(prefix: str = "wordgrab-test-"):
    """Point WORDGRAB_DATA at a throwaway directory. Call before importing wordgrab."""
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    os.environ["WORDGRAB_DATA"] = str(tmp)
    if str(tmp) == str(REAL_DATA):  # paranoia
        raise SandboxViolation(f"sandbox points at the real data dir: {tmp}")
    _state["tmp"] = tmp
    _state["fingerprint"] = fingerprint(REAL_DATA)
    _state["env_before"] = dict(os.environ)
    return tmp


def fingerprint(directory: Path) -> dict:
    """Size + mtime + content hash of every file, to detect any change."""
    result: dict[str, tuple] = {}
    if not directory.exists():
        return result
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        result[str(path.relative_to(directory))] = (
            len(data),
            hashlib.sha256(data).hexdigest(),
        )
    return result


def verify() -> None:
    """Fail loudly if the real data dir changed, then clean up the temp dir."""
    tmp = _state.get("tmp")
    before = _state.get("fingerprint")
    if before is not None:
        after = fingerprint(REAL_DATA)
        changed = sorted(set(before) ^ set(after))
        changed += sorted(k for k in set(before) & set(after) if before[k] != after[k])
        if changed:
            raise SandboxViolation(
                "these real data files were modified during the test run: "
                + ", ".join(changed)
                + "\nThe test suite must use sandbox.activate() before importing wordgrab."
                + "\nRestore with:  python -m wordgrab --backups"
            )
    if tmp and tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)


def rebase() -> None:
    """Accept the current real-data state as the baseline.

    Only for intentional changes (e.g. writing the sentinel file for the first
    time). Anything else should be a test failure, not a rebase.
    """
    _state["fingerprint"] = fingerprint(REAL_DATA)


def guard_real_data() -> None:
    """Drop a sentinel so anyone (human or script) can tell real data from scratch."""
    REAL_DATA.mkdir(parents=True, exist_ok=True)
    (REAL_DATA / SENTINEL).write_text(
        "This is the real WordGrab vocabulary database.\n"
        "Do not delete it. Tests must set WORDGRAB_DATA to a temp dir.\n",
        encoding="utf-8",
    )


def prepare(path: Path = None) -> None:
    """Ensure the sandbox is active. Import this before `wordgrab`."""
    if "WORDGRAB_DATA" not in os.environ:
        activate()
    sys.path.insert(0, str(ROOT))


def real_data_exists() -> bool:
    return (REAL_DATA / "words.db").exists()