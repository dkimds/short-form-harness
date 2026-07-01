# Retrospective — 숏폼 영상 생성 Harness

## TL;DR
- 만든 것: 레퍼런스 mp4를 분석해 재사용 가능한 style_profile.json으로 분해하고(analyze), 그 프로파일 + 사용자 입력(제품/인물사진/배경텍스트)으로 새 숏폼 mp4를 생성하는(generate) 2단계 CLI 하네스. 훅은 시스템이 매번 다르게 생성, 인물은 참조 사진으로, 배경은 텍스트 override로 통제 가능.
- 핵심 판단: 스타일의 본질은 비주얼이 아니라 pacing·narrative.beats·captions 슬롯 같은 "구조"이고, 분석↔생성을 JSON 하나로 완전히 분리해야 이 구조를 재사용할 수 있다.
- 도달 지점 / 한계: 분석→합성→Gate까지 파이프라인은 end-to-end로 완주하고 재현성(같은 시스템·다른 입력→다른 결과)도 검증됨. 그런데 실제 3편 생성에서 Gate FAIL을 못 뛰어넘었다 — Veo quota 문제로 image-to-video 모델을 Omni Flash로 교체하고, duration 하드코딩 버그(5.0초 고정 → 실측값 사용)를 코드 레벨에서는 고쳤지만, 그 수정을 반영한 재생성·재판정까지는 시간 안에 완료하지 못했다. beat count(7 기대 vs 5 관측) 불일치도 원인 진단은 됐지만 해결은 다음 과제로 남긴다.

---

## 1. 가정과 가설

- **가설 1 (중심):** 스타일 = 비주얼이 아니라 구조다. → 근거: 같은 biodance인데 ref1 ≈ 0.9초컷(빠른 몽타주) vs ref2 ≈ 2.4초컷(느린 호흡)으로 리듬이 정반대. 비주얼은 거의 같은데 느낌이 다름 → 차이의 원인은 구조라 판단. → 설계: style_profile의 무게중심을 pacing/narrative/captions에 둠.
- **가설 2 (pacing은 단일 값이 아니라 분포):** ref1/ref2가 다르므로 평균 컷길이 하나로 못 박지 않고 분포에서 샘플링. → 이게 "같은 시스템 → 다른 결과"의 엔진.
- **가설 3 (자막 = 내용이 아니라 슬롯):** 자막이 이 스타일인 이유는 *무슨 말이냐*가 아니라 *어디에·언제·어떤 모양으로* 뜨느냐. → 문구는 비우고 슬롯(위치·타이밍·스타일)만 규정 → 메시지(가변)와 스타일(불변) 분리.
- **가설 4 (훅은 생성·비결정):** 훅이 가장 레버리지 큰 요소 → is_hook 플래그로 박아 생성 단계가 특별 취급, temperature↑로 재실행마다 달라지게.
- **메타:** 위 가설들을 머릿속이 아니라 스키마 필드로 외재화 → 반증 가능. 생성물이 어설프면 어느 필드의 가정이 틀렸는지 짚어 그 필드만 수정. (schema=가설 레지스트리, generate=실험, gate=검정)
- **틀렸던 가설:** 처음엔 N개 레퍼런스를 하나로 병합하는 설계였는데, 두 레퍼런스가 서로 다른 스타일(빠른 몽타주 vs 느린 호흡)이라 병합하면 어느 쪽도 안 닮는 평균의 함정에 빠진다는 걸 발견했다. 그래서 '레퍼런스 1개 → 프로파일 1개'로 바꿔, 각 프로파일이 내부적으로 일관된 스타일 계약이 되게 했다.
- **틀렸던 가설 (2차):** `pacing.cut_count_range`(리듬 지표, 예: 12)를 `narrative.beats`(서사 구조, 예: 7개)에 비율로 배분하면 "빠른 리듬 + 서사 구조"를 동시에 만족시킬 거라 가정했다(Task 6.8). 실제로 돌려보니 같은 beat 안에 배분된 여러 컷이 전부 동일한 프롬프트를 재사용해서 사실상 같은 장면의 반복이었고, Gate의 비전 판정이 "distinct scene 5개 vs 기대 beat 7개"로 실패했다(`outputs/20260702_002342_82989f/gate.json`). 두 필드는 같은 영상에서 나왔지만 서로 다른 축(리듬 관찰값 vs 서사 해석값)이라 하나를 다른 하나에 억지로 배분하는 절차 자체가 틀렸다. `_distribute_cuts()`를 제거하고 1 beat = 1 shot으로 고정, `cut_count_range`는 생성에 관여하지 않는 분석 메타데이터로 격리했다(`src/generate/plan.py`). 빠른 리듬을 실제로 재현하려면 beats 자체를 세분화해야 한다는 게 새 결론.

