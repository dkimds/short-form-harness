<!--
============================================================
retro.md (작성 중 — 작업하며 채워간다)
- 내용은 직접 채운다. AI로 매끈하게 뽑지 말 것 = 면접에서 본인 언어로 답해야 함.
- 작성 원칙: "결과 보고"가 아니라 "판단의 증거". 정직한 실패 + 우회 설계가 최고점.
- 분량 배분: 가정·실패 40% / 모델비교 25% / 개선안 15% / Gate설계 20%
- 아래 [LOG A/B/C]를 옆에 켜두고 그때그때 적는다. 끝나고 기억으로 쓰면 다 뭉개진다.
- 진행 상태: Task 1(스캐폴딩)~Task 2(vendor_client) 완료. 외부 API 실호출 전.
============================================================
-->

# Retrospective — 숏폼 영상 생성 Harness

## TL;DR (3줄)
<!-- 면접관이 제일 먼저 읽는다. "무엇을 만들었고, 핵심 판단은 뭐였고, 어디까지 됐다"를 3줄로.
     ※ 파이프라인 1편이라도 돌고 나서 마지막에 채우는 게 정확하다. 지금은 비워둠. -->
- 만든 것:
- 핵심 판단(한 문장):
- 도달 지점 / 한계:

---

## 1. 가정과 가설 (Hypotheses)
<!-- 떨어지는 버전: "스타일을 분석해 적용했다"
     통하는 버전: 검증 가능한 가설로. 스키마 = 가설의 외재화라는 프레임으로 쓴다.
     각 필드가 "이게 스타일을 이룬다"는 반증 가능한 주장이고, 생성 결과로 맞고 틀림을 되짚는다. -->

- **가설 1 (중심):** 스타일 = 비주얼이 아니라 구조다. → 근거: 같은 biodance인데 ref1 ≈ 0.9초컷(빠른 몽타주) vs ref2 ≈ 2.4초컷(느린 호흡)으로 리듬이 정반대. 비주얼은 거의 같은데 느낌이 다름 → 차이의 원인은 구조라 판단. → 설계: style_profile의 무게중심을 pacing/narrative/captions에 둠.
- **가설 2 (pacing은 단일 값이 아니라 분포):** ref1/ref2가 다르므로 평균 컷길이 하나로 못 박지 않고 분포에서 샘플링. → 이게 "같은 시스템 → 다른 결과"의 엔진.
- **가설 3 (자막 = 내용이 아니라 슬롯):** 자막이 이 스타일인 이유는 *무슨 말이냐*가 아니라 *어디에·언제·어떤 모양으로* 뜨느냐. → 문구는 비우고 슬롯(위치·타이밍·스타일)만 규정 → 메시지(가변)와 스타일(불변) 분리.
- **가설 4 (훅은 생성·비결정):** 훅이 가장 레버리지 큰 요소 → is_hook 플래그로 박아 생성 단계가 특별 취급, temperature↑로 재실행마다 달라지게.
- **메타:** 위 가설들을 머릿속이 아니라 스키마 필드로 외재화 → 반증 가능. 생성물이 어설프면 어느 필드의 가정이 틀렸는지 짚어 그 필드만 수정. (schema=가설 레지스트리, generate=실험, gate=검정)
- **틀렸던 가설:** 레퍼런스 여러 편을 합치면 더 안정적인 스타일 파라미터가 나온다 → 틀렸다. ref1(0.9s컷)과 ref2(2.4s컷)는 스타일이 정반대라, 합쳤을 때 `cut_count_range: [4, 12]`처럼 범위가 너무 넓어져서 생성 단계 샘플링에 실질적 제약이 없어진다. 게다가 vision 결과는 첫 번째 비전 분석 결과만 사용하므로 2개 레퍼런스의 시각 정보가 합성되지도 않는다. → 차라리 스타일이 분명한 1편을 기준 레퍼런스로 골라 쓰고, 나머지는 참고용으로 따로 분석하는 게 더 나은 설계였을 수 있다. (spec에 `merge_pacing` 함수가 명시되어 있어 구현했지만, 실제 운용에서는 단일 레퍼런스 1편 → 1 profile 흐름이 더 직관적이다)

