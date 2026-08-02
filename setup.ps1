Write-Host "RenzeiMacro - Prerequisite Installer" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan

# 1. Check and Install Python
Write-Host "`n[1/4] Checking for Python..." -ForegroundColor Cyan
$pythonInstalled = Get-Command "python" -ErrorAction SilentlyContinue
if (-not $pythonInstalled) {
    Write-Host "Python not found. Installing Python 3.12 via winget..." -ForegroundColor Yellow
    winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
    # Refresh PATH in current session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
} else {
    Write-Host "Python is already installed." -ForegroundColor Green
}

# 2. Install Python Dependencies
Write-Host "`n[2/4] Installing Python dependencies..." -ForegroundColor Cyan
$reqPath = Join-Path $PSScriptRoot "python\requirements.txt"
if (Test-Path $reqPath) {
    python -m pip install --upgrade pip
    python -m pip install -r "$reqPath"
} else {
    Write-Host "Warning: requirements.txt not found at $reqPath" -ForegroundColor Red
}

# 3. Check and Install AutoHotkey v2
Write-Host "`n[3/4] Checking for AutoHotkey v2..." -ForegroundColor Cyan
$ahkPath = "${env:ProgramFiles}\AutoHotkey\v2\AutoHotkey64.exe"
if (-not (Test-Path $ahkPath)) {
    Write-Host "AutoHotkey v2 not found. Installing via winget..." -ForegroundColor Yellow
    winget install AutoHotkey.AutoHotkey --accept-package-agreements --accept-source-agreements --silent
} else {
    Write-Host "AutoHotkey v2 is already installed." -ForegroundColor Green
}

# 4. Check and Install Tesseract OCR
Write-Host "`n[4/4] Checking for Tesseract OCR..." -ForegroundColor Cyan
$tesseractPath = "${env:ProgramFiles}\Tesseract-OCR\tesseract.exe"
$tesseractInstalled = Get-Command "tesseract" -ErrorAction SilentlyContinue

if ((-not $tesseractInstalled) -and (-not (Test-Path $tesseractPath))) {
    Write-Host "Tesseract OCR not found. Installing via winget..." -ForegroundColor Yellow
    winget install tesseract-ocr.tesseract --accept-package-agreements --accept-source-agreements --silent
    
    # Add to machine PATH if not present
    $machinePath = [Environment]::GetEnvironmentVariable('PATH', 'Machine')
    if ($machinePath -notmatch 'Tesseract-OCR') {
        Write-Host "Adding Tesseract-OCR to system PATH..." -ForegroundColor Yellow
        [Environment]::SetEnvironmentVariable('PATH', "$machinePath;${env:ProgramFiles}\Tesseract-OCR", 'Machine')
        $env:Path += ";${env:ProgramFiles}\Tesseract-OCR"
    }
} else {
    Write-Host "Tesseract OCR is already installed." -ForegroundColor Green
}

Write-Host "`n====================================" -ForegroundColor Cyan
Write-Host "Installation Complete! You are ready to use RenzeiMacro." -ForegroundColor Green
Write-Host "Note: If any new tools were installed, you may need to restart your computer or IDE for system PATH changes to take effect." -ForegroundColor Yellow