## 2. 어디서 막혔나 (Failures & Workarounds)

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
  - 완전한 동영상: 전체 숏 veo_i2v 설정 완료, 실제 생성은 시간 제약으로 미실행 (6~10분 소요)
  - TTS 보이스오버: google-cloud-texttospeech 미설치로 묵음 WAV 폴백 사용 중
  - **Gate PASS 자체** (아래 "체크포인트 4" 참고) — duration 버그는 코드는 고쳤지만 재판정까지 못 감
  - **beat count 불일치** (기대 7 vs 관측 5) — 원인은 짚었지만(정지 이미지 shot들이 비전 판정에서 서로 구분 안 됨 추정) 해결책(beats 세분화 또는 shot별 프롬프트 variation)은 미구현
  - **인물 일관성의 "기본값" 설계** — `--creator-photo` 없이 `--background`만 주면 인물이 매 run마다 임의로 바뀌는 문제(백인이 나온 사례)를 실제로 겪었다. `--creator-photo`를 강제하거나 기본 인물 묘사 필드를 추가하는 방향까지 논의했지만 구현은 못 함

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
- 원인: compose.py의 `_load_clip`이 .mp4 확장자를 `VideoFileClip`으로 로드하도록 분기하지만, Veo 클립(5초)과 Imagen 이미지(1~2초 ImageClip)의 duration mismatch로 `adjust_durations`가 전체를 10~15초에 맞춰 스트레칭 → 결국 각 클립이 정지 화면처럼 보임
- 근본 원인: Veo는 5초 고정 클립을 내려주는데, shotlist의 beat duration은 0.9~4초로 설계됨. beat duration ≠ Veo clip duration이라 합성 시 길이 충돌이 발생함
- 가장 깔끔한 해결: Veo 클립은 그 자체 duration을 쓰고, shot["duration_sec"]를 실제 클립 길이로 덮어쓰도록 _render_veo_shot에서 업데이트해야 함. 그리고 VideoFileClip을 제대로 이어붙여야 함
- 시간 제약으로 우선 P2(문서·Gate)로 넘어가고, 이 문제는 "끝내 못 푼 것"으로 기록