## 2. 어디서 막혔나 (Failures & Workarounds)
<!-- [LOG B]에서 가져온다. 막연한 "어려웠다" 금지. 구체적 증상 + 수치 + 우회.
     ※ 외부 API 실호출 전이라 아직 비어있음. 구현하며 채운다. -->

| 막힌 지점 | 증상(수치 포함) | 원인 추정 | 우회/해결 |
|---|---|---|---|
|  |  |  |  |
|  |  |  |  |

- 끝내 못 푼 것: <!-- 솔직하게. "시간 있었으면" 섹션으로 연결. -->

## 3. 모델 실험 & 비교 (Model Bench)
<!-- [LOG C]에서. "제일 좋았던 것"만 쓰지 말고 왜 나머지는 탈락했는지 한 줄씩.
     트레이드오프를 말할 줄 아는 게 시니어 신호.
     아래는 현재 '디폴트로 설정한' 모델. 점수 칸은 실호출 후 측정해 채운다. -->

| 단계 | 후보 모델 | 일관성 | 속도 | 비용 | 스타일적합 | 채택? | 한 줄 사유 |
|---|---|---|---|---|---|---|---|
| 분석/비전 | gemini-2.0-flash | | | | | (디폴트) | mp4 video-native 입력, 분석↔gate 동일 모델 |
| 훅 생성 | gemini-2.0-flash | | | | | (디폴트) | 분석과 같은 모델로 컨텍스트 일관 |
| 이미지 | imagen-3.0-generate-002 | | | | | (디폴트) | 측정 예정 |
| 히어로 클립(i2v) | veo-2.0-generate-001 | | | | | (디폴트) | 측정 예정 |

- 최종 조합과 이유: <!-- 측정 후 확정 -->

## 4. 시간이 더 있었다면 (Next)
<!-- "품질 개선" 같은 막연한 말 금지. 구체적 다음 액션 + 시스템 사고가 드러나게. -->
1.
2.
3.

## 5. 자기판정 Gate 설계 (가산점 — 진심으로 쓸 것)
<!-- 실제 구현 못 했어도 설계만 명확하면 점수 나온다. 결정적 체크 / 의미 체크로 나눠라. -->

- **결정적 체크:** (ffprobe·CV로 자동) 9:16 / 길이 범위 / 컷 수 / 음악 존재 / 자막 슬롯 픽셀 영역 텍스트 유무 → pass/fail
- **의미 체크:** 생성 final.mp4를 Gemini에 재입력 → style_profile의 genre·mood·beat 일치도 0~1 스코어. 분석과 동일 모델이라 기준 일관.
- **루프:** 임계값 미달 시 재생성(--retry N), 결과를 gate.json에 기록
- 한계/오탐 가능성: <!-- 자기 설계의 약점을 스스로 짚으면 신뢰도 상승. 예: 분석·판정이 같은 모델이라 '같은 편향'을 통과시킬 위험 -->

---

## (의도적) 면접 떡밥
<!-- retro 본문 곳곳에 "이건 의도적으로 단순화했다 / 의도적으로 선택했다"를 1~2개 심어라.
     면접관이 그걸 물으면 준비된 답이 나오고, 대화를 네 강점으로 끌고 온다. -->
- 떡밥 1 (SDK 선택): `google-generativeai`(deprecated) 대신 신 SDK `google-genai` 채택. → 예상 질문: "왜 신 SDK?" → 답: deprecated 경고 + future-proof + Files API 통합이 mp4 업로드(분석 단계)에 자연스러움.
- 떡밥 2 (단일 벤더 lock-in): Gemini 단일 벤더 → vendor_client.py 한 곳에 격리해 OpenAI/Runway 교체 가능하게 추상화. → 예상 질문: "lock-in 위험?" → 답 준비됨.
- 떡밥 3 (음악 단순화): <!-- 라이브러리 매칭으로 단순화한 이유 — 구현 시 채움 -->


<!--
============================================================
작업 중 켜둘 3개 로그 (retro의 90%는 여기서 나온다)
============================================================

