<#
.SYNOPSIS
  一键演示 Alpme 设备码登录的 pending 过程：
  重启 mock -> 等待监听 -> 跑客户端 -> 输出 mock 轮询日志。

.DESCRIPTION
  适用于 Windows 本地演示。会先停掉占用指定端口的旧 mock（仅当命令行包含 mock_alpme.py）。
  默认跑完客户端后停止 mock；加 -KeepRunning 可保留 mock 继续使用。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\tests\demo_pending.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\tests\demo_pending.ps1 -Port 18081 -KeyName CLI -KeepRunning
#>
[CmdletBinding()]
param(
  [int]$Port = 18080,
  [ValidateSet('snake', 'camel')][string]$Style = 'snake',
  [string]$Polls = 'pending,pending,approved',
  [string]$KeyName = 'TEST',
  [string]$Python = 'python',
  [switch]$KeepRunning
)

$ErrorActionPreference = 'Stop'

$root   = Split-Path -Parent $PSScriptRoot
$mock   = Join-Path $PSScriptRoot 'mock_alpme.py'
$client = Join-Path $root 'alpme-get-key.py'
$log    = Join-Path $env:TEMP ("alpme_mock_$Port.log")
$err    = Join-Path $env:TEMP ("alpme_mock_$Port.err")

function Write-Info([string]$m) { Write-Host "[*] $m" -ForegroundColor Cyan }
function Write-Ok([string]$m) { Write-Host "[+] $m" -ForegroundColor Green }
function Write-Bad([string]$m) { Write-Host "[!] $m" -ForegroundColor Red }

# 1. 停掉占用端口的旧 mock
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conn) {
  $oldPid = $conn.OwningProcess
  $oldProc = Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue
  if ($oldProc -and $oldProc.CommandLine -match 'mock_alpme\.py') {
    Stop-Process -Id $oldPid -Force
    Write-Ok "stopped old mock PID $oldPid"
    Start-Sleep -Milliseconds 500
  } else {
    Write-Bad "port $Port is used by non-mock process PID $oldPid ($($oldProc.Name))"
    exit 1
  }
}

# 2. 启动新的 mock
Remove-Item $log, $err -Force -ErrorAction SilentlyContinue
$mockArgs = @($mock, '--port', "$Port", '--style', $Style, '--polls', $Polls, '--verbose')
$mockProc = Start-Process -FilePath $Python -ArgumentList $mockArgs `
  -WorkingDirectory $root `
  -RedirectStandardOutput $log -RedirectStandardError $err `
  -WindowStyle Hidden -PassThru

Write-Ok "mock started PID $($mockProc.Id), log: $log"

# 3. 等待端口监听
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Milliseconds 300
  if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    $ready = $true
    break
  }
}
if (-not $ready) {
  Write-Bad "mock did not listen on port $Port"
  Get-Content $err -ErrorAction SilentlyContinue
  if (-not $KeepRunning) { Stop-Process -Id $mockProc.Id -Force -ErrorAction SilentlyContinue }
  exit 1
}
Write-Ok "listening on 127.0.0.1:$Port"

# 4. 跑客户端
Write-Info "running client ..."
& $Python $client --base-url "http://127.0.0.1:$Port" --no-browser --key-name $KeyName
$clientExit = $LASTEXITCODE

# 5. 输出 mock 日志
Start-Sleep -Milliseconds 300
Write-Host "`n===== mock log =====" -ForegroundColor Yellow
Get-Content $log -ErrorAction SilentlyContinue
Write-Host "====================" -ForegroundColor Yellow

# 6. 清理
if (-not $KeepRunning) {
  Stop-Process -Id $mockProc.Id -Force -ErrorAction SilentlyContinue
  Write-Info "mock stopped (use -KeepRunning to keep it running)"
} else {
  Write-Info "mock kept running at 127.0.0.1:$Port (PID $($mockProc.Id))"
}

exit $clientExit
