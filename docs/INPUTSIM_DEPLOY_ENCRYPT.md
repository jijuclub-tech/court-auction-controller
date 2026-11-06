# InputSim 배포 시 암호화 지시

## 대상
- `core/input_sim.py` (클래스 `InputSim`)

## 원칙
- **개발 중**: `.py` 소스 그대로 유지. Grok만 수정.
- **배포 시**: 반드시 암호화 또는 난독화 후 포함. 원본 `.py`는 배포 패키지에 넣지 않음.

## 배포 전 필수 작업
1. `input_sim.py`를 pyarmor 또는 동등 도구로 암호화
2. 암호화된 결과물만 수집기 패키지에 포함
3. 원본 `input_sim.py`는 배포본에서 제외
4. 수집기는 `from core.input_sim import InputSim`으로 호출만 수행

## 담당
- 암호화 적용: ChatGPT (배포 단계)
- 소스 수정: Grok만

## 참고
컴파일(.pyd)만으로는 역분석이 가능하므로, 배포 시에는 암호화 단계를 반드시 거친다.
