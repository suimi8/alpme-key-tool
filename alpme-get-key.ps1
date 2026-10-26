<#
.SYNOPSIS
  Alpme API Key 一键获取（设备码登录流程）

.DESCRIPTION
  仅调用 Alpme 官方 HTTP API，不安装、不运行官方 VS Code 扩展。
  流程：POST /auth/link/start -> 浏览器确认 -> 轮询 /auth/link/{code} -> POST /keys 创建 Key。

  安全提示：
  - 官方 VS Code 扩展（alpme.alpme）已被确认会静默下发未签名木马，本脚本不使用该扩展。
  - 本脚本不会保存账号密码；登录由你在浏览器中完成。
  - 返回的 secret 只显示一次，请立即保存。

.PARAMETER BaseUrl
  Alpme 平台地址，默认 https://alpme.com（自建部署可改）。

.PARAMETER KeyName
  新建 Key 的名称，默认 CLI。

.PARAMETER ClientToken
  可选：自建部署需要的 x-alpme-client-token。

.PARAMETER NoBrowser
  不自动打开浏览器，只打印验证地址。

.PARAMETER OutFile
  可选：把 ALPME_API_KEY / ALPME_BASE_URL 写入指定文件（权限限制为当前用户）。

.PARAMETER TestKey
  不登录，直接测试一个已有 Key：GET /v1/models + 一次最小 chat 请求。

.PARAMETER ChatModel
  测试聊天时使用的模型，默认 deepseek-v4.1-flash。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\alpme-get-key.ps1 -KeyName "CLI"

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\alpme-get-key.ps1 -TestKey sk_live_xxx
#>
[CmdletBinding()]
param(
  [string]$BaseUrl = 'https://alpme.com',
  [string]$KeyName = 'CLI',
  [string]$ClientToken = '',
  [switch]$NoBrowser,
  [string]$OutFile = '',
  [string]$TestKey = '',
  [string]$ChatModel = 'deepseek-v4.1-flash'
)

$ErrorActionPreference = 'Stop'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Write-Info([string]$m) { Write-Host "[*] $m" -ForegroundColor Cyan }
function Write-Ok([string]$m) { Write-Host "[+] $m" -ForegroundColor Green }
function Write-Err([string]$m) { Write-Host "[!] $m" -ForegroundColor Red }

function Get-ErrBody($e) {
  try {
    if ($e.ErrorDetails -and $e.ErrorDetails.Message) { return [string]$e.ErrorDetails.Message }
    if ($e.Exception.Response) {
      $sr = New-Object IO.StreamReader($e.Exception.Response.GetResponseStream())
      return [string]$sr.ReadToEnd()
    }
  } catch {}
  return [string]$e.Exception.Message
}

$base = $BaseUrl.TrimEnd('/')
$common = @{ 'accept' = 'application/json'; 'x-alpme-client' = 'vscode' }
if ($ClientToken) { $common['x-alpme-client-token'] = $ClientToken }

# ---------- 仅测试已有 Key ----------
if ($TestKey) {
  Write-Info "测试 Key：$base/v1"
  $h = @{ 'accept' = 'application/json'; 'authorization' = "Bearer $TestKey" }

  try {
    $m = Invoke-RestMethod -Uri "$base/v1/models" -Headers $h -Method Get -TimeoutSec 30
    Write-Ok ("/v1/models OK，模型数=" + @($m.data).Count)
  } catch {
    Write-Err ("/v1/models 失败：" + (Get-ErrBody $_))
    exit 1
  }

  $chatBody = @{ model = $ChatModel; messages = @(@{ role = 'user'; content = 'hi' }); max_tokens = 8 } | ConvertTo-Json -Depth 5 -Compress
  try {
    $r = Invoke-RestMethod -Uri "$base/v1/chat/completions" -Method Post -Headers $h -ContentType 'application/json' -Body $chatBody -TimeoutSec 60
    Write-Ok ("chat OK，model=" + $r.model + " content=" + $r.choices[0].message.content)
  } catch {
    Write-Err ("chat 失败：" + (Get-ErrBody $_))
    exit 1
  }
  exit 0
}

# ---------- 设备码登录 ----------
Write-Info "1/4 创建设备登录会话 ..."
try {
  $start = Invoke-RestMethod -Uri "$base/api/vscode/auth/link/start" -Method Post -Headers $common -ContentType 'application/json' -Body '{}' -TimeoutSec 30
} catch {
  Write-Err ("创建会话失败：" + (Get-ErrBody $_))
  exit 1
}

