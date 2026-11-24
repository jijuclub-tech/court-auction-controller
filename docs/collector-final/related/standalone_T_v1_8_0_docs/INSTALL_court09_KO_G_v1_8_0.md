# 법원봇 09 대전 전용 수집기 시험판 (v1.7_4p + 입력 잠금 T-v1.8.0)

한영님 지시 t1141u (2026-11-23 13:47 KST): 키보드·마우스 잠금 시험판은 **법원봇 09 대전 수집기에만** 붙인다. 1호와 다른 봇 수집기는 그대로 둔다.

- **아직 설치하지 마세요.** 설치 후보 경로: `C:\BIDGUARD\court09\` (PC: DESKTOP-698F5NB)
- **1호 PC(jijuclub5)나 다른 봇 PC에 깔면 안 됩니다.** 잠금은 PC 이름이 DESKTOP-698F5NB가 아니면 스스로 거부합니다.
- 잠금 모듈 출처: GitHub court-auction-controller PR 51 (브랜치 `feature/issue-50-launch-upload-T-v1-8-0`, 커밋 71287e4). PR은 병합 전이고, 실제 PC에서 돌려 본 적이 없습니다.

## 1. 잠금은 기본으로 꺼져 있습니다
평소처럼 실행하면 원래 v1.7_4p와 똑같이 돕니다. 키보드·마우스는 건드리지 않습니다.
```
cd /d C:\BIDGUARD\court09
.venv\Scripts\python.exe -m standalone run collector_kb\sites\court_nonmetro.json --minutes 30 --i-am-here
```
`/lock` 을 붙여도 아래 3가지가 모두 `true` 가 아니면 **잠그지도, 시작하지도 않고** "잠금 거부"를 띄웁니다. 이 패키지는 셋 다 `false` 로 나갑니다 (`court09_lock_config_G_v1_8_0.json`).
- `independent_watchdog_verified` 별도 감시 작업이 수집기가 죽어도 15분 뒤 풀어 주는지 확인
- `remote_unlock_verified` 원격에서 /unlock 이 실제로 풀리는지 확인
- `rdp_access_verified` 원격 접속 세션이 살아 있는지 확인
그 밖에 `approved_physical_hids`(잠글 키보드·마우스의 정확한 장치 ID)와 `detected_pico2_hids`(피코2 장치 ID)가 채워져야 합니다. 지금은 비어 있습니다.

## 2. 풀기 (/unlock)
수집기가 꺼져 있어도 됩니다. **관리자 권한** PowerShell 또는 명령창에서:
```
cd /d C:\BIDGUARD\court09
.venv\Scripts\python.exe -m standalone /unlock
```
같은 뜻: `python -m standalone unlock`, `python court09_unlock_G_v1_8_0.py --unlock`, 또는 `unlock_court09_G_v1_8_0.bat` 을 관리자 권한으로 실행.
- 잠금 기록(저널) 위치는 고정: `C:\BIDGUARD\court09\state_lock_G_v1_8_0\input_lock_journal_T_v1_8_0.json`
- 잠금이 없으면 `UNLOCK_OK 잠금 없음`, 풀면 `UNLOCK_OK 키보드·마우스를 다시 켰어요`.
- `UNLOCK_RETRY_REQUIRED` 가 나오면 다시 실행. 그래도 안 되면 장치 관리자에서 직접 켭니다.

## 3. 15분 만료와 재부팅 복원
- 잠글 때 만료 시각(15분 뒤)을 저널에 먼저 적고 나서 장치를 끕니다.
- 만료 복원은 **별도 감시 작업**이 해야 합니다: `python court09_unlock_G_v1_8_0.py --watchdog` 를 작업 스케줄러에 "시작할 때 / 관리자"로 등록. 이 등록은 아직 하지 않았습니다.
- 저널은 재부팅해도 남으므로, 재부팅 뒤 감시 작업이 다시 떠서 풀어 줍니다(현장 시험 필요).
- 15분이 지나면 수집 중이어도 풀립니다. 오래 잠그려면 연장 기능을 따로 검증해야 합니다.

## 4. 피코2는 잠기지 않습니다
피코2(USB VID 2E8A)는 잠금 대상에 넣을 수 없게 막혀 있습니다. 지정한 물리 키보드·마우스의 정확한 장치 ID만 끕니다. 장치 종류 전체를 끄지 않습니다.

## 5. 원격 풀기 시험은 글록봇 연결로만
이 PC는 원격 데스크톱(RDP)이 꺼져 있습니다(fDenyTSConnections=1, TermService 중지). 그래서 잠근 뒤 원격으로 푸는 시험은 **글록봇 연결로 /unlock 을 실행하는 방법**으로만 할 수 있습니다. 이 시험이 통과하기 전에는 `remote_unlock_verified` 를 `true` 로 바꾸지 않습니다.

## 6. 꺼 둔 기능
`/upload`, `/kiosk`, 계정 대여는 코드만 들어 있고 꺼져 있습니다. 붙이면 "꺼져 있어요"를 띄우고 시작하지 않습니다. 운영 관제기와 NAS 권한은 바꾸지 않았습니다.

## 7. 남은 설치 순서 (한영님 확인 뒤)
1. 이 폴더를 `C:\BIDGUARD\court09\` 로 복사 (기존 자료 삭제·이동 금지)
2. `.venv` 만들고 `pip install -r requirements.txt`, `config.json` 은 09용으로 작성 (collector_id, 로컬 대기 `C:\BIDGUARD\court09_out`)
3. 옵션 없이 `python tests\run_all.py` → ALL PASS 확인
4. 장치 ID 확인: 피코2(COM4, VID_2E8A PID_000C)와 잠글 키보드·마우스 정확한 ID를 설정 파일에 기록
5. 감시 작업 등록 → 사람이 앞에 있는 상태에서 짧게 잠금·글록봇 /unlock·강제종료·재부팅 복원 시험
6. 모두 통과하면 그때 3가지 값을 `true` 로 (사람이 직접)
