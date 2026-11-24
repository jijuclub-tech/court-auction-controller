param([string]$folder, [string]$fn = "appraisal.pdf")
# 수집기1호용 저장 스크립트(3호 stash.ps1 경험). 아이디·비밀번호 없음.
# 1호 DESKTOP-C09LIQ2 (G v1.3.0, 2026-11-24): X: 먼저 X:\HDD1\BIDGUARD\collector1_out, 안 되면 C:\BIDGUARD\court_fallback (한영님 t1152u).
$src = Get-ChildItem C:\BIDGUARD\court_fallback\tmp_dl -Filter *.pdf | Select-Object -First 1
if (-not $src) { "NO_FILE"; exit 1 }
$b = [IO.File]::ReadAllBytes($src.FullName); "hdr=" + [Text.Encoding]::ASCII.GetString($b,0,5)
$h1 = (Get-FileHash $src.FullName -Algorithm SHA256).Hash.ToLower(); "sha=$h1"; "name=" + $src.Name; "bytes=" + $src.Length; "time=" + $src.LastWriteTime.ToString("yyyy-MM-ddTHH:mm:ss+09:00")
$NasRoot = "X:\HDD1\BIDGUARD\collector1_out"
$targets = @(@("X", "$NasRoot\court\cases\$folder"), @("LOCAL", "C:\BIDGUARD\court_fallback\court\cases\$folder"))
$done = $false
foreach ($t in $targets) {
  if ($t[0] -eq "X" -and -not (Test-Path $NasRoot)) { "x_skip=no_drive"; continue }
  try {
    New-Item -ItemType Directory -Force $t[1] -EA Stop | Out-Null
    if (Test-Path "$($t[1])\$fn") { "EXISTS_ON_" + $t[0]; exit 2 }
    Copy-Item $src.FullName "$($t[1])\$fn" -EA Stop
    $h2 = (Get-FileHash "$($t[1])\$fn" -Algorithm SHA256).Hash.ToLower()
    if ($h1 -eq $h2) { "nas=" + $t[0]; "nas_path=$($t[1])\$fn"; "nas_match=True"; $done = $true; break }
    else { $t[0] + "_sha_mismatch" }
  } catch { $t[0] + "_fail=" + $_.Exception.GetType().Name }
}
if (-not $done) { "nas_match=False" }