**체크포인트 4 (실전 3편 생성 시도 — 최종 상태) 관찰:**
- Veo(veo-3.1-*-preview)가 이 프로젝트에서 quota 소진 상태라 실제 429가 계속 발생 → Gemini Omni Flash(gemini-omni-flash-preview)로 image-to-video 모델을 교체. 이후 429 없이 안정적으로 mp4 생성됨
- 인물 일관성: `--creator-photo`에 참조 이미지만 넘기고 "이 사람을 유지하라"는 지시문이 없어서 모델이 참조 이미지를 스타일 참고 정도로만 취급 → 매 shot마다 다른 사람이 나오는 문제 발견. 프롬프트에 명시적 지시문("Use the exact same person... keep face/identity consistent")을 추가해 개선 확인
- 배경만 바꾸는 시나리오(`--background`만 주고 `--creator-photo` 없이 실행)에서 인물이 매번 임의로 바뀌는 문제 발생(실제로 백인 인물이 나온 사례). 원인: `_build_prompt_text()`에 인물 외형을 지정하는 필드가 전혀 없음 — `--input`이 우연히 인물 묘사 텍스트였을 때만 인물이 고정됐던 것. `--creator-photo` 강제 또는 프로파일에 기본 인물 필드 추가 등 해결 방향은 논의했으나 시간 부족으로 구현 못 함
- duration 버그 재발견: `_render_veo_shot()`이 Veo 시절 유산으로 `shot["duration_sec"] = 5.0`을 무조건 덮어쓰고 있었음 — Gemini Omni Flash로 교체된 뒤에도 이 하드코딩이 남아 있어, 실제 결과 mp4(13.03초)가 목표(음악 길이 ~10.7초)와 어긋나 Gate가 duration/cut_count 둘 다 FAIL(`outputs/20260702_045447_4823d9/gate.json`). `_measure_video_duration()`을 추가해 실제 mp4 길이를 moviepy로 측정하고 그 값을 shot에 기록하도록 코드는 수정했지만, 이 수정을 반영한 재생성·재판정까지는 시간 안에 완료하지 못함
- beat count 불일치(기대 7 vs 관측 5)는 이전 체크포인트(2)에서 겪은 문제와 다른 원인으로 재발한 것으로 보임 — 이번엔 1 beat = 1 shot이 이미 적용된 상태였는데도 비전 판정이 5개로 봄. 정지 이미지(product_hero·result_glow)들이 서로 비슷해 distinct scene으로 구분되지 않았을 가능성이 있으나 확정 원인 진단은 못 함
- 결론: 파이프라인 자체는 끝까지 도는 것을 여러 번 확인했고 각 실패마다 원인을 구조적으로 추적할 수 있었지만("어디서 왜 깨지는지 설명 가능"), 시간 안에 Gate PASS까지는 도달하지 못했다. 이 하네스의 가치는 "한 번에 완벽한 영상"이 아니라 "실패해도 어디서 왜 깨졌는지 추적 가능한 구조"에 있다고 판단해, 여기서 정리하고 제출한다.

## 3. 모델 실험 & 비교

| 단계 | 후보 모델 | 일관성 | 속도 | 비용 | 스타일적합 | 채택? | 한 줄 사유 |
|---|---|---|---|---|---|---|---|
| 분석/비전 | gemini-2.5-flash | 안정적 | 보통 | 낮음 | 높음 | ✅ 최종 | mp4 video-native 입력, 분석↔gate 동일 모델로 판정 기준 일관 |
| 훅 생성 | gemini-2.5-flash | 의도적 비결정적 (temp=0.9) | 빠름 | 낮음 | - | ✅ 최종 | 분석과 같은 모델로 컨텍스트 일관, 매 run마다 다른 훅 필요 |
| 장면 이미지 | imagen-4.0-generate-001 | - | - | - | - | ❌ 탈락 | v1beta에서 403, 이후 실제 quota 문제로 완전 대체 |
| 장면 이미지 | gemini-2.5-flash-image (Nano Banana) | 참조 이미지 지시문 추가 후 양호 | 빠름 | Imagen과 별도 quota | 높음 | ✅ 최종 | generate_content 기반, Imagen 지원종료(2026-08-17) 예고에도 영향 없음 |
| 히어로 클립(i2v) | veo-3.1-fast/lite-generate-preview | - | - | - | - | ❌ 탈락 | 이 프로젝트에서 quota 소진 — 빌링 켜진 상태에서도 429 반복, preview 모델 RPM 제약 확인 |
| 히어로 클립(i2v) | gemini-omni-flash-preview | 안정적(429 없음) | 보통 | Veo와 분리된 quota | 양호 | ✅ 최종 | Veo와 별개 벤더 내부 모델. interactions.create(task=image_to_video)로 대체 |

- 최종 조합과 이유: 분석·훅·Gate 판정은 모두 gemini-2.5-flash로 통일해 "같은 모델이 분석하고 같은 모델이 채점"하는 일관성을 유지했다. 이미지·비디오 생성은 원래 Imagen·Veo로 계획했으나 실제 실행 중 둘 다 quota/가용성 문제에 부딪혀, 같은 벤더(Google) 안에서 quota가 분리된 대체 모델(Nano Banana, Gemini Omni Flash)로 교체했다.

