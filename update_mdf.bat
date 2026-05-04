@echo off
setlocal EnableDelayedExpansion
title MDF 2026 — Update

echo.
echo ============================================================
echo   MDF 2026 — Update Tool
echo ============================================================
echo.

REM ── CONFIGURE THIS ONCE ──────────────────────────────────────────────────
REM Your GitHub raw URL (change YOUR_USERNAME to your GitHub username)
SET UPDATE_URL=https://raw.githubusercontent.com/YOUR_USERNAME/mdf-updates/main/mdf

REM ── Auto-detect root ─────────────────────────────────────────────────────
SET MDF_ROOT=%~dp0
IF "%MDF_ROOT:~-1%"=="\" SET MDF_ROOT=%MDF_ROOT:~0,-1%

REM Find Python
SET PYTHON=%MDF_ROOT%\dependencies\Python\python.exe
IF NOT EXIST "%PYTHON%" (
    where python >nul 2>&1 && SET PYTHON=python
)
IF NOT EXIST "%PYTHON%" (
    where python3 >nul 2>&1 && SET PYTHON=python3
)

echo Checking for updates...
echo.

"%PYTHON%" -c "
import json, urllib.request, os, sys, shutil

mdf_root   = sys.argv[1]
update_url = sys.argv[2]

# 1. Fetch manifest
try:
    with urllib.request.urlopen(update_url + '/manifest.json', timeout=10) as r:
        manifest = json.loads(r.read())
except Exception as e:
    print('ERROR: Cannot reach update server.')
    print('Check your internet connection.')
    print(str(e))
    sys.exit(1)

version = manifest.get('version', 'unknown')
notes   = manifest.get('notes', '')
files   = manifest.get('files', [])

# 2. Check local version
ver_file = os.path.join(mdf_root, '.mdf_version')
current  = ''
try:
    with open(ver_file) as f:
        current = f.read().strip()
except Exception:
    pass

print(f'Current version : {current or \"unknown\"}')
print(f'Latest version  : {version}')
if notes: print(f'Update notes    : {notes}')
print()

if current == version:
    print('You are already on the latest version. No update needed.')
    sys.exit(0)

print(f'Updating {len(files)} file(s)...')
print()

ok = 0; fail = 0
for entry in files:
    rel_path  = entry['path']          # where to place it in MDF root
    file_url  = update_url + '/' + entry['url']  # where to download from
    dest      = os.path.join(mdf_root, rel_path.replace('/', os.sep))

    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # Backup
        if os.path.exists(dest):
            shutil.copy2(dest, dest + '.bak')
        urllib.request.urlretrieve(file_url, dest)
        print(f'  OK : {rel_path}')
        ok += 1
    except Exception as e:
        print(f'  FAIL: {rel_path} — {e}')
        fail += 1

# 3. Save new version
try:
    with open(ver_file, 'w') as f:
        f.write(version)
except Exception:
    pass

print()
print(f'Done. {ok} updated, {fail} failed.')
if fail == 0:
    print(f'Updated to version {version}.')
    print('Restart MDF 2026 for changes to take effect.')
else:
    print('Some files failed. Close MDF 2026 and try again.')
" "%MDF_ROOT%" "%UPDATE_URL%"

echo.
pause
