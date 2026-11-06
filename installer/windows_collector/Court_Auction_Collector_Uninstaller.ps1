#Requires -Version 5.1
<#
  BIDGUARD 법원경매 수집기 — 지우기
  설치 폴더의 Uninstall_Court_Auction_Collector.bat, 시작 메뉴, 윈도우 설정 > 앱 어디서 눌러도 이 창이 뜬다.
  지우는 것: 프로그램·전용 파이썬(이 설치가 깐 경우만)·바탕화면/시작 메뉴/자동 실행 아이콘·윈도우 앱 목록 등록
  수집 자료(reports·data·설정)는 사람이 고른다: 지우거나, 따로 옮겨 둔다(기본).
#>
param([Parameter(Mandatory = $true)][string]$InstallDir)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
Set-Location $env:TEMP   # 지울 폴더 안에 머물지 않게

$InstallDir = (Resolve-Path -LiteralPath $InstallDir).Path.TrimEnd('\')
$log = Join-Path $env:TEMP ("BIDGUARD_Collector_Uninstall_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
function Write-Log([string]$m) { Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) -Encoding UTF8 }
$nl = [Environment]::NewLine

$recordPath = Join-Path $InstallDir 'install_record.json'
if (-not (Test-Path $recordPath)) {
  [void][Windows.Forms.MessageBox]::Show("이 폴더에는 수집기 설치 기록이 없습니다:$nl$InstallDir", '지우기', 'OK', 'Warning'); exit 1
}
$rec = Get-Content -Raw -Encoding UTF8 $recordPath | ConvertFrom-Json
$settings = Get-Content -Raw -Encoding UTF8 (Join-Path $InstallDir 'installer\installer_settings.json') | ConvertFrom-Json
$keepTo = Join-Path (Split-Path -Parent $InstallDir) ("Court_Auction_Collector_Data_{0}" -f (Get-Date -Format 'yyyyMMdd_HHmm'))

# ── 확인 창 ──
$f = New-Object Windows.Forms.Form
$f.Text = "$($settings.product_name) 지우기"; $f.Size = New-Object Drawing.Size(560, 330); $f.StartPosition = 'CenterScreen'
$f.FormBorderStyle = 'FixedDialog'; $f.MaximizeBox = $false; $f.MinimizeBox = $false; $f.Font = New-Object Drawing.Font('Malgun Gothic', 10)
$t = New-Object Windows.Forms.Label; $t.Location = New-Object Drawing.Point(20, 16); $t.Size = New-Object Drawing.Size(510, 140)
$t.Text = "수집기 $($rec.collector_name) ($($rec.version)) 을(를) 이 컴퓨터에서 지웁니다.$nl$nl" +
          "  • 실행 중인 수집기는 닫습니다.$nl  • 프로그램, 아이콘, 자동 실행, 윈도우 앱 목록 등록을 지웁니다.$nl" +
          $(if ($rec.python_installed_by_this_setup) { "  • 이 설치가 깐 전용 파이썬도 지웁니다.$nl" } else { "  • 원래 이 PC에 있던 파이썬은 그대로 둡니다.$nl" })
$chk = New-Object Windows.Forms.CheckBox; $chk.Location = New-Object Drawing.Point(20, 160); $chk.Size = New-Object Drawing.Size(510, 24)
$chk.Text = '수집 자료·설정도 모두 지우기 (되살릴 수 없음)'
$keepLbl = New-Object Windows.Forms.Label; $keepLbl.Location = New-Object Drawing.Point(38, 186); $keepLbl.Size = New-Object Drawing.Size(495, 44); $keepLbl.ForeColor = [Drawing.Color]::Gray
$keepLbl.Text = "체크하지 않으면 자료는 여기로 옮겨 둡니다:$nl$keepTo"
$chk.Add_CheckedChanged({ $keepLbl.Visible = -not $chk.Checked })
$ok = New-Object Windows.Forms.Button; $ok.Text = '지우기'; $ok.Location = New-Object Drawing.Point(318, 240); $ok.Size = New-Object Drawing.Size(100, 34); $ok.DialogResult = 'OK'
$no = New-Object Windows.Forms.Button; $no.Text = '취소'; $no.Location = New-Object Drawing.Point(428, 240); $no.Size = New-Object Drawing.Size(100, 34); $no.DialogResult = 'Cancel'
$f.Controls.AddRange(@($t, $chk, $keepLbl, $ok, $no)); $f.AcceptButton = $ok; $f.CancelButton = $no
if ($f.ShowDialog() -ne 'OK') { exit 0 }
$deleteData = $chk.Checked
if ($deleteData -and [Windows.Forms.MessageBox]::Show('수집 자료까지 정말 지울까요? 되살릴 수 없습니다.', '마지막 확인', 'YesNo', 'Warning') -ne 'Yes') { exit 0 }

$problems = @()
try {
  Write-Log "지우기 시작: $InstallDir (자료 지우기=$deleteData)"
  # 1 실행 중인 수집기·펌웨어 업데이트기 닫기
  foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'")) {
    if ($p.ExecutablePath -and $p.ExecutablePath.ToLower().StartsWith($InstallDir.ToLower())) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; Write-Log "종료: PID $($p.ProcessId)" }
  }
  Start-Sleep -Milliseconds 800
  # 2 자료 옮기기
  $app = Join-Path $InstallDir 'app'
  if (-not $deleteData) {
    $moved = @()
    foreach ($name in $settings.preserve_in_app) {
      $src = Join-Path $app $name
      if (Test-Path -LiteralPath $src) { New-Item -ItemType Directory -Force -Path $keepTo | Out-Null; Move-Item -LiteralPath $src -Destination (Join-Path $keepTo $name) -Force; $moved += $name }
    }
    $fwRec = Join-Path $InstallDir 'firmware_install_record.json'
    if (Test-Path $fwRec) { New-Item -ItemType Directory -Force -Path $keepTo | Out-Null; Move-Item $fwRec $keepTo -Force; $moved += 'firmware_install_record.json' }
    Write-Log "자료 옮김: $($moved -join ', ') → $keepTo"
  }
  # 3 아이콘·자동 실행
  foreach ($s in @($rec.shortcuts)) { if ($s -and (Test-Path -LiteralPath $s)) { Remove-Item -LiteralPath $s -Force; Write-Log "아이콘 지움: $s" } }
  $sm = Join-Path ([Environment]::GetFolderPath('Programs')) 'BIDGUARD'
  if ((Test-Path $sm) -and -not (Get-ChildItem $sm -Force)) { Remove-Item $sm -Force }
  # 4 윈도우 앱 목록
  $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$($settings.product_id)"
  if (Test-Path $key) { Remove-Item $key -Recurse -Force; Write-Log '윈도우 앱 목록에서 지움' }
  # 5 전용 파이썬 (이 설치가 깐 경우만)
  if ($rec.python_installed_by_this_setup) {
    $pyInst = Get-ChildItem (Join-Path $InstallDir 'runtime\python_installer') -Filter '*.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pyInst) {
      $proc = Start-Process $pyInst.FullName -ArgumentList '/quiet /uninstall' -PassThru -Wait -WindowStyle Hidden
      Write-Log "전용 파이썬 지움 (코드 $($proc.ExitCode))"
      if ($proc.ExitCode -ne 0) { $problems += "전용 파이썬 지우기 코드 $($proc.ExitCode)" }
    }
  }
  # 6 설치 폴더
  Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
  if (Test-Path -LiteralPath $InstallDir) { Start-Sleep -Seconds 2; Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue }
  if (Test-Path -LiteralPath $InstallDir) { $problems += "일부 파일이 사용 중이라 남았습니다: $InstallDir (컴퓨터를 다시 켠 뒤 지우십시오)" }
  $parent = Split-Path -Parent $InstallDir
  if ((Test-Path $parent) -and -not (Get-ChildItem $parent -Force) -and $parent.Length -gt 3) { Remove-Item $parent -Force }
  Write-Log '지우기 끝'
} catch { $problems += $_.Exception.Message; Write-Log "오류: $($_.Exception.Message)" }

$msg = "수집기를 지웠습니다.$nl"
if (-not $deleteData -and (Test-Path $keepTo)) { $msg += "$nl자료는 여기로 옮겨 두었습니다:$nl$keepTo$nl(다시 설치하면 설치 프로그램이 이 자료를 찾아 되살릴지 묻습니다)$nl" }
if ($problems.Count) { $msg += "$nl확인할 점:$nl  - " + ($problems -join "$nl  - ") + "$nl$nl기록: $log" }
[void][Windows.Forms.MessageBox]::Show($msg, '지우기', 'OK', $(if ($problems.Count) { 'Warning' } else { 'Information' }))