## 4. 시간이 더 있었다면 (Next)
1. **duration 수정 반영 재생성**: `_measure_video_duration()` 추가로 코드는 고쳤으니, 이걸 반영해 다시 3편을 생성하고 Gate가 duration/cut_count를 통과하는지 확인하는 게 최우선이다.
2. **beat count 불일치 재현·수정**: product_hero·result_glow 같은 정지 이미지 shot들이 비전 판정에서 서로 구분되지 않는 게 원인이라는 가설을 검증하려면, 각 shot의 프롬프트에 variation(카메라 앵글·타이밍 차이)을 넣어 실제로 distinct scene 수가 늘어나는지 A/B로 확인해야 한다.
3. **인물 일관성의 기본 동작 설계**: `--creator-photo` 없이 `--background`만 줬을 때 인물이 매번 임의로 바뀌는 문제. `--background`를 줄 때 `--creator-photo`를 강제하는 CLI 검증, 또는 프로파일에 중립적인 기본 인물 묘사 필드를 추가하는 두 방향 중 하나를 실제로 구현해야 한다.
4. **Noto Color Emoji + CJK 폰트 주입**: P0에서 이모지를 제거한 채 자막을 렌더링했다. PIL에 `assets/fonts/NotoColorEmoji.ttf`와 `NotoSansCJK-Regular.ttf`를 로드해 이모지·한글 자막을 정상 렌더링하는 것이 다음 우선순위.
5. **실제 BGM 트랙 확보**: 현재는 레퍼런스에서 추출한 BGM(ref1_bgm.mp3)을 실제로 쓰고 있어 이 항목은 해결됐지만, 라이선스 안전성 검증(저작권 클리어런스)은 별도로 필요하다.
6. **Veo quota 정상화 또는 완전 대체 확정**: 지금은 Gemini Omni Flash로 우회했지만 이 역시 preview 모델이라 같은 리스크가 있다. 시간이 있다면 Veo GA(Vertex AI 경로) 접근을 별도로 구성하거나, Omni Flash의 실제 안정성을 더 많은 run으로 검증해야 한다.

## 5. 자기판정 Gate 설계

- **결정적 체크:** (ffprobe·CV로 자동) 9:16 / 길이 범위 / 컷 수 / 음악 존재 / 자막 슬롯 픽셀 영역 텍스트 유무 → pass/fail
- **의미 체크:** 생성 final.mp4를 Gemini에 재입력 → style_profile의 genre·mood·beat 일치도 0~1 스코어. 분석과 동일 모델이라 기준 일관.
- **루프:** 임계값 미달 시 재생성(--retry N), 결과를 gate.json에 기록
- 한계/오탐 가능성:
  - 분석·판정이 같은 모델(gemini-2.5-flash)이라 같은 편향을 통과시킬 위험이 있다 — 예를 들어 이 모델이 특정 스타일 특징을 체계적으로 못 보면 분석 단계도 못 뽑고 Gate도 그 결함을 못 잡는다.
  - `cut_count` 결정적 체크는 실제 씬 감지가 아니라 `duration / avg_shot_len_sec` 나눗셈 추정값이다 — 실제로 duration 버그가 있을 때 이 추정값도 같이 틀어져서 "가짜 실패"가 겹쳐 나온 걸 직접 확인했다(`outputs/20260702_045447_4823d9/gate.json`: duration 13.03s FAIL + cut_count 15 FAIL이 같은 원인에서 파생). 결정적 체크라도 파생 지표는 원인이 하나여도 여러 항목이 동시에 FAIL로 보일 수 있어, Gate 결과를 볼 때 "실패 항목 개수"보다 "근본 원인이 몇 개인지"를 먼저 따져야 한다는 걸 실전에서 배웠다.
  - vision_judgment의 "beat count(narrative scenes) 불일치" 판정은 AI 비전의 주관적 씬 구분에 의존한다 — 같은 영상도 재판정 시 다른 값이 나올 수 있어(비결정적 모델) Gate 자체가 재현 가능한 결과를 보장하지 않는다.