# t325u(15:22): 사건 사이 6~9분, 로봇 확인 뒤 8~10분
# 수집기3호 오늘(11/23) 매각 감정평가서 순차 받기 — 한영님 08:50/08:51 지시. 피코2만, 사건 사이 4~6분(로봇 확인 뒤 5~7분)
param([string]$End = "11:05", [int]$PerWindow = 5, [int]$KeepOldFirst = 1, [int]$MaxCases = 999)
$P = "C:\BIDGUARD\collector3\plans"; $AF = "C:\BIDGUARD\collector3\auto_flow"; $dl = "C:\BIDGUARD\collector3_out\tmp_dl"
$RES = "C:\BIDGUARD\collector3_out\today1123_results.jsonl"; $CAP = "C:\BIDGUARD\collector3_out\recaptcha_log.jsonl"
$OUTU = "C:\Users\user\c3_today"; New-Item -ItemType Directory -Force $OUTU, "C:\BIDGUARD\collector3_out\done_dl" | Out-Null
$lf = "$P\last_case_end.txt"; $RG = "C:\BIDGUARD\collector3\remote_guard"
$yl = @{"2026"=@(410,432); "2025"=@(438,460); "2024"=@(465,490); "2023"=@(494,520); "2022"=@(522,548)}
$endT = [datetime]::ParseExact((Get-Date -Format "yyyy-MM-dd") + " " + $End, "yyyy-MM-dd HH:mm", $null)
$q = Get-Content "$P\queue_today.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$done = @{}; if (Test-Path $RES) { Get-Content $RES -Encoding UTF8 | % { try { $r = $_ | ConvertFrom-Json; if ($r.status -in @("ok","failed","skipped","notice")) { $done[$r.folder] = 1 } } catch {} } }
function W($o) { $o.t = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss+09:00"); Add-Content $RES ($o | ConvertTo-Json -Compress -Depth 5) -Encoding UTF8 }
function capn { if (Test-Path $CAP) { @(Get-Content $CAP -Encoding UTF8).Count } else { 0 } }
function waitpdf($sec) { for ($i = 0; $i -lt $sec; $i += 2) { $f = Get-ChildItem $dl -Filter *.pdf -EA SilentlyContinue; $pt = Get-ChildItem $dl -Filter *.crdownload -EA SilentlyContinue; if ($f -and -not $pt) { return $true }; Start-Sleep 2 }; return $false }
function step($plan) { Push-Location $AF; $env:BIDGUARD_PICO_PORT = "COM9"; $o = python -u tools\c2_step_click_G_v1.2.4.py $plan --end 23:59 --keep --no-beep 2>&1 | Out-String; $rc = $LASTEXITCODE; Pop-Location; return @($rc, $o) }
$inWin = 0; $first = $true; $consec = 0; $n = 0; $capHit = $false
foreach ($e in $q) {
  if ($done.ContainsKey($e.folder)) { continue }
  if ((Get-Date) -gt $endT) { "TIME_UP next=" + $e.case; break }
  if ($n -ge $MaxCases) { "MAX_CASES next=" + $e.case; break }
  if ((Test-Path "Z:\HDD1\BIDGUARD\collector3_out\court\cases\$($e.folder)\appraisal.pdf") -or (Test-Path "X:\VOL1\Home.bidguard\collector3_out\court\cases\$($e.folder)\appraisal.pdf")) { W @{status="skipped"; why="already_on_nas"; folder=$e.folder; case=$e.case; court=$e.court}; continue }
  while ((Test-Path "$RG\pause.flag") -or (Test-Path "$RG\hold.flag")) { "flag_wait " + (Get-Date -Format HH:mm:ss); Start-Sleep 30 }
  if (Get-ChildItem $dl -Filter *.pdf) { "TMP_NOT_EMPTY"; break }
  if (Test-Path $lf) { $last = [datetime]::Parse((Get-Content $lf)); $gap = if ($capHit) { Get-Random -Minimum 480 -Maximum 600 } else { Get-Random -Minimum 360 -Maximum 540 }; $wait = $gap - ((Get-Date) - $last).TotalSeconds; if ($wait -gt 0) { "wait=" + [int]$wait; Start-Sleep -Seconds ([int]$wait) } }
  $useOpen = $first -or ($inWin -ge $PerWindow) -or ($consec -ge 2)
  $tpl = if ($useOpen) { "open_today_tpl.txt" } else { "full_today_tpl.txt" }
  $keep = if ($first -and $KeepOldFirst -eq 1) { "true" } else { "false" }
  $a2 = ($e.alts | ConvertTo-Json -Compress); if ($e.alts.Count -eq 1) { $a2 = "[" + $a2 + "]" }
  $sy = $e.year; $num = [string]$e.num; $tt = (Get-Content "$P\$tpl" -Raw -Encoding UTF8)
  if ($e.syear) {  # 병합 창: 검색은 다른 사건(snum), 감정평가서 창 확인·저장은 이 사건(지휘자 12:58)
    $mc = [string]$e.year + "타경" + [string]$e.num
    $tt = $tt.Replace('"stop_unless_any": ["__YEAR__타경__NUM__", "타경__NUM__", "__N4__"], "fuzzy_case": "__YEAR__타경__NUM__"', ('"stop_unless_any": ["' + $mc + '"], "fuzzy_case": "' + $mc + '"'))
    $sy = [string]$e.syear; $num = [string]$e.snum }
  $y = $yl[$sy]
  $pt = $tt.Replace('__COURT__',$e.alts[0]).Replace('__CALTS__',$a2).Replace('__CALTS2__',$a2).Replace('__YEAR__',$sy).Replace('__NUM__',$num).Replace('__N4__',$num.Substring([Math]::Max(0,$num.Length-4))).Replace('__YLO__',[string]$y[0]).Replace('__YHI__',[string]$y[1]).Replace('__YC__',[string][int](($y[0]+$y[1])/2)).Replace('__KEEP__',$keep).Replace('__LABEL__',($e.court + " " + $e.case))
  $null = $pt | ConvertFrom-Json
  $pf = "$P\d_$($e.folder).json"; [IO.File]::WriteAllText($pf, $pt, (New-Object Text.UTF8Encoding($false)))
  $c0 = capn; "start " + (Get-Date -Format HH:mm:ss) + " " + $e.court + " " + $e.case + " tpl=" + $tpl
  $r = step $pf; $rc = $r[0]; $n++
  if ($useOpen) { $inWin = 0; $first = $false }
  $inWin++
  $run = (Get-ChildItem ("C:\BIDGUARD\collector3_out\steps\" + (Get-Date -Format "yyyy-MM-dd")) -Directory | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
  $lastlog = (Get-Content "$run\run_log.jsonl" -Encoding UTF8 | Select-Object -Last 3) -join " "
  $opened = $lastlog -match '"step": "open_pdf"'
  $ok = waitpdf 30
  if (-not $ok -and $opened -and $rc -eq 0) { "RETRY_OPEN"; $r2 = step "$P\open_only.json"; $ok = waitpdf 40 }
  $c1 = capn; if ($c1 -gt $c0) { $capHit = $true }
  $round = $null; $ml = Get-ChildItem $run -Filter "*_modal_layout.json" -EA SilentlyContinue | Select-Object -First 1
  if ($ml) { $j = Get-Content $ml.FullName -Raw -Encoding UTF8 | ConvertFrom-Json; $rl = $j.lines | ? { $_.text -match '열람회차' } | Select-Object -First 1; if ($rl) { $round = (($j.lines | ? { [math]::Abs($_.center[1] - $rl.center[1]) -le 14 -and $_.box[0] -ge $rl.box[0] } | % { $_.text }) -join " ") } }
  (Get-Date).ToString("o") | Set-Content $lf
  if ($ok) {
    $src = Get-ChildItem $dl -Filter *.pdf | Select-Object -First 1
    $st = powershell -ExecutionPolicy Bypass -File "$P\stash.ps1" $e.folder | Out-String
    Copy-Item $src.FullName "$OUTU\$($e.folder).pdf" -Force
    Move-Item $src.FullName ("C:\BIDGUARD\collector3_out\done_dl\" + $e.folder + "__" + $src.Name) -Force
    $null = step "$P\close_modal.json"
    W @{status="ok"; folder=$e.folder; case=$e.case; court=$e.court; code=$e.code; items=$e.items; p=$e.p; rc=$rc; run=(Split-Path $run -Leaf); orig=$src.Name; stash=$st.Trim(); round=$round; captcha_events=($c1-$c0)}
    $consec = 0; "OK " + $e.case + " round=" + $round
  } else {
    $stt = if ($rc -eq 23) { "notice" } else { "failed" }
    if ($rc -eq 23) { Add-Content "C:\BIDGUARD\collector3_out\retry_list_1123.txt" ($e.folder + " " + $e.case + " P_NOTICE " + (Get-Date -Format HH:mm:ss)) -Encoding UTF8 }
    W @{status=$stt; folder=$e.folder; case=$e.case; court=$e.court; code=$e.code; p=$e.p; rc=$rc; run=(Split-Path $run -Leaf); opened=$opened; last=$lastlog; round=$round; captcha_events=($c1-$c0)}
    $consec++; "FAILED " + $e.case + " rc=" + $rc
    if ($rc -in @(2,3,9,10,11,12,13,20)) { "STOP rc=" + $rc + " " + $e.case; break }
    if ($consec -ge 3) { "STOP consec_fail=3 at " + $e.case; break }
  }
}
"DRIVER_END " + (Get-Date -Format HH:mm:ss)
