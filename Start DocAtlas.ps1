param(
  [switch]$SampleOne
)

$ErrorActionPreference = 'Stop'
$AppRoot = $PSScriptRoot
$DataRoot = Join-Path $AppRoot '.docatlas'
$PidFile = Join-Path $DataRoot 'server.pid'
$OutLog = Join-Path $DataRoot 'server.log'
$ErrorLog = Join-Path $DataRoot 'server-error.log'

Set-Location -LiteralPath $AppRoot
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null

try {
  $health = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 2
  if ($health.ok) {
    if (-not $SampleOne) {
      $sources = Invoke-RestMethod 'http://127.0.0.1:8765/api/sources' -TimeoutSec 3
      foreach ($source in $sources) {
        $payload = @{ source_id = $source.id; force = $false } | ConvertTo-Json
        Invoke-RestMethod 'http://127.0.0.1:8765/api/scan' -Method Post -ContentType 'application/json' -Body $payload -TimeoutSec 30 | Out-Null
      }
    }
    Start-Process 'http://127.0.0.1:8765'
    exit 0
  }
} catch {
  # Start a new local server below.
}

if (-not (Test-Path -LiteralPath (Join-Path $AppRoot 'dist\client\index.html'))) {
  if (-not (Test-Path -LiteralPath (Join-Path $AppRoot 'node_modules'))) {
    & npm install
    if ($LASTEXITCODE -ne 0) { throw 'Không thể cài phần giao diện.' }
  }
  & npm run build
  if (-not (Test-Path -LiteralPath (Join-Path $AppRoot 'dist\client\index.html'))) {
    throw 'Không thể tạo giao diện DocAtlas.'
  }
}

$arguments = @((Join-Path $AppRoot 'server.py'), '--port', '8765')
if ($SampleOne) { $arguments += '--sample-one' }

$process = Start-Process -FilePath 'python' `
  -ArgumentList $arguments `
  -WorkingDirectory $AppRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $OutLog `
  -RedirectStandardError $ErrorLog `
  -PassThru

Set-Content -LiteralPath $PidFile -Value $process.Id

for ($attempt = 0; $attempt -lt 40; $attempt++) {
  Start-Sleep -Milliseconds 250
  try {
    $health = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 1
    if ($health.ok) {
      Start-Process 'http://127.0.0.1:8765'
      exit 0
    }
  } catch {
    # Keep waiting while the local database starts.
  }
}

throw "DocAtlas chưa khởi động được. Xem file $ErrorLog"
