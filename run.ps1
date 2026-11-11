# Start Pitchdeck Title Tuner with the project's own virtual environment (Windows).
# Usage (from any folder):  & "C:\path\to\PitchdeckTitleTuner\run.ps1"
$ErrorActionPreference = "Stop"
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Virtual environment not found. Create it first:" -ForegroundColor Yellow
    Write-Host "  py -3.12 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}
Set-Location $PSScriptRoot   # so .streamlit/config.toml and .env are picked up
& $py -m streamlit run app.py @args
