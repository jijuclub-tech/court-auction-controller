#Requires -Version 5.1
<#
  BIDGUARD 법원경매 수집기 — 윈도우 설치 마법사  (설치 프로그램 판: 묶음의 version.json)

  사람이 하는 일: Install_Court_Auction_Collector.bat 더블클릭 → [다음] 몇 번 → [설치]
  인터넷이 없어도 설치된다 (파이썬·부품이 묶음 안에 들어 있음). 관리자 권한도 필요 없다.

  화면 순서
    1 시작   무엇을 하는지 쉬운 말로 안내
    2 점검   윈도우·디스크·묶음 지문·이전 설치·실행 중인 수집기·화면 해상도(브라우저 권장 크기)
    3 설정   설치 폴더·수집기 이름(관제형/보급형 자동 판별)·관제기 주소·바로가기·자동 실행
    4 설치   지문 확인된 파일만 설치, 자료 보관 → 파이썬 → 부품 → 프로그램 → 바로가기 → 자가점검
             중간에 하나라도 실패하면 설치 전 상태로 되돌림
    5 완료   결과, 브라우저 해상도 안내, [수집기 실행] [피코2 펌웨어 업데이트]

  지키는 것: config.json, settings.json, credentials_local.json, work_policy.json, reports, data
  이전 설치(같은 프로그램, 또는 예전 압축 풀어 쓰던 수집기 폴더)는 자료를 옮긴 뒤 정리한다.
#>
$ErrorActionPreference = 'Stop'
$script:LogBox = $null

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class BidguardNative {
  [DllImport("user32.dll")] public static extern IntPtr GetDC(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);
  [DllImport("gdi32.dll")] public static extern int GetDeviceCaps(IntPtr hdc, int nIndex);
}
"@

# ── 기본 경로 ─────────────────────────────────────────────────────
$script:InstallerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:BundleRoot   = Split-Path -Parent $script:InstallerDir
$script:Settings     = Get-Content -Raw -Encoding UTF8 (Join-Path $script:InstallerDir 'installer_settings.json') | ConvertFrom-Json
$script:Version      = Get-Content -Raw -Encoding UTF8 (Join-Path $script:BundleRoot 'version.json') | ConvertFrom-Json
$script:Stamp        = Get-Date -Format 'yyyyMMdd_HHmmss'
$script:LogFile      = Join-Path $env:TEMP "BIDGUARD_Collector_Install_$($script:Stamp).log"
$script:UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$($script:Settings.product_id)"
$script:State = @{
  Checks = @{}; OldInstall = $null; LegacyFolder = $null; KeptData = $null; Screen = $null; Blocking = $false
  Done = $false; Failed = $false; Error = ''; Record = $null
}

# ── 기록 ─────────────────────────────────────────────────────────
function Write-Log([string]$msg, [string]$level = 'INFO') {
  $line = "{0} [{1}] {2}" -f (Get-Date -Format 'HH:mm:ss'), $level, $msg
  Add-Content -Path $script:LogFile -Value $line -Encoding UTF8
  if ($script:LogBox) {
    $script:LogBox.AppendText($line + [Environment]::NewLine)
    [System.Windows.Forms.Application]::DoEvents()
  }
}

function Pump { [System.Windows.Forms.Application]::DoEvents() }

# 외부 프로그램을 돌리는 동안 화면이 멈추지 않게 기다림
function Invoke-Waiting([string]$exe, [string]$arguments, [string]$workDir = $null, [int]$timeoutSec = 900) {
  $out = Join-Path $env:TEMP "bg_out_$([guid]::NewGuid().ToString('N')).txt"
  $err = Join-Path $env:TEMP "bg_err_$([guid]::NewGuid().ToString('N')).txt"
  # 이 설치 창 뒤의 콘솔은 숨겨져 있으므로 NoNewWindow 로 돌리면 검은 창이 뜨지 않음
  $p = @{ FilePath = $exe; ArgumentList = $arguments; PassThru = $true; NoNewWindow = $true
          RedirectStandardOutput = $out; RedirectStandardError = $err }
  if ($workDir) { $p.WorkingDirectory = $workDir }
  Write-Log "실행: $exe $arguments"
  $proc = Start-Process @p
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  while (-not $proc.HasExited) {
    Pump; Start-Sleep -Milliseconds 150
    if ((Get-Date) -gt $deadline) { try { $proc.Kill() } catch {}; throw "시간 초과: $([IO.Path]::GetFileName($exe))" }
  }
  $proc.WaitForExit()
  $text = ''
  foreach ($f in @($out, $err)) { if (Test-Path $f) { $text += (Get-Content -Raw -Encoding UTF8 $f); Remove-Item $f -Force -ErrorAction SilentlyContinue } }
  if ($text) { Write-Log ($text.Trim()) 'OUT' }
  return [pscustomobject]@{ Code = $proc.ExitCode; Text = $text }
}

# ── 점검용 도우미 ─────────────────────────────────────────────────
function Get-ScreenInfo {
  # 배율을 몰라도 실제 해상도를 얻는 방법: 물리 크기(DESKTOPHORZRES) ÷ 논리 크기(HORZRES) = 배율
  $dc = [BidguardNative]::GetDC([IntPtr]::Zero)
  try {
    $logW = [BidguardNative]::GetDeviceCaps($dc, 8);   $logH = [BidguardNative]::GetDeviceCaps($dc, 10)
    $phyW = [BidguardNative]::GetDeviceCaps($dc, 118); $phyH = [BidguardNative]::GetDeviceCaps($dc, 117)
  } finally { [void][BidguardNative]::ReleaseDC([IntPtr]::Zero, $dc) }
  if ($phyW -le 0) { $phyW = $logW; $phyH = $logH }
  $scale = [math]::Round($phyW / [double]$logW, 2)
  $effW = [int][math]::Round($phyW / $scale); $effH = [int][math]::Round($phyH / $scale)
  $s = $script:Settings.screen
  $level = 'good'
  if ($effW -lt $s.recommended_width -or $effH -lt $s.recommended_height) { $level = 'fair' }
  if ($effW -lt $s.minimum_width -or $effH -lt $s.minimum_height) { $level = 'poor' }
  return [pscustomobject]@{ PhysicalWidth = $phyW; PhysicalHeight = $phyH; ScalePercent = [int]($scale * 100)
                            BrowserWidth = $effW; BrowserHeight = $effH; Level = $level }
}

function Get-ScreenGuide($si) {
  $s = $script:Settings.screen
  $nl = [Environment]::NewLine
  $t  = "수집기가 여는 브라우저 권장 화면" + $nl
  $t += "  • 해상도 $($s.recommended_width)×$($s.recommended_height), 윈도우 배율 100%" + $nl
  $t += "  • 브라우저 창은 최대화, 브라우저 확대는 100% (Ctrl 과 0 을 함께 누르면 100%)" + $nl + $nl
  $t += "이 컴퓨터: 실제 해상도 $($si.PhysicalWidth)×$($si.PhysicalHeight), 배율 $($si.ScalePercent)%" + $nl
  $t += "  → 브라우저에 실제로 보이는 크기 약 $($si.BrowserWidth)×$($si.BrowserHeight)" + $nl + $nl
  switch ($si.Level) {
    'good' { $t += "판정: 좋음. 법원경매 화면 전체가 브라우저 한 화면에 들어옵니다." }
    'fair' { $t += "판정: 보통. 대부분 들어오지만 화면 아래·오른쪽이 잘릴 수 있습니다." }
    'poor' { $t += "판정: 문제. 화면이 작아 페이지가 한 화면에 들어오지 않을 가능성이 큽니다." }
  }
  $t += $nl + $nl + "왜 중요한가요?" + $nl
  $t += "  수집기는 화면을 보고 일합니다. 페이지 전체가 브라우저 한 화면에 들어와야" + $nl
  $t += "  단추와 글자를 잘 알아봅니다. 잘리거나 스크롤이 생기면 알아보는 힘이 떨어집니다." + $nl + $nl
  $t += "문제가 있으면 이렇게 하십시오" + $nl
  $t += "  ① [디스플레이 설정 열기] → '배율'을 100%로 바꿉니다." + $nl
  $t += "  ② 그래도 잘리면 브라우저에서 Ctrl 과 - 를 눌러 90%, 80% 로 줄여" + $nl
  $t += "     페이지 전체가 한 화면에 들어오게 합니다. (되돌리기: Ctrl 과 0)" + $nl
  $t += "  ③ 해상도를 바꿀 수 있으면 $($s.recommended_width)×$($s.recommended_height) 로 맞춥니다."
  return $t
}

