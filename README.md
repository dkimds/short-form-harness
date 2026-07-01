# short-form-harness

레퍼런스 숏폼 영상의 **스타일을 JSON으로 분해**하고(분석 단계), 그 JSON과 사용자 입력 1개(text / image / video)를 받아 **동일한 스타일의 새 숏폼 1편을 생성**하는 재사용 가능한 파이프라인 하네스.

대상 스타일: 뷰티/스킨케어 UGC 광고 (biodance TikTok). 평가 핵심은 영상 품질이 아니라 ① 다른 입력 → 다른 결과(재사용성), ② 분석↔생성의 구조적 분리다.

---

## 환경 변수

| 이름              | 발급처                                           | 용도                                                          |
|-------------------|--------------------------------------------------|---------------------------------------------------------------|
| `GOOGLE_API_KEY`  | <https://aistudio.google.com>                    | Gemini(분석·훅·Gate QA), Imagen(장면 이미지), Veo(히어로 클립), TTS(보이스오버) |

---

## 빠른 시작

```bash
# 시스템 의존성 (ffmpeg — ffprobe 포함)
brew bundle install

# 환경 설정
cp .env.example .env
# .env에 GOOGLE_API_KEY 입력

# 의존성 설치 (pyproject.toml / uv.lock 기준)
uv sync
```

> `probe.py`/`cut_detect.py`/`audio_stats.py`/`gate.py`가 `ffmpeg`/`ffprobe`를 PATH에서 직접 subprocess로 호출하므로 ffmpeg 설치가 필수다(moviepy가 pip으로 받는 내장 ffmpeg와는 별개). `pytest`는 이 호출들을 모킹하므로 ffmpeg 없이도 통과하지만, `analyze`/`generate` 실전 실행에는 반드시 필요하다. ImageMagick·libsndfile·별도 폰트는 필요 없다 — moviepy 2.x `TextClip`은 PIL 기반이고 `soundfile`은 libsndfile을 휠에 번들한다.

---

## 실행 방법

```bash
# 분석
uv run python cli.py analyze --ref refs/reference1.mp4 --out profiles/biodance.json

# 생성 — 텍스트 입력
uv run python cli.py generate --profile profiles/biodance.json --input "glow serum"
```

> 레퍼런스는 분석에만 사용한다. 생성 단계는 `profiles/*.json`만 읽으며 레퍼런스 mp4를 직접 참조하지 않는다.

---

## 디렉터리 구조

```
short-form-harness/
├─ cli.py                        # analyze / generate 서브커맨드 진입점
├─ style_profile.schema.json     # 프로파일 스키마 — source of truth
├─ profiles/
│  └─ biodance.json              # 레퍼런스 분석 산출물 (예시)
├─ refs/                         # 레퍼런스 mp4 (분석 전용, 생성에 직접 사용 안 함)
├─ src/
│  ├─ analyze/                   # probe, cut_detect, audio_stats, vision, synthesize_profile
│  ├─ generate/                  # brief, hook_gen, plan, assets, compose, gate
│  └─ common/                    # config, vendor_client, io, exceptions
├─ prompts/                      # analyze_vision.md, hook_gen.md, gate_judge.md
├─ assets/
│  ├─ fonts/                     # 자막 렌더용 폰트
│  ├─ music/                     # 라이선스 안전 BGM 트랙 + index.json
│  └─ overlays/                  # 핸들·워터마크 placeholder
├─ tests/                        # 단위·속성·통합 테스트
├─ outputs/<run_id>/             # 매 생성 run의 산출물
├─ .env.example                  # 키 이름·발급처·용도 (값 비움)
├─ pyproject.toml                # 의존성 정의
└─ uv.lock                       # 잠금 파일
```

`analyze/`와 `generate/`는 서로를 import하지 않는다. 유일한 접점은 `style_profile.json` 파일이다.

---

## 내부 흐름

```
분석 단계
  ref.mp4
    ├─ probe.py         → 해상도·fps·duration
    ├─ cut_detect.py    → 컷 수·페이싱 분포
    ├─ audio_stats.py   → music_start·LUFS·보이스오버 유무
    └─ vision.py        → Gemini → beats·captions·visual·audio_mood
              │
              ▼
    synthesize_profile.py
              │
              ▼
    profiles/<name>.json   ← 스키마 검증 통과

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
              (유일한 인터페이스: JSON 파일)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

생성 단계
  user_input + profiles/<name>.json
              │
              ▼
    brief.py            → UserInput + profile 섹션 결합
              │
              ▼
    hook_gen.py         → Gemini (temperature≥0.8, seed 랜덤) → 훅 텍스트
              │
              ▼
    plan.py             → beat sheet → shotlist (컷 수 샘플링, 비율 배분)
              │
              ▼
    assets.py           → Imagen 이미지 / Veo i2v / TTS 보이스오버
              │
              ▼
    compose.py          → moviepy/ffmpeg 9:16 합성 → final.mp4
              │
              ▼
    gate.py             → 결정적 체크(종횡비·길이·컷수) + Gemini 자기판정
              │
         PASS / FAIL (최대 2회 재시도)
              │
              ▼
    outputs/<run_id>/
```

---

## 출력물

매 `generate` 실행마다 `outputs/<run_id>/`에 아래 파일이 저장된다.

| 파일             | 내용                                                               |
|------------------|--------------------------------------------------------------------|
| `final.mp4`      | 최종 합성 영상 (9:16, 10~15초)                                     |
| `prompt.txt`     | 사용된 user_input, 훅 텍스트, profile 경로, 실행 타임스탬프       |
| `shotlist.json`  | 각 장면의 role·asset_type·생성 프롬프트·실제 파일 경로             |
| `gate.json`      | 결정적 체크(종횡비·길이·컷수) + Gemini 비전 자기판정 결과          |

`run_id`는 `YYYYMMDD_HHMMSS_<6자리 hex>` 형식으로 자동 생성된다.

예시:

```
outputs/20260629_163352_09b1b2/
├─ final.mp4
├─ prompt.txt
├─ shotlist.json
├─ shot_00.png        ← 장면별 Imagen 생성 이미지 (또는 폴백)
├─ shot_03.mp4        ← Veo i2v 히어로 클립
├─ voiceover.wav      ← TTS 보이스오버 (has_voiceover=true인 경우)
└─ gate.json
```
