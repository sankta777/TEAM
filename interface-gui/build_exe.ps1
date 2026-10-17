# Build PyInstaller EXE Script
# Untuk Ruskomponen_BOT_BLK

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Building EXE for Ruskomponen_BOT_BLK" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan

# Set working directory
$projectPath = "D:\My Project\RNV3\Interface\Upload ke github\Code\SourceCodeInterface-RNV3"
cd $projectPath

# Activate venv
Write-Host "Aktivasi virtual environment..." -ForegroundColor Yellow
.\venv\Scripts\Activate.ps1

# Define paths
$scriptFile = "$projectPath\Ruskomponen_BOT_BLK.py"
$iconFile = "D:\logo ruskomponen\logo ruskomponen\ico format\R b vy.ico"
$outputDir = "$projectPath\dist"

Write-Host "`nMembangun EXE..." -ForegroundColor Yellow
Write-Host "Script: $scriptFile" -ForegroundColor Green
Write-Host "Icon: $iconFile" -ForegroundColor Green
Write-Host "Output: $outputDir" -ForegroundColor Green

# Run PyInstaller dengan config yang benar
python -m PyInstaller --onefile `
  --console `
  --icon="$iconFile" `
  --hidden-import=PyQt5.QtGui `
  --hidden-import=PyQt5.QtCore `
  --hidden-import=PyQt5.QtSerialPort `
  --hidden-import=PyQt5.QtWidgets `
  --hidden-import=PyQt5.QtSerialPort `
  --hidden-import=serial `
  --distpath="$outputDir" `
  --name="Ruskomponen_BOT_BLK" `
  "$scriptFile"

Write-Host "`n=====================================" -ForegroundColor Green
Write-Host "Build Selesai!" -ForegroundColor Green
Write-Host "EXE tersimpan di: $outputDir\Ruskomponen_BOT_BLK.exe" -ForegroundColor Green
Write-Host "=====================================" -ForegroundColor Green
