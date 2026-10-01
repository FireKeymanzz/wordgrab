@echo off
setlocal
cd /d "%~dp0"

rem ---- hard stop: the real vocabulary database is NOT test output ------------
rem Every test must call tests\sandbox.activate() first, which points
rem WORDGRAB_DATA at a temp dir, then verify() at the end to prove the real
rem library was untouched. If a test ever writes here, it fails loudly.
if not exist "tests\sandbox.py" (
  echo ERROR: tests\sandbox.py is missing - refusing to run.
  echo Tests are only safe with the sandbox in place.
  exit /b 1
)

rem keep a pre-test snapshot so a bug can never cost you the library
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe

rem WordGrab holds a single-instance mutex. If it is still running, (a) the
rem snapshot below turns into a no-op (the new process just asks the running
rem one to show its panel) and (b) the sandbox check reports a false alarm,
rem because the app's own connections keep rewriting the SQLite -wal/-shm.
"%PY%" -c "import sys; sys.path.insert(0,'.'); from wordgrab import instance; sys.exit(0 if instance.is_running() else 1)" >nul 2>&1
if not errorlevel 1 (
  echo WARNING: WordGrab is still running in the tray.
  echo          Quit it first, or the tests will report a false alarm on your
  echo          real words.db and the pre-test snapshot will be skipped.
  echo.
)

if exist "data\words.db" (
  echo [0/1] snapshotting the current library before tests...
  "%PY%" -X utf8 -m wordgrab --backup >nul 2>&1 || (
    python -X utf8 -m wordgrab --backup >nul 2>&1
  )
)

"%PY%" -X utf8 tests\test_core.py || goto :fail
echo [1/10] core ok

"%PY%" -X utf8 tests\test_capture_win.py || goto :fail
echo [2/10] capture ok

"%PY%" -X utf8 tests\test_e2e_dblclick.py || goto :fail
echo [3/10] end to end ok

"%PY%" -X utf8 tests\test_regression_thread.py || goto :fail
echo [4/10] regression ok

"%PY%" -X utf8 tests\test_ui.py || goto :fail
echo [5/10] ui ok

"%PY%" -X utf8 tests\test_extension.py || goto :fail
echo [6/10] extension ok

"%PY%" -X utf8 tests\test_export.py || goto :fail
echo [7/10] export ok

"%PY%" -X utf8 tests\test_concurrency.py || goto :fail
echo [8/10] concurrency ok

"%PY%" -X utf8 tests\test_backup.py || goto :fail
echo [9/10] backup ok

"%PY%" -X utf8 tests\test_sandbox_guard.py || goto :fail
echo [10/10] sandbox self-test ok

echo.
echo All tests passed. The real library was never touched.
exit /b 0

:fail
echo.
echo Tests FAILED.
echo If the message mentions the sandbox or a modified data file, treat it
echo seriously: restore with  python -m wordgrab --backups
exit /b 1