"""The sandbox must actually catch a violation, not just look pretty.

Deliberately writes to the real data directory to prove verify() notices.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from sandbox import (  # noqa: E402
    REAL_DATA, SandboxViolation, activate, fingerprint, guard_real_data, rebase,
    verify,
)

failures: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' :: ' + extra) if extra else ''}")
    if not cond:
        failures.append(name)


def main() -> int:
    tmp = activate("wordgrab-sandbox-selftest_")

    # 全新 clone 里 data/ 根本不存在（被 gitignore），这个自检需要往里面写东西、
    # 并且需要至少有一个真实文件才能验证"改动已有文件"也抓得到。先把目录和哨兵
    # 建好——只新建，不碰任何已有数据。
    empty = not REAL_DATA.exists() or not any(p.is_file() for p in REAL_DATA.iterdir())
    if empty:
        guard_real_data()
        rebase()  # 哨兵是有意为之的一次性改动
        print(f"   (created an empty {REAL_DATA} + sentinel for this self-test)")

    check("activate points WORDGRAB_DATA at a temp dir",
          os.environ["WORDGRAB_DATA"] == str(tmp), os.environ["WORDGRAB_DATA"])
    check("sandbox is NOT the real data dir",
          str(tmp) != str(REAL_DATA) and str(tmp).startswith(
              os.environ.get("TEMP", "c:\\users")[:6].lower()) is not None)
    check("real data dir exists to protect", REAL_DATA.exists(), str(REAL_DATA))

    # the sandbox dir is disposable: writing there must be fine
    (tmp / "words.db").write_text("scratch", encoding="utf-8")
    try:
        verify()
        check("writing inside the sandbox is allowed", True)
    except SandboxViolation as exc:
        check("writing inside the sandbox is allowed", False, str(exc))

    # now the important half: writing to the REAL dir must be caught
    activate("wordgrab-sandbox-selftest_")
    marker = REAL_DATA / "selftest-marker.tmp"
    try:
        marker.write_text("oops", encoding="utf-8")
        try:
            verify()
            check("verify() catches a new file in the real data dir", False,
                  "it did not notice")
        except SandboxViolation as exc:
            check("verify() catches a new file in the real data dir",
                  "selftest-marker.tmp" in str(exc), str(exc)[:120])
    finally:
        marker.unlink(missing_ok=True)

    # modifying an existing file must also be caught
    activate("wordgrab-sandbox-selftest_")
    existing = next((p for p in REAL_DATA.iterdir() if p.is_file()), None)
    if existing:
        original = existing.read_bytes()
        try:
            existing.write_bytes(original + b"\n<!-- touched -->")
            try:
                verify()
                check("verify() catches a modified file", False, "it did not notice")
            except SandboxViolation as exc:
                check("verify() catches a modified file",
                      existing.name in str(exc), str(exc)[:120])
        finally:
            existing.write_bytes(original)
    else:
        check("verify() catches a modified file", True, "(no file to touch)")

    # and after restoring, verify() passes again
    activate("wordgrab-sandbox-selftest_")
    try:
        verify()
        check("clean state passes verify()", True)
    except SandboxViolation as exc:
        check("clean state passes verify()", False, str(exc))

    # fingerprint must notice content changes, not just timestamps
    a = fingerprint(REAL_DATA)
    check("fingerprint is a dict of files", isinstance(a, dict) and len(a) > 0,
          str(list(a)[:3]))
    check("fingerprint stores size and hash",
          all(isinstance(v, tuple) and len(v) == 2 for v in a.values()))

    # the sentinel marks the real dir for humans and scripts. Writing it is the
    # one legitimate change this self-test makes, so re-baseline afterwards.
    activate("wordgrab-sandbox-selftest_")
    guard_real_data()
    sentinel = REAL_DATA / ".wordgrab-real-data"
    check("sentinel written into the real data dir", sentinel.exists(), str(sentinel))
    check("sentinel warns about deletion",
          "Do not delete" in sentinel.read_text(encoding="utf-8"))

    activate("wordgrab-sandbox-selftest_")
    rebase()  # the sentinel is an intended, one-time change
    verify()
    check("verify() passes once the sentinel is in place", True)

    # rebase must be an explicit choice, not a silent escape hatch
    activate("wordgrab-sandbox-selftest_")
    (REAL_DATA / "rebase-probe.tmp").write_text("x", encoding="utf-8")
    try:
        verify()
        check("verify() still catches changes after a rebase", False,
              "rebase disabled detection")
    except SandboxViolation:
        check("verify() still catches changes after a rebase", True)
    finally:
        (REAL_DATA / "rebase-probe.tmp").unlink(missing_ok=True)
        activate("wordgrab-sandbox-selftest_")
        rebase()
    print()
    print("ALL PASS" if not failures else "FAILURES: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())