$code      = $start.code
$userCode  = if ($start.userCode) { $start.userCode } elseif ($start.user_code) { $start.user_code } else { '' }
$url       = if ($start.verifyUrlComplete) { $start.verifyUrlComplete }
             elseif ($start.verify_url_complete) { $start.verify_url_complete }
             elseif ($start.verifyUrl) { $start.verifyUrl }
             else { $start.verify_url }
$interval  = if ($start.intervalSeconds) { [int]$start.intervalSeconds }
             elseif ($start.interval_seconds) { [int]$start.interval_seconds }
             else { 3 }
$expires   = if ($start.expiresInSeconds) { [int]$start.expiresInSeconds }
             elseif ($start.expires_in_seconds) { [int]$start.expires_in_seconds }
             else { 600 }

if (-not $code -or -not $url) {
  Write-Err "平台未返回 code 或 verifyUrl"
  exit 1
}

Write-Host ""
Write-Host "  用户码: $userCode" -ForegroundColor Yellow
Write-Host "  验证页: $url" -ForegroundColor Yellow
Write-Host ""
if (-not $NoBrowser) {
  try { Start-Process $url } catch { Write-Err "无法自动打开浏览器，请手动访问上面的地址" }
}

Write-Info "2/4 等待浏览器确认（最长 $expires 秒，每 $interval 秒轮询）..."
$deadline = (Get-Date).AddSeconds($expires)
$token = $null
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Seconds $interval
  try {
    $st = Invoke-RestMethod -Uri "$base/api/vscode/auth/link/$code" -Headers $common -Method Get -TimeoutSec 20
  } catch {
    $msg = Get-ErrBody $_
    if ($msg -match '\b4\d\d\b') { Write-Err ("登录失败：" + $msg); exit 1 }
    continue
  }

  switch ([string]$st.status) {
    'approved'  { $token = $st.token }
    'expired'   { Write-Err "会话已过期，请重试"; exit 1 }
    'denied'    { Write-Err "已在浏览器中拒绝授权"; exit 1 }
    'not_found' { Write-Err "会话不存在或已失效"; exit 1 }
  }
  if ($token) { break }
}

if (-not $token) {
  Write-Err "等待超时，未获得会话 token"
  exit 1
}
Write-Ok "登录成功"

$auth = @{ 'accept' = 'application/json'; 'authorization' = "Bearer $token"; 'x-alpme-client' = 'vscode' }
if ($ClientToken) { $auth['x-alpme-client-token'] = $ClientToken }

Write-Info "3/4 读取账号与配额 ..."
try {
  $me = Invoke-RestMethod -Uri "$base/api/vscode/me" -Headers $auth -Method Get -TimeoutSec 30
  Write-Ok ("账号: " + $me.user.email + "，套餐: " + $me.plan.slug)
} catch {
  Write-Err ("读取 /me 失败：" + (Get-ErrBody $_))
}

try {
  $keys = Invoke-RestMethod -Uri "$base/api/vscode/keys" -Headers $auth -Method Get -TimeoutSec 30
  Write-Ok ("已有 Key: " + @($keys.keys).Count + "，配额: " + $keys.quota.active + "/" + $keys.quota.max)
} catch {}

Write-Info "4/4 创建 API Key（$KeyName）..."
$createBody = @{ name = $KeyName } | ConvertTo-Json -Compress
try {
  $created = Invoke-RestMethod -Uri "$base/api/vscode/keys" -Method Post -Headers $auth -ContentType 'application/json' -Body $createBody -TimeoutSec 30
} catch {
  Write-Err ("创建 Key 失败：" + (Get-ErrBody $_))
  exit 1
}

$keyObj = if ($created.key) { $created.key } elseif ($created.created) { $created.created } else { $null }
$secret = if ($created.secret) { $created.secret } elseif ($keyObj -and $keyObj.secret) { $keyObj.secret } else { $null }
$kid    = if ($keyObj -and $keyObj.id) { $keyObj.id } else { '?' }
if (-not $secret) {
  Write-Err "平台未返回 secret"
  exit 1
}

Write-Host ""
Write-Host "================ API KEY ================" -ForegroundColor Green
Write-Host "Key ID : $kid"
Write-Host "Name   : $KeyName"
Write-Host "Secret : $secret" -ForegroundColor Yellow
Write-Host "Base   : $base/v1"
Write-Host "========================================="
Write-Host "secret 只显示这一次，请立即保存。" -ForegroundColor Red

if ($OutFile) {
  $lines = @("ALPME_API_KEY=$secret", "ALPME_BASE_URL=$base/v1")
  [IO.File]::WriteAllLines($OutFile, $lines, (New-Object Text.UTF8Encoding($false)))
  try { icacls $OutFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null } catch {}
  Write-Ok "已写入 $OutFile（权限限制为当前用户）"
}
