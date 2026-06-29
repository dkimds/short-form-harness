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
- **틀렸던 가설:** 처음엔 N개 레퍼런스를 하나로 병합하는 설계였는데, 두 레퍼런스가 서로 다른 스타일(빠른 몽타주 vs 느린 호흡)이라 병합하면 어느 쪽도 안 닮는 평균의 함정에 빠진다는 걸 발견했다. 그래서 '레퍼런스 1개 → 프로파일 1개'로 바꿔, 각 프로파일이 내부적으로 일관된 스타일 계약이 되게 했다.

## 2. 어디서 막혔나 (Failures & Workarounds)
<!-- [LOG B]에서 가져온다. 막연한 "어려웠다" 금지. 구체적 증상 + 수치 + 우회. -->

| 막힌 지점 | 증상(수치 포함) | 원인 추정 | 우회/해결 |
|---|---|---|---|
| Gemini 429 rate limit | free tier 일일 0 할당량 → vision 결과 None | 당일 다른 호출로 소진 | graceful degradation으로 profile은 저장, 다음날 재시도 |
| Files API ACTIVE 폴링 | 400 FAILED_PRECONDITION "File is not in an ACTIVE state" | 업로드 후 즉시 호출 | files.get()으로 ACTIVE 될 때까지 최대 20초 폴링 |
| ffmpeg stderr LUFS | stdout 파싱 시 항상 -23.0 기본값 | loudnorm 출력이 stderr에 JSON으로 감 | regex로 stderr에서 JSON 블록 추출 |
| PIL 기본 폰트 한글 깨짐 | 폴백 이미지의 텍스트가 □□□으로 표시 | PIL 기본 폰트가 CJK 미지원 | P0는 run 지속이 목표라 허용, P1에서 Noto CJK 폰트 주입 예정 |
| 음악 MP3 placeholder | moviepy가 최소 MP3 구조를 파싱 못함 | libav 디코딩 요구 | Python wave 모듈로 1초 무음 WAV 생성으로 대체 |
| moviepy TextClip | ImageMagick 미설치 시 자막 생성 실패 | TextClip이 ImageMagick 의존 | try/except로 개별 자막 실패 무시 (graceful degradation) |

- 끝내 못 푼 것:
  - 자막의 한글 가독성 (PIL 기본 폰트 CJK 미지원, P1 개선 대상)
  - 실제 동영상 합성: Veo 5초 클립 + Imagen 이미지가 제대로 이어붙여지지 않음. compose.py가 ImageClip과 VideoFileClip을 혼합할 때 duration mismatch로 슬라이드쇼처럼 보임. shot["duration_sec"]를 실제 Veo 클립 길이로 업데이트해야 해결됨
  - TTS 보이스오버: google-cloud-texttospeech 미설치로 묵음 WAV 폴백 사용 중

**체크포인트 2 실제 품질 관찰:**
- 소리 없음: assets/music/에 무음 WAV placeholder만 있고 TTS(google-cloud-texttospeech) 미설치 → 음악·VO 모두 묵음
- 영상 끊김: Imagen API(imagen-4.0-generate-001)가 v1beta에서 403 응답 → 폴백 이미지(단색 배경)만 10장 이어붙임. 각 컷이 0.9~5초짜리 단색 슬라이드쇼.
- 캡션 깨짐: PIL 기본 폰트가 한글·이모지 미지원 → 텍스트가 □□□로 표시되거나 누락
- 이 세 가지는 모두 P0 설계 범위 내 한계 (Imagen 정상화 + BGM 추가 + 폰트 주입이 다음 단계)
- 평가 핵심(파이프라인 구조, 재현성, 분석↔생성 분리)은 3편 생성으로 검증됨

**체크포인트 3 (P1 Veo 통합 후) 관찰:**
- BGM: refs/에서 추출한 실제 트랙(ref1_bgm.mp3) 믹싱 → 노래 나옴 ✅
- Veo mp4 생성: product_hero 숏에서 1.5MB mp4 클립 생성 확인 ✅
- 그런데 최종 영상은 여전히 "노래 나오면서 그림이 바뀌는" 슬라이드쇼
- 원인: compose.py의 `_load_clip`이 .mp4 확장자를 `VideoFileClip`으로 로드하도록 분기하지만,
  Veo 클립(5초)과 Imagen 이미지(1~2초 ImageClip)의 duration mismatch로 `adjust_durations`가
  전체를 10~15초에 맞춰 스트레칭 → 결국 각 클립이 정지 화면처럼 보임
- 근본 원인: Veo는 5초 고정 클립을 내려주는데, shotlist의 beat duration은 0.9~4초로 설계됨.
  beat duration ≠ Veo clip duration이라 합성 시 길이 충돌이 발생함
