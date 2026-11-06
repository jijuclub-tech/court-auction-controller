# 관제기 검수 요청 (Claude)

## 추가된 기능 (이번 반영)

### 1. 관리자 인증
- `set_admin(username, password)`: 비밀번호 PBKDF2-HMAC-SHA256 해시 저장
- `verify_admin(username, password)`: 상수 시간 비교

### 2. API 키 관리
- `store_api_key(name, api_key)`: Fernet 암호화 저장 (`apikeys.enc`)
- `get_encrypted_api_key_for_collector(collector_name, key_name)`
  - **관제형 수집기에만** 전달
  - 보급형은 거부
  - 일회성 세션 키로 재암호화하여 전달

### 3. 로그 연동
- API 키 발급 시 관제형 로그에 기록 (`api_key_issued`)

## 파일
- `controller.py` — 핵심 로직
- `requirements.txt` — `cryptography` 추가
- `credentials.json` — 관리자 해시 (생성됨)
- `apikeys.enc` — 암호화된 키 저장소
- `.apikey_secret` — Fernet 마스터 키 (권한 주의)

## 검수 포인트
1. 비밀번호 해시 강도 및 저장 방식
2. API 키 암호화 저장 및 전달 시 보급형 차단 여부
3. 세션 키 전달 방식의 보안 리스크
4. 로그에 민감 정보가 남지 않는지
5. 우분투 서버 배포 시 파일 권한 권장사항

## 미반영 / 추후
- 관리자 세션 토큰 / 만료
- API 키 회전
- 전송 채널 분리 (session_key를 별도 경로로)
