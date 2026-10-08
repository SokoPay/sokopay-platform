# SokoPay demo server (Windows PowerShell). Builds a FRESH demo database, loads the demo
# data, prints the logins, and starts the server so the Android emulator (10.0.2.2) and a
# browser (http://127.0.0.1:8000) can reach it.
#
#   cd backend
#   powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
#
# Demo data only. Never point this at a real database.
$ErrorActionPreference = "Stop"
$env:DJANGO_SETTINGS_MODULE = "config.settings.dev"
$env:DATABASE_URL = "sqlite:///demo.sqlite3"
$env:SOKOPAY_ACTIVE_LICENCE = "DEMI"
$env:ALLOW_MOCK_INTEGRATIONS = "True"
$env:ALLOWED_HOSTS = "localhost,127.0.0.1,10.0.2.2"
$py = ".\.venv\Scripts\python.exe"
if (Test-Path demo.sqlite3) { Remove-Item demo.sqlite3 }
& $py manage.py migrate -v 0
& $py manage.py seed_demo --out demo-credentials.md
Write-Host "`nLogins saved to backend\demo-credentials.md. Starting the server on port 8000 (Ctrl+C to stop)..."
& $py manage.py runserver 0.0.0.0:8000
