param([string]$folder, [string]$fn = "appraisal.pdf")
# 2026-11-23 23:1x 지휘자 t348u: X:(NAS2e, ipDISK Drive)에 먼저 저장, 안 되면 Z:, 그것도 안 되면 로컬 대기 폴더
$src = Get-ChildItem C:\BIDGUARD\collector3_out\tmp_dl -Filter *.pdf | Select-Object -First 1
if (-not $src) { "NO_FILE"; exit 1 }
$b = [IO.File]::ReadAllBytes($src.FullName); "hdr=" + [Text.Encoding]::ASCII.GetString($b,0,5)
$h1 = (Get-FileHash $src.FullName -Algorithm SHA256).Hash.ToLower(); "sha=$h1"; "name=" + $src.Name; "bytes=" + $src.Length; "time=" + $src.LastWriteTime.ToString("yyyy-MM-ddTHH:mm:ss+09:00")
$targets = @(@("X", "X:\VOL1\Home.bidguard\collector3_out\court\cases\$folder"), @("Z", "Z:\HDD1\BIDGUARD\collector3_out\court\cases\$folder"), @("LOCAL", "C:\BIDGUARD\collector3_out\nas_wait\court\cases\$folder"))
$done = $false
foreach ($t in $targets) {
  if ($t[0] -eq "X" -and -not (Test-Path "X:\VOL1\Home.bidguard")) { "x_skip=no_drive"; continue }
  try {
    New-Item -ItemType Directory -Force $t[1] -EA Stop | Out-Null
    if (Test-Path "$($t[1])\$fn") { "EXISTS_ON_" + $t[0]; exit 2 }
    Copy-Item $src.FullName "$($t[1])\$fn" -EA Stop
    $h2 = (Get-FileHash "$($t[1])\$fn" -Algorithm SHA256).Hash.ToLower()
    if ($h1 -eq $h2) { "nas=" + $t[0]; "nas_path=$($t[1])\$fn"; "nas_match=True"; if ($t[0] -eq "Z") { "z_match=True" }; $done = $true; break }
    else { $t[0] + "_sha_mismatch" }
  } catch { $t[0] + "_fail=" + $_.Exception.GetType().Name }
}
if (-not $done) { "nas_match=False" }
Copy-Item $src.FullName C:\Users\user\c3_appr_tmp.pdf -Force