function Get-FreeMB([string]$path) {
  $root = [IO.Path]::GetPathRoot($path)
  $d = New-Object IO.DriveInfo($root)
  return [int]($d.AvailableFreeSpace / 1MB)
}

function Test-BundleManifest {
  $manifestPath = Join-Path $script:BundleRoot 'BUILD-MANIFEST.json'
  if (-not (Test-Path $manifestPath)) { throw "설치 묶음의 지문 목록(BUILD-MANIFEST.json)이 없습니다. 묶음을 다시 받으십시오." }
  $m = Get-Content -Raw -Encoding UTF8 $manifestPath | ConvertFrom-Json
  $names = @($m.files.PSObject.Properties.Name)
  $i = 0
  foreach ($rel in $names) {
    $i++
    if ($i % 20 -eq 0) { Pump }
    $full = Join-Path $script:BundleRoot ($rel -replace '/', '\')
    if (-not (Test-Path -LiteralPath $full)) { throw "설치 묶음에 파일이 빠졌습니다: $rel  (압축을 끝까지 풀었는지 확인하십시오)" }
    $h = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLower()
    if ($h -ne $m.files.$rel) { throw "설치 묶음 파일이 바뀌었거나 망가졌습니다: $rel  (묶음을 다시 받으십시오)" }
  }
  return $names.Count
}

function Find-ExistingInstall {
  if (Test-Path $script:UninstallKey) {
    $k = Get-ItemProperty $script:UninstallKey
    $loc = $k.PSObject.Properties['InstallLocation']; $ver = $k.PSObject.Properties['DisplayVersion']
    if ($loc -and $loc.Value -and (Test-Path $loc.Value)) {
      return [pscustomobject]@{ Path = $loc.Value; Version = $(if ($ver) { $ver.Value } else { '?' }) }
    }
  }
  $def = $script:Settings.default_install_dir
  $rec = Join-Path $def 'install_record.json'
  if (Test-Path $rec) {
    $r = Get-Content -Raw -Encoding UTF8 $rec | ConvertFrom-Json
    return [pscustomobject]@{ Path = $def; Version = $r.version }
  }
  return $null
}

# 예전 방식(압축 풀고 install.bat → 바탕화면 Collector_이름.lnk)으로 쓰던 수집기 폴더 찾기
function Find-LegacyFolder {
  $desk = [Environment]::GetFolderPath('Desktop')
  $sh = New-Object -ComObject WScript.Shell
  foreach ($lnk in @(Get-ChildItem -Path $desk -Filter 'Collector_*.lnk' -ErrorAction SilentlyContinue)) {
    try {
      $target = $sh.CreateShortcut($lnk.FullName).TargetPath
      $folder = Split-Path -Parent $target
      if ((Test-Path (Join-Path $folder 'ui\app.py')) -and (Test-Path (Join-Path $folder 'run.bat'))) {
        return [pscustomobject]@{ Path = $folder; Shortcut = $lnk.FullName }
      }
    } catch {}
  }
  return $null
}

# 예전에 지우기 할 때 옮겨 둔 자료 (Court_Auction_Collector_Data_날짜)
function Find-KeptData {
  $parent = Split-Path -Parent $script:Settings.default_install_dir
  if (-not (Test-Path $parent)) { return $null }
  $d = Get-ChildItem -LiteralPath $parent -Directory -Filter 'Court_Auction_Collector_Data_*' -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
  if ($d) { return $d.FullName } else { return $null }
}

function Get-RunningCollectors {
  $list = @()
  try {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'"
    foreach ($p in $procs) {
      if ($p.CommandLine -and ($p.CommandLine -match 'ui\.app' -or $p.CommandLine -match 'pico2_firmware_updater')) { $list += $p }
    }
  } catch {}
  return $list
}

function Get-PythonCandidate {
  $minor = $script:Settings.python.minor
  foreach ($hive in @('HKCU:', 'HKLM:')) {
    $key = "$hive\Software\Python\PythonCore\$minor\InstallPath"
    if (Test-Path $key) {
      $props = Get-ItemProperty $key
      $exe = $null
      if ($props.PSObject.Properties['ExecutablePath']) { $exe = $props.ExecutablePath }
      if (-not $exe -and $props.PSObject.Properties['(default)']) { $exe = Join-Path $props.'(default)' 'python.exe' }
      if ($exe -and (Test-Path $exe)) { return $exe }
    }
  }
  return $null
}

function Test-InstallPath([string]$p) {
  if (-not $p) { return '설치 폴더를 정하십시오.' }
  if ($p -notmatch '^[A-Za-z]:\\[A-Za-z0-9_\-\.\\]+$') { return '설치 폴더 이름은 영어·숫자·밑줄(_)·빼기(-)만 쓰십시오. 한글·띄어쓰기는 안 됩니다. 예) C:\BIDGUARD\Court_Auction_Collector' }
  $low = $p.ToLower()
  foreach ($bad in @('\downloads', '\desktop', '\onedrive', 'program files', '\windows')) {
    if ($low.Contains($bad)) { return "이 자리에는 설치할 수 없습니다 ($bad). 내려받기·바탕화면·OneDrive 폴더는 윈도우가 정리하거나 옮길 수 있습니다." }
  }
  if ($p.TrimEnd('\').Length -le 3) { return '드라이브 맨 위(C:\)가 아닌 폴더를 정하십시오.' }
  return ''
}

function Get-CollectorType([string]$name) {
  if ($name -match '^(?i)jiju\d{4}$') { return 'control' } else { return 'supply' }
}

# ── 설치 본체 ─────────────────────────────────────────────────────
function Copy-Tree([string]$src, [string]$dst) {
  New-Item -ItemType Directory -Force -Path $dst | Out-Null
  $items = Get-ChildItem -LiteralPath $src -Recurse -Force
  $n = 0
  foreach ($it in $items) {
    $rel = $it.FullName.Substring($src.TrimEnd('\').Length).TrimStart('\')
    $target = Join-Path $dst $rel
    if ($it.PSIsContainer) { New-Item -ItemType Directory -Force -Path $target | Out-Null }
    else {
      Copy-Item -LiteralPath $it.FullName -Destination $target -Force
      $n++; if ($n % 25 -eq 0) { Pump }
    }
  }
}

function Save-PreservedData([string]$appDir, [string]$toDir) {
  $saved = @()
  if (-not $appDir -or -not (Test-Path $appDir)) { return $saved }
  New-Item -ItemType Directory -Force -Path $toDir | Out-Null
  foreach ($name in $script:Settings.preserve_in_app) {
    $src = Join-Path $appDir $name
    if (Test-Path -LiteralPath $src) {
      if ((Get-Item -LiteralPath $src).PSIsContainer) { Copy-Tree $src (Join-Path $toDir $name) }
      else { Copy-Item -LiteralPath $src -Destination (Join-Path $toDir $name) -Force }
      $saved += $name
    }
  }
  return $saved
}

function New-Shortcut([string]$path, [string]$target, [string]$workDir, [string]$icon, [string]$desc) {
  $sh = New-Object -ComObject WScript.Shell
  $s = $sh.CreateShortcut($path)
  $s.TargetPath = $target; $s.WorkingDirectory = $workDir; $s.Description = $desc
  if ($icon -and (Test-Path $icon)) { $s.IconLocation = $icon }
  $s.Save()
}

function Set-Step([int]$i, [string]$state) {
  $marks = @{ 'run' = '▶'; 'ok' = '✔'; 'fail' = '✖'; 'wait' = '·' }
  $lbl = $script:StepLabels[$i]
  $lbl.Text = "$($marks[$state])  $($script:StepNames[$i])"
  switch ($state) { 'ok' { $lbl.ForeColor = [Drawing.Color]::FromArgb(17, 98, 58) } 'fail' { $lbl.ForeColor = [Drawing.Color]::FromArgb(161, 42, 42) } 'run' { $lbl.ForeColor = [Drawing.Color]::FromArgb(29, 79, 145) } default { $lbl.ForeColor = [Drawing.Color]::Gray } }
  $script:Progress.Value = [math]::Min(100, [int](($i + ($(if ($state -eq 'ok') { 1 } else { 0 }))) * 100 / $script:StepNames.Count))
  Pump
}

function Invoke-Install($opt) {
  $dir = $opt.InstallDir
  $app = Join-Path $dir 'app'; $tools = Join-Path $dir 'tools'; $runtime = Join-Path $dir 'runtime'
  $venv = Join-Path $runtime 'venv'
  $backup = Join-Path $dir "_backup_$($script:Stamp)"
  $undo = New-Object System.Collections.ArrayList   # 실패하면 거꾸로 되돌릴 일들
  $pythonOwner = 'none'; $pythonInstalledNow = $false
  $step = 0
  try {
    # 0 준비
    Set-Step $step 'run'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $probe = Join-Path $dir '_write_test.tmp'; Set-Content -Path $probe -Value 'ok'; Remove-Item $probe -Force
    if ((Get-FreeMB $dir) -lt $script:Settings.minimum_free_disk_mb) { throw "디스크 여유 공간이 부족합니다. $($script:Settings.minimum_free_disk_mb)MB 이상 비워 주십시오." }
    foreach ($p in @(Get-RunningCollectors)) { Write-Log "실행 중인 수집기 종료: PID $($p.ProcessId)"; Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    Set-Step $step 'ok'; $step++

    # 1 자료 보관
    Set-Step $step 'run'
    $dataSaved = Join-Path $backup 'preserved'
    $sourceApp = $null
    if (Test-Path (Join-Path $app 'ui\app.py')) { $sourceApp = $app }
    elseif ($opt.LegacyFolder) { $sourceApp = $opt.LegacyFolder.Path }
    elseif ($opt.KeptData) { $sourceApp = $opt.KeptData }
    $saved = Save-PreservedData $sourceApp $dataSaved
    if ($saved.Count) { Write-Log "보관한 자료: $($saved -join ', ') (출처 $sourceApp)" } else { Write-Log '보관할 이전 자료 없음' }
    Set-Step $step 'ok'; $step++

    # 2 파이썬 (화면 부품 tkinter 포함)
    Set-Step $step 'run'
    $own = Join-Path $runtime 'python\python.exe'
    $base = $null
    if (Test-Path $own) { $base = $own; $pythonOwner = 'bundled'; Write-Log '이전에 설치한 전용 파이썬을 그대로 씀' }
    else {
      $existing = Get-PythonCandidate
      if ($existing) {
        $r = Invoke-Waiting $existing '-c "import tkinter,sys;print(sys.version)"'
        if ($r.Code -eq 0) { $base = $existing; $pythonOwner = 'existing'; Write-Log "이 PC에 이미 있는 파이썬 $($script:Settings.python.minor) 사용: $existing" }
      }
    }
    if (-not $base) {
      $pyExe = Join-Path $script:BundleRoot ("prerequisites\" + $script:Settings.python.installer_file)
      $target = Join-Path $runtime 'python'
      $pyArgs = "/quiet InstallAllUsers=0 TargetDir=`"$target`" Include_launcher=0 InstallLauncherAllUsers=0 Include_test=0 Include_doc=0 Include_dev=0 Include_tcltk=1 Include_pip=1 PrependPath=0 Shortcuts=0 AssociateFiles=0 /log `"$(Join-Path $env:TEMP "BIDGUARD_python_$($script:Stamp).log")`""
      Write-Log "파이썬 $($script:Settings.python.version) 설치 (이 프로그램 전용, 1~3분)"
      $r = Invoke-Waiting $pyExe $pyArgs
      if ($r.Code -ne 0) { throw "파이썬 설치가 끝나지 않았습니다 (코드 $($r.Code)). 기록 파일을 관리자에게 보내 주십시오." }
      $pythonInstalledNow = $true
      [void]$undo.Add(@{ Kind = 'uninstall_python'; Exe = $pyExe })
      if (Test-Path $own) { $base = $own; $pythonOwner = 'bundled' }
      else {
        # 같은 3.12가 이미 등록돼 있으면 설치 프로그램이 그쪽을 고쳐 설치함 → 등록된 경로를 다시 찾음
        $base = Get-PythonCandidate; $pythonOwner = 'existing'
      }
      if (-not $base) { throw '파이썬을 설치했지만 실행 파일을 찾지 못했습니다.' }
      New-Item -ItemType Directory -Force -Path (Join-Path $runtime 'python_installer') | Out-Null
      Copy-Item $pyExe (Join-Path $runtime 'python_installer') -Force   # 나중에 깨끗이 지우는 데 필요
    }
    $r = Invoke-Waiting $base '-c "import tkinter,sys;print(sys.version)"'
    if ($r.Code -ne 0) { throw '파이썬에 화면 부품(tkinter)이 없습니다.' }
    Set-Step $step 'ok'; $step++

    # 3 부품 (인터넷 없이 묶음 안의 부품으로)
    Set-Step $step 'run'
    $venvNew = Join-Path $runtime 'venv_new'
    if (Test-Path $venvNew) { Remove-Item $venvNew -Recurse -Force }
    $r = Invoke-Waiting $base "-m venv `"$venvNew`""
    if ($r.Code -ne 0) { throw '수집기 전용 실행 공간(venv)을 만들지 못했습니다.' }
    $vpy = Join-Path $venvNew 'Scripts\python.exe'
    $wheels = Join-Path $script:BundleRoot 'prerequisites\wheels'
    $req = Join-Path $script:BundleRoot ('prerequisites\' + $script:Settings.requirements_file)
    $r = Invoke-Waiting $vpy "-m pip install --no-index --find-links `"$wheels`" -r `"$req`" --disable-pip-version-check --no-warn-script-location"
    if ($r.Code -ne 0) { throw '부품 설치에 실패했습니다.' }
    $r = Invoke-Waiting $vpy "-c `"import tkinter, requests, dotenv, serial; print('PARTS OK')`""
    if ($r.Code -ne 0) { throw '부품 확인에 실패했습니다.' }
    Set-Step $step 'ok'; $step++

    # 4 프로그램 복사 (새 판을 옆에 다 만든 뒤 한 번에 바꿔 끼움)
    Set-Step $step 'run'
    $appNew = Join-Path $dir 'app_new'; $toolsNew = Join-Path $dir 'tools_new'
    foreach ($d in @($appNew, $toolsNew)) { if (Test-Path $d) { Remove-Item $d -Recurse -Force } }
    Copy-Tree (Join-Path $script:BundleRoot 'payload\collector') $appNew
    Copy-Tree (Join-Path $script:BundleRoot 'payload\firmware_updater') $toolsNew
    Copy-Tree (Join-Path $script:BundleRoot 'payload\firmware') (Join-Path $toolsNew 'firmware')
    Copy-Tree $script:InstallerDir (Join-Path $dir 'installer_new')
    Copy-Item (Join-Path $script:BundleRoot 'version.json') (Join-Path $dir 'installer_new') -Force
    # 바꿔 끼우기
    foreach ($pair in @(@($app, $appNew, 'app'), @($tools, $toolsNew, 'tools'), @($venv, $venvNew, 'venv'), @((Join-Path $dir 'installer'), (Join-Path $dir 'installer_new'), 'installer'))) {
      $cur = $pair[0]; $new = $pair[1]; $tag = $pair[2]
      if (Test-Path $cur) { Move-Item $cur (Join-Path $backup "old_$tag"); [void]$undo.Add(@{ Kind = 'restore'; From = (Join-Path $backup "old_$tag"); To = $cur }) }
      else { [void]$undo.Add(@{ Kind = 'remove'; Path = $cur }) }
      Move-Item $new $cur
    }
    # 지킨 자료 되돌려 놓기
    foreach ($name in $saved) { $src = Join-Path $dataSaved $name; $dst = Join-Path $app $name
      if ((Get-Item -LiteralPath $src).PSIsContainer) { Copy-Tree $src $dst } else { Copy-Item -LiteralPath $src -Destination $dst -Force } }
    # 수집기 이름·유형, 관제기 주소
    $cfgPath = Join-Path $app 'config.json'
    $cfg = [ordered]@{}
    if (Test-Path $cfgPath) { (Get-Content -Raw -Encoding UTF8 $cfgPath | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $cfg[$_.Name] = $_.Value } }
    $cfg['name'] = $opt.CollectorName; $cfg['type'] = (Get-CollectorType $opt.CollectorName)
    if (-not $cfg.Contains('created')) { $cfg['created'] = (Get-Date).ToString('s') }
    if (-not $cfg.Contains('ai_accounts')) { $cfg['ai_accounts'] = @() }
    if (-not $cfg.Contains('pico2_enabled')) { $cfg['pico2_enabled'] = $true }
    [IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
    if ($opt.ControllerUrl) {
      $setPath = Join-Path $app 'settings.json'; $set = [ordered]@{}
      if (Test-Path $setPath) { (Get-Content -Raw -Encoding UTF8 $setPath | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $set[$_.Name] = $_.Value } }
      $set['controller_url'] = $opt.ControllerUrl
      [IO.File]::WriteAllText($setPath, ($set | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
    }
    Set-Step $step 'ok'; $step++

    # 5 실행 파일·바로가기·윈도우 등록
    Set-Step $step 'run'
    $ascii = New-Object Text.ASCIIEncoding
    [IO.File]::WriteAllText((Join-Path $dir 'Run_Court_Auction_Collector.bat'), "@echo off`r`ncd /d `"%~dp0app`"`r`n`"%~dp0runtime\venv\Scripts\python.exe`" -m ui.app`r`nif errorlevel 1 pause`r`n", $ascii)
    [IO.File]::WriteAllText((Join-Path $dir 'Update_Pico2_Firmware.bat'), "@echo off`r`ncd /d `"%~dp0tools`"`r`nstart `"`" `"%~dp0runtime\venv\Scripts\pythonw.exe`" pico2_firmware_updater.py`r`n", $ascii)
    [IO.File]::WriteAllText((Join-Path $dir 'Uninstall_Court_Auction_Collector.bat'), "@echo off`r`ncopy /y `"%~dp0installer\Court_Auction_Collector_Uninstaller.ps1`" `"%TEMP%\BIDGUARD_Collector_Uninstaller.ps1`" >nul`r`nstart `"`" powershell -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File `"%TEMP%\BIDGUARD_Collector_Uninstaller.ps1`" -InstallDir `"%~dp0.`"`r`n", $ascii)
    $icon = Join-Path $app 'jiju.ico'
    $shortcuts = @()
    $runBat = Join-Path $dir 'Run_Court_Auction_Collector.bat'
    $desc = "$($script:Settings.product_name) ($($opt.CollectorName))"
    if ($opt.Desktop) { $p = Join-Path ([Environment]::GetFolderPath('Desktop')) "$($script:Settings.shortcut_name).lnk"; New-Shortcut $p $runBat $app $icon $desc; $shortcuts += $p }
    if ($opt.StartMenu) {
      $sm = Join-Path ([Environment]::GetFolderPath('Programs')) 'BIDGUARD'
      New-Item -ItemType Directory -Force -Path $sm | Out-Null
      $p = Join-Path $sm "$($script:Settings.shortcut_name).lnk"; New-Shortcut $p $runBat $app $icon $desc; $shortcuts += $p
      $p = Join-Path $sm "$($script:Settings.firmware_shortcut_name).lnk"; New-Shortcut $p (Join-Path $dir 'Update_Pico2_Firmware.bat') $tools $icon '피코2 펌웨어 업데이트'; $shortcuts += $p
      $p = Join-Path $sm "$($script:Settings.uninstall_shortcut_name).lnk"; New-Shortcut $p (Join-Path $dir 'Uninstall_Court_Auction_Collector.bat') $dir $icon '수집기 지우기'; $shortcuts += $p
    }
    if ($opt.AutoStart) { $p = Join-Path ([Environment]::GetFolderPath('Startup')) "$($script:Settings.shortcut_name).lnk"; New-Shortcut $p $runBat $app $icon "$desc 자동 실행"; $shortcuts += $p }
    foreach ($p in $shortcuts) { [void]$undo.Add(@{ Kind = 'remove'; Path = $p }) }
    # 윈도우 '설정 > 앱'에 등록 → 거기서도 지울 수 있게
    $hadKey = Test-Path $script:UninstallKey
    New-Item -Path $script:UninstallKey -Force | Out-Null
    Set-ItemProperty $script:UninstallKey DisplayName "$($script:Settings.product_name) ($($opt.CollectorName))"
    Set-ItemProperty $script:UninstallKey DisplayVersion $script:Version.label
    Set-ItemProperty $script:UninstallKey Publisher $script:Settings.publisher
    Set-ItemProperty $script:UninstallKey InstallLocation $dir
    Set-ItemProperty $script:UninstallKey DisplayIcon $icon
    Set-ItemProperty $script:UninstallKey UninstallString "`"$(Join-Path $dir 'Uninstall_Court_Auction_Collector.bat')`""
    Set-ItemProperty $script:UninstallKey NoModify 1 -Type DWord
    Set-ItemProperty $script:UninstallKey NoRepair 1 -Type DWord
    if (-not $hadKey) { [void]$undo.Add(@{ Kind = 'remove_key' }) }
    Set-Step $step 'ok'; $step++

    # 6 자가점검 (설치한 것을 실제로 켜 봄)
    Set-Step $step 'run'
    $vpy = Join-Path $venv 'Scripts\python.exe'
    $r = Invoke-Waiting $vpy ("-c `"" + $script:Settings.self_check_imports + "; print('SELFCHECK OK')`"") $app
    if ($r.Code -ne 0 -or $r.Text -notmatch 'SELFCHECK OK') { throw '설치한 수집기가 켜지지 않습니다 (자가점검 실패).' }
    $r = Invoke-Waiting $vpy 'pico2_firmware_updater.py --check' $tools 120
    if ($r.Text -match '지문이 기록과 다릅니다|펌웨어 파일이 없습니다') { throw '펌웨어 파일 점검에 실패했습니다.' }
    if ($opt.ControllerUrl) {
      try { $resp = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$($opt.ControllerUrl)/login"; Write-Log "관제기 연결 확인: HTTP $($resp.StatusCode)" }
      catch { Write-Log "관제기에 지금은 연결되지 않습니다 (설치는 계속). 나중에 수집기 Settings 탭에서 연결하십시오." 'WARN' }
    }
    Set-Step $step 'ok'; $step++

    # 7 정리 (성공했을 때만 옛것을 지움)
    Set-Step $step 'run'
    $record = [ordered]@{
      product = $script:Settings.product_id; version = $script:Version.label; installed_at = (Get-Date).ToString('s')
      install_dir = $dir; collector_name = $opt.CollectorName; collector_type = (Get-CollectorType $opt.CollectorName)
      controller_url = $opt.ControllerUrl; python_owner = $pythonOwner; python_base = $base
      python_installed_by_this_setup = ($pythonOwner -eq 'bundled'); shortcuts = $shortcuts; preserved = $saved
      source_commit = $script:Version.source_commit
    }
    [IO.File]::WriteAllText((Join-Path $dir 'install_record.json'), ($record | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
    Remove-Item $backup -Recurse -Force -ErrorAction SilentlyContinue
    if ($opt.KeptData) {
      $fwRec = Join-Path $opt.KeptData 'firmware_install_record.json'
      if (Test-Path $fwRec) { Copy-Item $fwRec $dir -Force }
      Remove-Item -LiteralPath $opt.KeptData -Recurse -Force -ErrorAction SilentlyContinue
      Write-Log "옮겨 둔 자료를 되살리고 그 폴더를 정리: $($opt.KeptData)"
    }
    if ($opt.LegacyFolder -and $opt.CleanLegacy) {
      $lp = $opt.LegacyFolder.Path
      if ((Test-Path (Join-Path $lp 'ui\app.py')) -and -not ($lp.ToLower().StartsWith($dir.ToLower()))) {
        Remove-Item -LiteralPath $lp -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $opt.LegacyFolder.Shortcut -Force -ErrorAction SilentlyContinue
        Write-Log "예전 수집기 폴더 정리: $lp (자료는 새 설치로 옮김)"
      }
    }
    $logs = Join-Path $dir 'logs'; New-Item -ItemType Directory -Force -Path $logs | Out-Null
    Set-Step $step 'ok'
    $script:Progress.Value = 100
    Write-Log '설치 완료'
    Copy-Item $script:LogFile $logs -Force
    $script:State.Record = $record
    return $true
  }
  catch {
    $msg = $_.Exception.Message
    Write-Log "실패: $msg" 'ERROR'
    if ($step -lt $script:StepNames.Count) { Set-Step $step 'fail' }
    Write-Log '설치 전 상태로 되돌리는 중...'
    for ($i = $undo.Count - 1; $i -ge 0; $i--) {
      $u = $undo[$i]
      try {
        switch ($u.Kind) {
          'remove'  { if (Test-Path -LiteralPath $u.Path) { Remove-Item -LiteralPath $u.Path -Recurse -Force } }
          'restore' { if (Test-Path -LiteralPath $u.To) { Remove-Item -LiteralPath $u.To -Recurse -Force }; Move-Item -LiteralPath $u.From -Destination $u.To }
          'remove_key' { Remove-Item $script:UninstallKey -Recurse -Force -ErrorAction SilentlyContinue }
          'uninstall_python' { if ($pythonOwner -eq 'bundled') { Invoke-Waiting $u.Exe '/quiet /uninstall' | Out-Null } }
        }
      } catch { Write-Log "되돌리기 중 문제: $($_.Exception.Message)" 'WARN' }
    }
    foreach ($d in @('app_new', 'tools_new', 'installer_new', 'runtime\venv_new')) { $p = Join-Path $dir $d; if (Test-Path $p) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue } }
    if ($pythonInstalledNow -and $pythonOwner -eq 'bundled') { $pp = Join-Path $runtime 'python'; if (Test-Path $pp) { Remove-Item $pp -Recurse -Force -ErrorAction SilentlyContinue } }
    if ((Test-Path $backup) -and -not (Get-ChildItem $backup -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notlike '*\preserved\*' })) { Remove-Item $backup -Recurse -Force -ErrorAction SilentlyContinue }
    Write-Log '되돌리기 끝. 이전 상태가 그대로 남아 있습니다.'
    $script:State.Error = $msg
    return $false
  }
}

# ── 화면 ─────────────────────────────────────────────────────────
$green = [Drawing.Color]::FromArgb(15, 92, 55); $soft = [Drawing.Color]::FromArgb(243, 246, 244)
$fontBody = New-Object Drawing.Font('Malgun Gothic', 10)
$fontBold = New-Object Drawing.Font('Malgun Gothic', 10, [Drawing.FontStyle]::Bold)
$fontTitle = New-Object Drawing.Font('Malgun Gothic', 15, [Drawing.FontStyle]::Bold)

$form = New-Object Windows.Forms.Form
$form.Text = "$($script:Settings.product_name) 설치  ($($script:Version.label))"
$form.Size = New-Object Drawing.Size(820, 640); $form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'; $form.MaximizeBox = $false; $form.Font = $fontBody; $form.BackColor = [Drawing.Color]::White
$iconPath = Join-Path $script:BundleRoot 'payload\collector\jiju.ico'
if (Test-Path $iconPath) { $form.Icon = New-Object Drawing.Icon($iconPath) }

$header = New-Object Windows.Forms.Panel; $header.Dock = 'Top'; $header.Height = 78; $header.BackColor = $green
$title = New-Object Windows.Forms.Label; $title.Text = $script:Settings.product_name; $title.Font = $fontTitle; $title.ForeColor = [Drawing.Color]::White
$title.Location = New-Object Drawing.Point(22, 12); $title.AutoSize = $true
$crumbs = New-Object Windows.Forms.Label; $crumbs.ForeColor = [Drawing.Color]::FromArgb(207, 232, 218); $crumbs.Location = New-Object Drawing.Point(24, 48); $crumbs.AutoSize = $true
$header.Controls.AddRange(@($title, $crumbs))

$footer = New-Object Windows.Forms.Panel; $footer.Dock = 'Bottom'; $footer.Height = 62; $footer.BackColor = $soft
$btnBack = New-Object Windows.Forms.Button; $btnBack.Text = '이전'; $btnBack.Size = New-Object Drawing.Size(100, 36); $btnBack.Location = New-Object Drawing.Point(452, 13)
$btnNext = New-Object Windows.Forms.Button; $btnNext.Text = '다음'; $btnNext.Size = New-Object Drawing.Size(130, 36); $btnNext.Location = New-Object Drawing.Point(560, 13)
$btnNext.BackColor = $green; $btnNext.ForeColor = [Drawing.Color]::White; $btnNext.FlatStyle = 'Flat'; $btnNext.Font = $fontBold
$btnCancel = New-Object Windows.Forms.Button; $btnCancel.Text = '취소'; $btnCancel.Size = New-Object Drawing.Size(90, 36); $btnCancel.Location = New-Object Drawing.Point(700, 13)
$footer.Controls.AddRange(@($btnBack, $btnNext, $btnCancel))

$body = New-Object Windows.Forms.Panel; $body.Dock = 'Fill'; $body.Padding = New-Object Windows.Forms.Padding(24, 16, 24, 8)
$form.Controls.AddRange(@($body, $footer, $header))

function New-Label([string]$text, [int]$x, [int]$y, [int]$w = 740, [int]$h = 24, $font = $fontBody) {
  $l = New-Object Windows.Forms.Label; $l.Text = $text; $l.Location = New-Object Drawing.Point($x, $y); $l.Size = New-Object Drawing.Size($w, $h); $l.Font = $font; return $l
}

$pageNames = @('시작', '점검', '설정', '설치', '완료')
$script:Page = 0
$pages = @()
for ($i = 0; $i -lt 5; $i++) { $p = New-Object Windows.Forms.Panel; $p.Dock = 'Fill'; $p.Visible = $false; $pages += $p; $body.Controls.Add($p) }

# 1 시작
$nl = [Environment]::NewLine
$welcome = "이 프로그램은 법원경매 수집기를 이 컴퓨터에 설치합니다.$nl$nl" +
  "  • 인터넷이 없어도 설치됩니다. 필요한 파이썬과 부품이 묶음 안에 모두 들어 있습니다.$nl" +
  "  • 관리자 권한이 필요 없습니다.$nl" +
  "  • 예전에 쓰던 수집기가 있으면 이름·설정·모은 자료를 그대로 옮기고, 예전 폴더는 정리합니다.$nl" +
  "  • 설치 중 문제가 생기면 설치 전 상태로 자동으로 되돌립니다.$nl" +
  "  • 다 끝나면 바탕화면 아이콘으로 수집기를 켤 수 있고, 피코2 펌웨어도 여기서 업데이트합니다.$nl$nl" +
  "걸리는 시간: 보통 2~5분$nl$nl" +
  "[다음]을 누르면 이 컴퓨터를 먼저 점검합니다. 아직 아무것도 바뀌지 않습니다."
$pages[0].Controls.Add((New-Label $welcome 0 0 740 360))
$verLbl = New-Label ("설치 묶음 판: $($script:Version.label)    만든 날: $($script:Version.built_at)") 0 380 740 24
$verLbl.ForeColor = [Drawing.Color]::Gray; $pages[0].Controls.Add($verLbl)

# 2 점검
$checkList = New-Object Windows.Forms.ListView; $checkList.View = 'Details'; $checkList.FullRowSelect = $true; $checkList.HeaderStyle = 'Nonclickable'
$checkList.Location = New-Object Drawing.Point(0, 0); $checkList.Size = New-Object Drawing.Size(760, 170)
[void]$checkList.Columns.Add('', 34); [void]$checkList.Columns.Add('항목', 170); [void]$checkList.Columns.Add('결과', 540)
$screenBox = New-Object Windows.Forms.TextBox; $screenBox.Multiline = $true; $screenBox.ReadOnly = $true; $screenBox.ScrollBars = 'Vertical'
$screenBox.Location = New-Object Drawing.Point(0, 180); $screenBox.Size = New-Object Drawing.Size(760, 200); $screenBox.BackColor = $soft
$btnDisplay = New-Object Windows.Forms.Button; $btnDisplay.Text = '디스플레이 설정 열기'; $btnDisplay.Size = New-Object Drawing.Size(170, 32); $btnDisplay.Location = New-Object Drawing.Point(0, 388)
$btnDisplay.Add_Click({ Start-Process 'ms-settings:display' })
$btnRecheck = New-Object Windows.Forms.Button; $btnRecheck.Text = '다시 점검'; $btnRecheck.Size = New-Object Drawing.Size(110, 32); $btnRecheck.Location = New-Object Drawing.Point(178, 388)
$btnStopCollector = New-Object Windows.Forms.Button; $btnStopCollector.Text = '실행 중인 수집기 닫기'; $btnStopCollector.Size = New-Object Drawing.Size(170, 32); $btnStopCollector.Location = New-Object Drawing.Point(296, 388); $btnStopCollector.Visible = $false
$pages[1].Controls.AddRange(@($checkList, $screenBox, $btnDisplay, $btnRecheck, $btnStopCollector))

function Add-Check([string]$name, [string]$level, [string]$text) {
  $mark = @{ ok = '✔'; warn = '⚠'; fail = '✖'; info = 'ℹ' }[$level]
  $item = New-Object Windows.Forms.ListViewItem($mark); [void]$item.SubItems.Add($name); [void]$item.SubItems.Add($text)
  switch ($level) { 'ok' { $item.ForeColor = [Drawing.Color]::FromArgb(17, 98, 58) } 'warn' { $item.ForeColor = [Drawing.Color]::FromArgb(154, 91, 0) } 'fail' { $item.ForeColor = [Drawing.Color]::FromArgb(161, 42, 42) } }
  [void]$checkList.Items.Add($item); Pump
  if ($level -eq 'fail') { $script:State.Blocking = $true }
}

function Invoke-Checks {
  $checkList.Items.Clear(); $script:State.Blocking = $false; $btnNext.Enabled = $false
  $os = [Environment]::OSVersion.Version
  if ($os.Major -ge 10 -and [Environment]::Is64BitOperatingSystem) { Add-Check '윈도우' 'ok' "Windows $($os.Major) 64비트 (빌드 $($os.Build))" }
  else { Add-Check '윈도우' 'fail' '윈도우 10 이상 64비트에서만 설치됩니다.' }
  $free = Get-FreeMB $script:Settings.default_install_dir
  if ($free -ge $script:Settings.minimum_free_disk_mb) { Add-Check '디스크 여유' 'ok' "$([IO.Path]::GetPathRoot($script:Settings.default_install_dir)) $([int]($free/1024)) GB 비어 있음" }
  else { Add-Check '디스크 여유' 'warn' "C: 여유 $free MB — 다른 드라이브를 설치 폴더로 정하십시오." }
  try { $n = Test-BundleManifest; Add-Check '설치 묶음' 'ok' "파일 $n 개 지문 확인 (망가지거나 바뀐 파일 없음)" }
  catch { Add-Check '설치 묶음' 'fail' $_.Exception.Message }
  $script:State.OldInstall = Find-ExistingInstall
  $script:State.LegacyFolder = Find-LegacyFolder
  if ($script:State.OldInstall) { Add-Check '이전 설치' 'info' "$($script:State.OldInstall.Version) 이(가) $($script:State.OldInstall.Path) 에 있음 → 자료를 지키고 새 판으로 바꿉니다." }
  elseif ($script:State.LegacyFolder) { Add-Check '예전 수집기' 'info' "$($script:State.LegacyFolder.Path) → 이름·설정·자료를 옮기고 정리할 수 있습니다 (다음 화면에서 선택)." }
  else {
    $script:State.KeptData = Find-KeptData
    if ($script:State.KeptData) { Add-Check '옮겨 둔 자료' 'info' "$($script:State.KeptData) → 이전에 지울 때 옮겨 둔 자료를 되살릴 수 있습니다 (다음 화면)." }
    else { Add-Check '이전 설치' 'ok' '없음 (새로 설치)' }
  }
  $run = @(Get-RunningCollectors)
  if ($run.Count) { Add-Check '실행 중인 수집기' 'warn' "$($run.Count)개 실행 중 → 설치할 때 자동으로 닫습니다. 하던 일을 먼저 멈추십시오."; $btnStopCollector.Visible = $true }
  else { Add-Check '실행 중인 수집기' 'ok' '없음'; $btnStopCollector.Visible = $false }
  $py = Get-PythonCandidate
  if ($script:State.OldInstall -and (Test-Path (Join-Path $script:State.OldInstall.Path 'runtime\python\python.exe'))) { Add-Check '파이썬' 'ok' '이전에 설치한 전용 파이썬을 그대로 씁니다.' }
  elseif ($py) { Add-Check '파이썬' 'ok' "이 PC의 파이썬 $($script:Settings.python.minor) 을(를) 씁니다: $py" }
  else { Add-Check '파이썬' 'info' "없음 → 묶음 안의 파이썬 $($script:Settings.python.version) 을(를) 이 프로그램 전용으로 설치합니다." }
  $si = Get-ScreenInfo
  $lvl = @{ good = 'ok'; fair = 'warn'; poor = 'warn' }[$si.Level]
  $word = @{ good = '좋음'; fair = '보통 — 아래 안내 참고'; poor = '문제 — 아래 안내대로 맞춰 주십시오' }[$si.Level]
  Add-Check '화면(브라우저)' $lvl "브라우저 보이는 크기 약 $($si.BrowserWidth)×$($si.BrowserHeight) (배율 $($si.ScalePercent)%) : $word"
  $screenBox.Text = Get-ScreenGuide $si
  $script:State.Screen = $si
  $btnNext.Enabled = -not $script:State.Blocking
  if ($script:State.Blocking) { [void][Windows.Forms.MessageBox]::Show('✖ 표시된 문제를 해결해야 설치할 수 있습니다.', '점검 결과', 'OK', 'Warning') }
}
$btnRecheck.Add_Click({ Invoke-Checks })
$btnStopCollector.Add_Click({
  if ([Windows.Forms.MessageBox]::Show('실행 중인 수집기를 닫을까요? 하던 수집이 멈춥니다.', '수집기 닫기', 'YesNo', 'Question') -eq 'Yes') {
    foreach ($p in @(Get-RunningCollectors)) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 800; Invoke-Checks
  }
})

# 3 설정
$y = 0
$pages[2].Controls.Add((New-Label '설치 폴더' 0 $y 740 22 $fontBold)); $y += 24
$txtDir = New-Object Windows.Forms.TextBox; $txtDir.Location = New-Object Drawing.Point(0, $y); $txtDir.Size = New-Object Drawing.Size(560, 26)
$btnBrowse = New-Object Windows.Forms.Button; $btnBrowse.Text = '찾아보기'; $btnBrowse.Location = New-Object Drawing.Point(570, ($y - 1)); $btnBrowse.Size = New-Object Drawing.Size(100, 28)
$btnBrowse.Add_Click({ $d = New-Object Windows.Forms.FolderBrowserDialog; $d.Description = '설치할 폴더 (영어 이름)'; if ($d.ShowDialog() -eq 'OK') { $txtDir.Text = Join-Path $d.SelectedPath 'Court_Auction_Collector' } })
$pages[2].Controls.AddRange(@($txtDir, $btnBrowse)); $y += 30
$dirHint = New-Label '영어·숫자만 쓰는 폴더. 내려받기·바탕화면·OneDrive 폴더는 안 됩니다.' 0 $y 740 22; $dirHint.ForeColor = [Drawing.Color]::Gray; $pages[2].Controls.Add($dirHint); $y += 34

$pages[2].Controls.Add((New-Label '수집기 이름' 0 $y 740 22 $fontBold)); $y += 24
$txtName = New-Object Windows.Forms.TextBox; $txtName.Location = New-Object Drawing.Point(0, $y); $txtName.Size = New-Object Drawing.Size(240, 26)
$typeLbl = New-Label '' 252 ($y + 3) 480 22; $pages[2].Controls.AddRange(@($txtName, $typeLbl)); $y += 30
$nameHint = New-Label '관제기에 등록한 이름과 같아야 합니다. jiju 뒤에 숫자 네 자리(예: jiju0001)면 관제형, 그 밖은 보급형입니다.' 0 $y 740 22; $nameHint.ForeColor = [Drawing.Color]::Gray; $pages[2].Controls.Add($nameHint); $y += 34
$txtName.Add_TextChanged({
  $n = $txtName.Text.Trim()
  if (-not $n) { $typeLbl.Text = '' }
  elseif ($n -notmatch '^[A-Za-z0-9_\-]{2,40}$') { $typeLbl.Text = '✖ 영어·숫자·_·- 만 쓸 수 있습니다'; $typeLbl.ForeColor = [Drawing.Color]::FromArgb(161, 42, 42) }
  elseif ((Get-CollectorType $n) -eq 'control') { $typeLbl.Text = '→ 관제형 (관제기의 지시를 받아 수행)'; $typeLbl.ForeColor = [Drawing.Color]::FromArgb(29, 79, 145) }
  else { $typeLbl.Text = '→ 보급형 (직접 입력한 내용을 수집)'; $typeLbl.ForeColor = [Drawing.Color]::FromArgb(138, 90, 11) }
})

$pages[2].Controls.Add((New-Label '관제기 주소 (선택)' 0 $y 740 22 $fontBold)); $y += 24
$txtCtrl = New-Object Windows.Forms.TextBox; $txtCtrl.Location = New-Object Drawing.Point(0, $y); $txtCtrl.Size = New-Object Drawing.Size(400, 26)
$btnTest = New-Object Windows.Forms.Button; $btnTest.Text = '연결 확인'; $btnTest.Location = New-Object Drawing.Point(410, ($y - 1)); $btnTest.Size = New-Object Drawing.Size(100, 28)
$ctrlLbl = New-Label '' 520 ($y + 3) 230 22
$pages[2].Controls.AddRange(@($txtCtrl, $btnTest, $ctrlLbl)); $y += 30
$ctrlHint = New-Label '관제기 설치를 마치면 화면에 나오는 주소입니다 (예: 192.168.0.20:8080). 비워 두고 나중에 넣어도 됩니다.' 0 $y 740 22; $ctrlHint.ForeColor = [Drawing.Color]::Gray; $pages[2].Controls.Add($ctrlHint); $y += 36
function Get-ControllerUrl { $u = $txtCtrl.Text.Trim().TrimEnd('/'); if ($u -and $u -notmatch '://') { $u = 'http://' + $u }; return $u }
$btnTest.Add_Click({
  $u = Get-ControllerUrl
  if (-not $u) { $ctrlLbl.Text = '주소를 넣으십시오'; return }
  $ctrlLbl.Text = '확인 중...'; Pump
  try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$u/login"; $ctrlLbl.Text = "✔ 연결됨 (HTTP $($r.StatusCode))"; $ctrlLbl.ForeColor = [Drawing.Color]::FromArgb(17, 98, 58) }
  catch { $ctrlLbl.Text = '✖ 연결 안 됨 (설치는 가능)'; $ctrlLbl.ForeColor = [Drawing.Color]::FromArgb(161, 42, 42) }
})

$chkDesktop = New-Object Windows.Forms.CheckBox; $chkDesktop.Text = '바탕화면에 수집기 아이콘 만들기'; $chkDesktop.Checked = $true; $chkDesktop.Location = New-Object Drawing.Point(0, $y); $chkDesktop.AutoSize = $true; $y += 28
$chkStart = New-Object Windows.Forms.CheckBox; $chkStart.Text = '시작 메뉴에 BIDGUARD 폴더 만들기 (수집기·펌웨어 업데이트·지우기)'; $chkStart.Checked = $true; $chkStart.Location = New-Object Drawing.Point(0, $y); $chkStart.AutoSize = $true; $y += 28
$chkAuto = New-Object Windows.Forms.CheckBox; $chkAuto.Text = '윈도우를 켜면 수집기 자동 실행'; $chkAuto.Checked = $true; $chkAuto.Location = New-Object Drawing.Point(0, $y); $chkAuto.AutoSize = $true; $y += 28
$chkLegacy = New-Object Windows.Forms.CheckBox; $chkLegacy.Text = '예전 수집기 폴더 정리 (이름·설정·자료는 새 설치로 옮긴 뒤 지움)'; $chkLegacy.Checked = $true; $chkLegacy.Location = New-Object Drawing.Point(0, $y); $chkLegacy.AutoSize = $true; $chkLegacy.Visible = $false
$chkKept = New-Object Windows.Forms.CheckBox; $chkKept.Checked = $true; $chkKept.Location = New-Object Drawing.Point(0, ($y + 28)); $chkKept.AutoSize = $true; $chkKept.Visible = $false
$pages[2].Controls.AddRange(@($chkDesktop, $chkStart, $chkAuto, $chkLegacy, $chkKept))

function Initialize-SettingsPage {
  if ($script:State.OldInstall) {
    # 이미 설치된 자리에 새 판을 덮어씀 (두 군데에 설치가 남지 않게). 옮기려면 먼저 지우기 후 새로 설치.
    $txtDir.Text = $script:State.OldInstall.Path; $txtDir.ReadOnly = $true; $btnBrowse.Enabled = $false
    $dirHint.Text = '이미 설치된 자리에 새 판을 덮어씁니다. 다른 자리로 옮기려면 먼저 지우기를 한 뒤 새로 설치하십시오.'
  } elseif (-not $txtDir.Text) { $txtDir.Text = $script:Settings.default_install_dir }
  $src = $null
  if ($script:State.OldInstall) { $src = Join-Path $script:State.OldInstall.Path 'app' } elseif ($script:State.LegacyFolder) { $src = $script:State.LegacyFolder.Path } elseif ($script:State.KeptData) { $src = $script:State.KeptData }
  if ($src -and -not $txtName.Text) {
    try { $c = Get-Content -Raw -Encoding UTF8 (Join-Path $src 'config.json') | ConvertFrom-Json; $txtName.Text = $c.name } catch {}
    try { $s = Get-Content -Raw -Encoding UTF8 (Join-Path $src 'settings.json') | ConvertFrom-Json; if ($s.controller_url) { $txtCtrl.Text = $s.controller_url } } catch {}
  }
  $chkLegacy.Visible = [bool]$script:State.LegacyFolder -and -not $script:State.OldInstall
  if ($chkLegacy.Visible) { $chkLegacy.Text = "예전 수집기 폴더 정리: $($script:State.LegacyFolder.Path) (자료는 옮긴 뒤 지움)" }
  $chkKept.Visible = [bool]$script:State.KeptData -and -not $script:State.OldInstall -and -not $script:State.LegacyFolder
  if ($chkKept.Visible) { $chkKept.Text = "지울 때 옮겨 둔 자료 되살리기: $($script:State.KeptData)" }
}

# 4 설치
$script:StepNames = @('준비 (실행 중인 수집기 닫기·공간 확인)', '이전 이름·설정·자료 보관', '파이썬 준비 (화면 부품 포함)', '부품 설치 (인터넷 없이)', '프로그램 넣기·자료 되돌려 놓기', '아이콘·시작 메뉴·윈도우 앱 목록 등록', '자가점검 (설치한 수집기를 실제로 켜 봄)', '마무리')
$script:StepLabels = @()
for ($i = 0; $i -lt $script:StepNames.Count; $i++) { $l = New-Label "·  $($script:StepNames[$i])" 0 ($i * 24) 740 22; $l.ForeColor = [Drawing.Color]::Gray; $script:StepLabels += $l; $pages[3].Controls.Add($l) }
$script:Progress = New-Object Windows.Forms.ProgressBar; $script:Progress.Location = New-Object Drawing.Point(0, 200); $script:Progress.Size = New-Object Drawing.Size(760, 18)
$script:LogBox = New-Object Windows.Forms.TextBox; $script:LogBox.Multiline = $true; $script:LogBox.ReadOnly = $true; $script:LogBox.ScrollBars = 'Vertical'
$script:LogBox.Location = New-Object Drawing.Point(0, 226); $script:LogBox.Size = New-Object Drawing.Size(760, 190); $script:LogBox.Font = New-Object Drawing.Font('Consolas', 8.5); $script:LogBox.BackColor = $soft
$pages[3].Controls.AddRange(@($script:Progress, $script:LogBox))

# 5 완료
$doneTitle = New-Label '' 0 0 740 30 $fontTitle
$doneText = New-Object Windows.Forms.TextBox; $doneText.Multiline = $true; $doneText.ReadOnly = $true; $doneText.ScrollBars = 'Vertical'; $doneText.BackColor = $soft
$doneText.Location = New-Object Drawing.Point(0, 38); $doneText.Size = New-Object Drawing.Size(760, 330)
$btnRun = New-Object Windows.Forms.Button; $btnRun.Text = '수집기 실행'; $btnRun.Size = New-Object Drawing.Size(140, 34); $btnRun.Location = New-Object Drawing.Point(0, 378); $btnRun.BackColor = $green; $btnRun.ForeColor = [Drawing.Color]::White; $btnRun.FlatStyle = 'Flat'
$btnFw = New-Object Windows.Forms.Button; $btnFw.Text = '피코2 펌웨어 업데이트'; $btnFw.Size = New-Object Drawing.Size(180, 34); $btnFw.Location = New-Object Drawing.Point(150, 378)
$btnLog = New-Object Windows.Forms.Button; $btnLog.Text = '설치 기록 열기'; $btnLog.Size = New-Object Drawing.Size(130, 34); $btnLog.Location = New-Object Drawing.Point(340, 378)
$pages[4].Controls.AddRange(@($doneTitle, $doneText, $btnRun, $btnFw, $btnLog))
$btnLog.Add_Click({ Start-Process notepad.exe $script:LogFile })
$btnRun.Add_Click({ Start-Process (Join-Path $txtDir.Text 'Run_Court_Auction_Collector.bat') -WorkingDirectory $txtDir.Text })
$btnFw.Add_Click({ Start-Process (Join-Path $txtDir.Text 'Update_Pico2_Firmware.bat') -WorkingDirectory $txtDir.Text })

function Show-Page([int]$n) {
  for ($i = 0; $i -lt $pages.Count; $i++) { $pages[$i].Visible = ($i -eq $n) }
  $script:Page = $n
  $parts = @()
  for ($k = 0; $k -lt $pageNames.Count; $k++) { if ($k -eq $n) { $parts += "[ $($k+1) $($pageNames[$k]) ]" } else { $parts += "$($k+1) $($pageNames[$k])" } }
  $crumbs.Text = $parts -join '   ›   '
  $btnBack.Visible = ($n -in 1, 2); $btnNext.Visible = $true; $btnCancel.Enabled = ($n -ne 3)
  switch ($n) {
    0 { $btnNext.Text = '다음'; $btnNext.Enabled = $true }
    1 { $btnNext.Text = '다음'; Invoke-Checks }
    2 { $btnNext.Text = '설치'; $btnNext.Enabled = $true; Initialize-SettingsPage }
    3 { $btnNext.Visible = $false; $btnBack.Visible = $false }
    4 { $btnNext.Text = '닫기'; $btnNext.Enabled = $true; $btnCancel.Visible = $false; $btnBack.Visible = $false }
  }
  Pump
}

$btnBack.Add_Click({ if ($script:Page -gt 0) { Show-Page ($script:Page - 1) } })
$btnCancel.Add_Click({ $form.Close() })
$btnNext.Add_Click({
  switch ($script:Page) {
    0 { Show-Page 1 }
    1 { Show-Page 2 }
    2 {
      $dir = $txtDir.Text.Trim().TrimEnd('\')
      $bad = Test-InstallPath $dir
      if ($bad) { [void][Windows.Forms.MessageBox]::Show($bad, '설치 폴더', 'OK', 'Warning'); return }
      $name = $txtName.Text.Trim()
      if ($name -notmatch '^[A-Za-z0-9_\-]{2,40}$') { [void][Windows.Forms.MessageBox]::Show('수집기 이름을 영어·숫자로 넣으십시오 (예: jiju0001).', '수집기 이름', 'OK', 'Warning'); return }
      $kind = @{ control = '관제형'; supply = '보급형' }[(Get-CollectorType $name)]
      $msg = "다음 내용으로 설치합니다.$nl$nl  폴더: $dir$nl  수집기: $name ($kind)$nl  관제기: $(if (Get-ControllerUrl) { Get-ControllerUrl } else { '나중에 연결' })$nl$nl설치를 시작할까요?"
      if ([Windows.Forms.MessageBox]::Show($msg, '설치 확인', 'YesNo', 'Question') -ne 'Yes') { return }
      $opt = [pscustomobject]@{ InstallDir = $dir; CollectorName = $name; ControllerUrl = (Get-ControllerUrl)
        Desktop = $chkDesktop.Checked; StartMenu = $chkStart.Checked; AutoStart = $chkAuto.Checked
        LegacyFolder = $(if ($chkLegacy.Visible) { $script:State.LegacyFolder } else { $null }); CleanLegacy = $chkLegacy.Checked
        KeptData = $(if ($chkKept.Visible -and $chkKept.Checked) { $script:State.KeptData } else { $null }) }
      Show-Page 3
      Write-Log "설치 시작: $($script:Version.label) → $dir ($name)"
      $ok = Invoke-Install $opt
      if ($ok) {
        $doneTitle.Text = '✔ 설치를 마쳤습니다'; $doneTitle.ForeColor = [Drawing.Color]::FromArgb(17, 98, 58)
        $t  = "수집기: $name ($kind)    판: $($script:Version.label)$nl설치 폴더: $dir$nl$nl"
        $t += "이제 할 일$nl  ① 바탕화면의 '$($script:Settings.shortcut_name)' 아이콘으로 수집기를 켭니다.$nl"
        $t += "  ② 관제기 주소를 아직 안 넣었다면 수집기 Settings 탭에서 넣고 연결합니다.$nl"
        $t += "  ③ 피코2를 처음 쓰거나 새 판이 나왔으면 [피코2 펌웨어 업데이트]를 누릅니다.$nl"
        if ((Get-CollectorType $name) -eq 'control') { $t += "  ④ 관제형은 Status 탭의 Start 를 누르면 관제기 지시를 기다렸다가 수행합니다.$nl" }
        $t += $nl + "──────── 화면 안내 ────────" + $nl + (Get-ScreenGuide $script:State.Screen) + $nl + $nl
        $t += "지우려면: 시작 메뉴 BIDGUARD 폴더의 지우기, 또는 윈도우 설정 > 앱 에서 '$($script:Settings.product_name)'"
        $doneText.Text = $t
      } else {
        $doneTitle.Text = '✖ 설치를 마치지 못했습니다'; $doneTitle.ForeColor = [Drawing.Color]::FromArgb(161, 42, 42)
        $btnRun.Visible = $false; $btnFw.Visible = $false
        $doneText.Text = "이유: $($script:State.Error)$nl$nl설치 전 상태로 되돌렸습니다. 컴퓨터에는 바뀐 것이 없습니다.$nl$nl[설치 기록 열기]를 눌러 나온 내용을 관리자에게 보내 주십시오.$nl기록 파일: $($script:LogFile)"
      }
      Show-Page 4
    }
    4 { $form.Close() }
  }
})
$form.Add_FormClosing({ param($s, $e) if ($script:Page -eq 3) { $e.Cancel = $true } })

# 압축 파일에서 받은 파일의 '인터넷에서 받음' 표시를 지움 (파이썬 설치 때 보안 경고 창이 뜨지 않게)
try { Get-ChildItem -LiteralPath $script:BundleRoot -Recurse -File | Unblock-File -ErrorAction SilentlyContinue } catch {}

try {
  Write-Log "설치 마법사 시작: $($script:Version.label) / 묶음 $($script:BundleRoot)"
  Show-Page 0
  [void]$form.ShowDialog()
} catch {
  [void][Windows.Forms.MessageBox]::Show("예상하지 못한 오류로 멈췄습니다.$([Environment]::NewLine)$($_.Exception.Message)$([Environment]::NewLine)$([Environment]::NewLine)기록: $($script:LogFile)", '설치 프로그램 오류', 'OK', 'Error')
}
