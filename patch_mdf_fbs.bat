@echo off
setlocal EnableDelayedExpansion
title MDF 2026 — FBS Patch Installer

echo.
echo ============================================================
echo   MDF 2026 — Full Body Swap Patch Installer
echo   Applies all FBS changes to your existing MDF installation
echo ============================================================
echo.

REM ── Find MDF 2026 root ────────────────────────────────────────────────────
REM The patch files folder is expected to be inside MDF 2026 root
REM OR run this BAT from wherever your MDF 2026 folder is

SET PATCH_DIR=%~dp0
IF "%PATCH_DIR:~-1%"=="\" SET PATCH_DIR=%PATCH_DIR:~0,-1%

REM Try to detect MDF root — look for main.py as indicator
SET MDF_ROOT=
IF EXIST "%PATCH_DIR%\main.py" SET MDF_ROOT=%PATCH_DIR%
IF EXIST "%PATCH_DIR%\..\main.py" SET MDF_ROOT=%PATCH_DIR%\..

REM If not found, ask user
IF "%MDF_ROOT%"=="" (
    echo Could not auto-detect MDF 2026 folder.
    echo.
    set /p MDF_ROOT=Enter full path to your MDF 2026 folder: 
)

REM Normalize path
PUSHD "%MDF_ROOT%" 2>nul
IF errorlevel 1 (
    echo ERROR: Folder not found: %MDF_ROOT%
    pause
    exit /b 1
)
SET MDF_ROOT=%CD%
POPD

echo MDF 2026 folder: %MDF_ROOT%
echo.

REM ── Verify it looks like MDF ──────────────────────────────────────────────
IF NOT EXIST "%MDF_ROOT%\main.py" (
    echo ERROR: This does not look like an MDF 2026 folder.
    echo Could not find main.py in: %MDF_ROOT%
    pause
    exit /b 1
)

REM ── Find Python ───────────────────────────────────────────────────────────
SET PYTHON=%MDF_ROOT%\dependencies\Python\python.exe
IF NOT EXIST "%PYTHON%" (
    where python >nul 2>&1
    IF %errorlevel% == 0 SET PYTHON=python
)
IF NOT EXIST "%PYTHON%" (
    where python3 >nul 2>&1
    IF %errorlevel% == 0 SET PYTHON=python3
)

echo Python: %PYTHON%
echo.

REM ── Run the Python patcher ────────────────────────────────────────────────
"%PYTHON%" -c "
import os, sys, shutil

patch_dir = sys.argv[1]
mdf_root  = sys.argv[2]

# Map: source file in patch folder -> destination relative to MDF root
FILE_MAP = {
    'fbs_ui.html':             'fbs_ui.html',
    'fbs_server.py':           'fbs_server.py',
    'decart-sdk.js':           'decart-sdk.js',
    'main_ui.py':              os.path.join('app', 'ui', 'main_ui.py'),
    'common_layout_data.py':   os.path.join('app', 'ui', 'widgets', 'common_layout_data.py'),
    'lucy_client.py':          os.path.join('app', 'processors', 'lucy_client.py'),
    'update_mdf.bat':          'update_mdf.bat',
}

print('Applying patch files...')
print()

ok   = 0
skip = 0
fail = 0

for src_name, dest_rel in FILE_MAP.items():
    src_path  = os.path.join(patch_dir, src_name)
    dest_path = os.path.join(mdf_root, dest_rel)

    if not os.path.exists(src_path):
        print(f'  SKIP (not in patch): {src_name}')
        skip += 1
        continue

    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        # Backup existing file
        if os.path.exists(dest_path):
            backup = dest_path + '.bak'
            shutil.copy2(dest_path, backup)
        shutil.copy2(src_path, dest_path)
        print(f'  OK: {src_name} -> {dest_rel}')
        ok += 1
    except Exception as e:
        print(f'  FAIL: {src_name} -> {e}')
        fail += 1

print()
print(f'Results: {ok} patched, {skip} skipped, {fail} failed.')
if fail > 0:
    print('Some files failed — check permissions or close MDF first.')
else:
    print('Patch complete! Restart MDF 2026 for changes to take effect.')
" "%PATCH_DIR%" "%MDF_ROOT%"

echo.
echo ============================================================
echo  Done. Restart MDF 2026 to apply changes.
echo ============================================================
echo.
pause