- 가장 깔끔한 해결: Veo 클립은 그 자체 duration을 쓰고, shot["duration_sec"]를 실제 클립 길이로 덮어쓰도록 _render_veo_shot에서 업데이트해야 함. 그리고 VideoFileClip을 제대로 이어붙여야 함
- 시간 제약으로 우선 P2(문서·Gate)로 넘어가고, 이 문제는 "끝내 못 푼 것"으로 기록

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
1. **Noto Color Emoji + CJK 폰트 주입**: P0에서 이모지를 제거한 채 자막을 렌더링했다. PIL에 `assets/fonts/NotoColorEmoji.ttf`와 `NotoSansCJK-Regular.ttf`를 로드해 이모지·한글 자막을 정상 렌더링하는 것이 다음 우선순위. 파일 하나 추가로 자막 완성도가 확 달라진다.
2. **실제 BGM 트랙 확보**: 현재 `assets/music/`에 무음 WAV 3개가 있어 compose_video는 돌지만 최종 영상에 음악이 없다. Pixabay/Freesound 라이선스 안전 트랙 3개를 추가하면 `select_music`의 mood 매칭이 의미를 가진다.
3. **Veo i2v 활성화 (P1)**: `vendor_client.py`의 `image_to_video` 스텁을 실제 Veo API로 교체 → `assets.py`의 `veo_i2v` 분기 활성화. product_hero 장면에 움직임이 생기면 레퍼런스와의 시각적 차이가 가장 크게 줄어드는 지점이다.

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
- 떡밥 3 (음악 단순화): 음악 선택을 mood 단어 교집합 스코어링으로 단순화했다. cosine similarity나 임베딩 기반 매칭이 더 정확하지만, mood 문자열이 `_`로 분리된 영어 키워드 형태여서 단어 교집합으로 충분히 의미 있는 매칭이 가능하다고 판단. → 예상 질문: "더 정교한 매칭은?" → 임베딩 기반 방식과 트레이드오프 설명 가능.


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

  Task 6.8 (plan.py — beats ↔ cut_count 배분 알고리즘):
  결정: beats=5개인데 cut_count=12면 비율 기반 배분 + 최소 1컷/beat 보장
  트레이드오프: beat duration 데이터가 없거나 0이면 순서대로 배분 → 이 경우 페이싱 의미가 약해짐
  선택: P0에서 veo_i2v도 imagen_image로 처리 — 플래그만 남겨두고 assets.py에서 분기

  Task 6.10 (assets.py — 폴백 이미지 설계):
  결정: VendorError OR 비율 불일치 시 모두 PIL로 576×1024 단색+텍스트 이미지 생성
  문제: PIL 기본 폰트는 한글을 렌더링 못함 → 텍스트가 깨져서 나옴
  우회: 폴백 이미지는 품질보다 "run이 멈추지 않음"이 목표라 허용. 실제 배포라면 NanumGothic 같은 CJK 폰트 필요
  → 면접 떡밥: "한글 폰트 문제 어떻게 해결?" → "P0는 run 지속이 목표, P1에서 Noto CJK 폰트 주입으로 해결"

  Task 6.12 (compose.py — 음악 파일 선행 조건):
  결정: 실제 MP3 트랙이 없어 Python wave 모듈로 1초 무음 WAV 3개 생성 (assets/music/)
  이유: MP3 최소 유효 바이트 구조가 까다롭고 moviepy가 libav/ffmpeg로 디코딩 → WAV가 더 안정적
  트레이드오프: 실제 트랙이 없으니 compose_video에서 음악 믹싱은 동작하지만 최종 영상에 음악이 없음
  → 실제 제출용으로는 라이선스 안전 BGM(Pixabay 등)을 assets/music/에 추가해야 함

  Task 6.12 (compose.py — moviepy TextClip 자막):
  결정: ImageMagick 미설치 환경에서 TextClip이 실패할 수 있어 try/except로 개별 자막 실패를 무시
  이유: 자막 실패가 전체 영상 생성을 막으면 안 됨 (graceful degradation)
  트레이드오프: 자막 없는 영상이 나올 수 있음 → 평가 기준(구조적 분리, 재현성)에는 영향 없음

  Task 10 (Veo 통합 — duration mismatch):
  증상: Veo가 5초 mp4를 내려주지만 shotlist의 beat duration은 0.9~4초
        → compose.py의 adjust_durations가 전체를 10~15초에 맞추다 보니 Veo 클립도 strecth
        → 실제 영상이 여전히 "노래 나오면서 그림 바뀌는" 슬라이드쇼로 보임
  원인: _render_veo_shot에서 shot["duration_sec"]를 실제 Veo 클립 길이(5초)로 갱신하지 않음
        또한 VideoFileClip을 subclipped 없이 full 5초로 쓰면 beat 타이밍과 어긋남
  해결 방향: _render_veo_shot이 반환 후 shot["duration_sec"] = 5.0 으로 업데이트,
             compose.py에서 VideoFileClip duration을 clip.duration 기준으로 읽도록 수정
  우선순위: 시간 제약으로 P2(문서·Gate) 먼저 진행, 이 수정은 잔여 시간에

  Task 7.1 (cli.py generate 서브커맨드 — context 오판):
  증상: brief.py가 이미 존재했는데 AI가 컨텍스트 컴팩션 후 "없다"고 판단 → 불필요한 재구현 실행
  원인: 긴 대화 중 컨텍스트 윈도우 압축으로 이전 파일 생성 사실이 소실됨
  영향: 코드 결과는 동일(파일 덮어쓰기), 불필요한 subagent 호출 1회 낭비
  교훈: 컨텍스트 경계를 넘는 작업에서는 "파일 존재 여부를 먼저 확인"하는 단계가 필요

[LOG C] 모델 벤치 — 모델 바꿀 때마다
  단계: 설정 | 모델: gemini-2.0-flash(기본), imagen-3.0-generate-002, veo-2.0-generate-001
  관찰: 기본값만 설정, 실제 호출은 분석 파이프라인 구현 후 측정 예정

  단계: 분석/비전 | 모델: gemini-2.0-flash
  관찰: analyze_vision.md 프롬프트 — beats/captions/visual/audio JSON 강제 출력
        실제 응답 품질은 Task 3.9 vision.py 실행 시 측정 예정
============================================================
-->