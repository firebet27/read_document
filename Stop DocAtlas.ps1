$ErrorActionPreference = 'Stop'
$AppRoot = $PSScriptRoot
$PidFile = Join-Path $AppRoot '.docatlas\server.pid'

if (-not (Test-Path -LiteralPath $PidFile)) {
  Write-Host 'DocAtlas không chạy hoặc không có thông tin tiến trình.'
  exit 0
}

$ServerPid = [int](Get-Content -LiteralPath $PidFile -Raw)
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $ServerPid" -ErrorAction SilentlyContinue
if ($process -and $process.CommandLine -like "*$AppRoot*server.py*") {
  Stop-Process -Id $ServerPid
  Write-Host 'Đã dừng DocAtlas.'
} else {
  Write-Host 'Không dừng tiến trình vì nó không còn là máy chủ DocAtlas.'
}
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