[LOG A] 결정 로그 — 갈림길마다 한 줄

  Task 2 (vendor_client.py 구현):
  `google-generativeai` vs `google-genai`(신 SDK) → `google-genai` 선택
  이유: google-generativeai가 deprecated 경고 발생, google-genai가 현재 공식 Python SDK
  트레이드오프: API가 `genai.Client()` 패턴으로 바뀌어 초기 적응 필요,
              대신 future-proof하고 Files API 통합이 더 자연스러움
  → requirements.txt에 `google-genai>=1.0.0` 추가

  Task 3.7 (audio_stats.py VO 감지):
  정확한 화자분리(pyannote/whisper) vs spectral centroid 휴리스틱 → 휴리스틱 선택
  이유: 외부 화자분리 모델은 의존성·비용·시간 리스크 큼. 레퍼런스 2편 모두 VO 없음
  트레이드오프: mean centroid>2000Hz + speech_ratio>0.3 기준은 음악↔VO 혼재 시 오탐 가능
  → 면접 떡밥: "VO 감지 왜 단순?" → "레퍼런스 특성 + 시간 트레이드오프 판단"

  Task 3.10 (synthesize_profile.py audio 섹션 병합):
  AudioStats(ffmpeg 측정: music_start_sec, target_lufs, has_voiceover) +
  vision audio(Gemini 추출: music_mood, vo_style) → style_profile.audio 하나로 병합
  이유: 스키마상 audio는 단일 섹션이지만 출처가 두 곳. "분석↔생성 인터페이스는 JSON 하나"
       원칙을 지키려면 두 출처를 합성 단계에서 합쳐야 함
  트레이드오프: ffmpeg 측정값(객관적)과 Gemini 해석값(주관적)이 한 섹션에 섞임.
              music_mood 같은 soft 필드는 Gemini 응답 품질에 의존적
  → 면접 떡밥: "audio 섹션 두 출처 어떻게 관리?" → 의도적 설계임을 설명 가능

[LOG B] 실패 로그 — 막힐 때마다

  Task 3.7 (loudnorm LUFS 파싱):
  증상: ffmpeg loudnorm이 stderr에 JSON 출력 — stdout 파싱 시 항상 빈값
  시도한 것: result.stdout 에서 JSON 파싱 → 항상 -23.0 기본값만 반환
  우회: result.stderr에서 regex `\{[^{}]+\}` 로 JSON 블록 추출 → 해결

  Task 4.2 / 체크포인트1 (Gemini 429 rate limit):
  증상: free tier 일일 할당량 0 — analyze 실행 즉시 429 RESOURCE_EXHAUSTED
  원인: gemini-2.0-flash free tier `GenerateRequestsPerDayPerProjectPerModel` limit = 0
        (이미 당일 다른 호출로 소진된 상태)
  결과: vision 결과 None → narrative/captions/visual 빈 상태로 profile 저장
  우회: 스키마는 통과(required 필드 없음), pacing/format/audio는 정상. 다음날 재시도 또는 유료 키 사용
  → retro 교훈: 분석 파이프라인이 vision 없이도 graceful degradation으로 profile 저장까지 완주함을 확인

  Task 4.2 체크포인트1 (gemini-2.5-flash FAILED_PRECONDITION):
  증상: 모델 교체 후에도 400 FAILED_PRECONDITION — "File is not in an ACTIVE state"
  원인: Files API는 업로드 후 처리 시간이 필요. 즉시 generateContent 호출 시 파일이 ACTIVE 아님
  우회: 업로드 후 files.get()으로 state==ACTIVE 될 때까지 폴링(최대 20초) → 해결
  → 체크포인트 1 최종 통과: schema PASS, beats 5개, captions 6개 모두 채워짐

  Task 4.2 (librosa 미설치):
  증상: `No module named 'librosa'` — audio onset/VO 감지 스킵, 기본값 반환
  원인: requirements.txt에 있지만 uv 환경에 아직 설치 안 됨
  우회: `uv pip install librosa` 필요. LUFS는 ffmpeg로 측정하므로 핵심 기능엔 영향 없음

[LOG C] 모델 벤치 — 모델 바꿀 때마다
  단계: 설정 | 모델: gemini-2.0-flash(기본), imagen-3.0-generate-002, veo-2.0-generate-001
  관찰: 기본값만 설정, 실제 호출은 분석 파이프라인 구현 후 측정 예정

  단계: 분석/비전 | 모델: gemini-2.0-flash
  관찰: analyze_vision.md 프롬프트 — beats/captions/visual/audio JSON 강제 출력
        실제 응답 품질은 Task 3.9 vision.py 실행 시 측정 예정
============================================================
-->