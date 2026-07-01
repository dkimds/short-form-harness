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
- 만든 것: 레퍼런스 mp4를 분석해 재사용 가능한 style_profile.json으로 분해하고(analyze), 그 프로파일 + 사용자 입력(제품/인물사진/배경텍스트)으로 새 숏폼 mp4를 생성하는(generate) 2단계 CLI 하네스. 훅은 시스템이 매번 다르게 생성, 인물은 참조 사진으로, 배경은 텍스트 override로 통제 가능.
- 핵심 판단(한 문장): 스타일의 본질은 비주얼이 아니라 pacing·narrative.beats·captions 슬롯 같은 "구조"이고, 분석↔생성을 JSON 하나로 완전히 분리해야 이 구조를 재사용할 수 있다.
- 도달 지점 / 한계: 분석→합성→Gate까지 파이프라인은 end-to-end로 완주하고 재현성(같은 시스템·다른 입력→다른 결과)도 검증됨. 그런데 실제 3편 생성에서 Gate FAIL을 못 뛰어넘었다 — Veo quota 문제로 image-to-video 모델을 Omni Flash로 교체하고, duration 하드코딩 버그(5.0초 고정 → 실측값 사용)를 코드 레벨에서는 고쳤지만, 그 수정을 반영한 재생성·재판정까지는 시간 안에 완료하지 못했다. beat count(7 기대 vs 5 관측) 불일치도 원인 진단은 됐지만 해결은 다음 과제로 남긴다.

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
- **틀렸던 가설 (2차):** `pacing.cut_count_range`(리듬 지표, 예: 12)를 `narrative.beats`(서사 구조, 예: 7개)에 비율로 배분하면 "빠른 리듬 + 서사 구조"를 동시에 만족시킬 거라 가정했다(Task 6.8). 실제로 돌려보니 같은 beat 안에 배분된 여러 컷이 전부 동일한 프롬프트를 재사용해서 사실상 같은 장면의 반복이었고, Gate의 비전 판정이 "distinct scene 5개 vs 기대 beat 7개"로 실패했다(`outputs/20260702_002342_82989f/gate.json`). 두 필드는 같은 영상에서 나왔지만 서로 다른 축(리듬 관찰값 vs 서사 해석값)이라 하나를 다른 하나에 억지로 배분하는 절차 자체가 틀렸다. `_distribute_cuts()`를 제거하고 1 beat = 1 shot으로 고정, `cut_count_range`는 생성에 관여하지 않는 분석 메타데이터로 격리했다(`src/generate/plan.py`). 빠른 리듬을 실제로 재현하려면 beats 자체를 세분화해야 한다는 게 새 결론.

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
  - 완전한 동영상: 전체 숏 veo_i2v 설정 완료, 실제 생성은 시간 제약으로 미실행 (6~10분 소요)
  - TTS 보이스오버: google-cloud-texttospeech 미설치로 묵음 WAV 폴백 사용 중
  - **Gate PASS 자체** (아래 "체크포인트 4" 참고) — duration 버그는 코드는 고쳤지만 재판정까지 못 감
  - **beat count 불일치** (기대 7 vs 관측 5) — 원인은 짚었지만(정지 이미지 shot들이 비전 판정에서
    서로 구분 안 됨 추정) 해결책(beats 세분화 또는 shot별 프롬프트 variation)은 미구현
  - **인물 일관성의 "기본값" 설계** — `--creator-photo` 없이 `--background`만 주면 인물이 매 run마다
    임의로 바뀌는 문제(백인이 나온 사례)를 실제로 겪었다. `--creator-photo`를 강제하거나 기본
    인물 묘사 필드를 추가하는 방향까지 논의했지만 구현은 못 함

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

**체크포인트 4 (실전 3편 생성 시도 — 최종 상태) 관찰:**
- Veo(veo-3.1-*-preview)가 이 프로젝트에서 quota 소진 상태라 실제 429가 계속 발생 → Gemini
  Omni Flash(gemini-omni-flash-preview)로 image-to-video 모델을 교체(상세: LOG B "Task 최종").
  이후 429 없이 안정적으로 mp4 생성됨
- 인물 일관성: `--creator-photo`에 참조 이미지만 넘기고 "이 사람을 유지하라"는 지시문이 없어서
  모델이 참조 이미지를 스타일 참고 정도로만 취급 → 매 shot마다 다른 사람이 나오는 문제 발견.
  프롬프트에 명시적 지시문("Use the exact same person... keep face/identity consistent")을
  추가해 개선 확인
- 배경만 바꾸는 시나리오(`--background`만 주고 `--creator-photo` 없이 실행)에서 인물이 매번
  임의로 바뀌는 문제 발생(실제로 백인 인물이 나온 사례). 원인: `_build_prompt_text()`에 인물
  외형을 지정하는 필드가 전혀 없음 — `--input`이 우연히 인물 묘사 텍스트였을 때만 인물이 고정됐던
  것. `--creator-photo` 강제 또는 프로파일에 기본 인물 필드 추가 등 해결 방향은 논의했으나 시간
  부족으로 구현 못 함
- duration 버그 재발견: `_render_veo_shot()`이 Veo 시절 유산으로 `shot["duration_sec"] = 5.0`을
  무조건 덮어쓰고 있었음 — Gemini Omni Flash로 교체된 뒤에도 이 하드코딩이 남아 있어, 실제 결과
  mp4(13.03초)가 목표(음악 길이 ~10.7초)와 어긋나 Gate가 duration/cut_count 둘 다 FAIL
  (`outputs/20260702_045447_4823d9/gate.json`). `_measure_video_duration()`을 추가해 실제
  mp4 길이를 moviepy로 측정하고 그 값을 shot에 기록하도록 코드는 수정했지만, 이 수정을 반영한
  재생성·재판정까지는 시간 안에 완료하지 못함
- beat count 불일치(기대 7 vs 관측 5)는 이전 체크포인트(2)에서 겪은 문제와 다른 원인으로
  재발한 것으로 보임 — 이번엔 1 beat = 1 shot이 이미 적용된 상태였는데도 비전 판정이 5개로 봄.
  정지 이미지(product_hero·result_glow)들이 서로 비슷해 distinct scene으로 구분되지 않았을
  가능성이 있으나 확정 원인 진단은 못 함
- 결론: 파이프라인 자체는 끝까지 도는 것을 여러 번 확인했고 각 실패마다 원인을 구조적으로
  추적할 수 있었지만("어디서 왜 깨지는지 설명 가능"), 시간 안에 Gate PASS까지는 도달하지 못했다.
  이 하네스의 가치는 "한 번에 완벽한 영상"이 아니라 "실패해도 어디서 왜 깨졌는지 추적 가능한
  구조"에 있다고 판단해, 여기서 정리하고 제출한다.

## 3. 모델 실험 & 비교 (Model Bench)
<!-- [LOG C]에서. "제일 좋았던 것"만 쓰지 말고 왜 나머지는 탈락했는지 한 줄씩.
     트레이드오프를 말할 줄 아는 게 시니어 신호.
     아래는 현재 '디폴트로 설정한' 모델. 점수 칸은 실호출 후 측정해 채운다. -->

| 단계 | 후보 모델 | 일관성 | 속도 | 비용 | 스타일적합 | 채택? | 한 줄 사유 |
|---|---|---|---|---|---|---|---|
| 분석/비전 | gemini-2.5-flash | 안정적 | 보통 | 낮음 | 높음 | ✅ 최종 | mp4 video-native 입력, 분석↔gate 동일 모델로 판정 기준 일관 |
| 훅 생성 | gemini-2.5-flash | 의도적 비결정적 (temp=0.9) | 빠름 | 낮음 | - | ✅ 최종 | 분석과 같은 모델로 컨텍스트 일관, 매 run마다 다른 훅 필요 |
| 장면 이미지 | imagen-4.0-generate-001 | - | - | - | - | ❌ 탈락 | v1beta에서 403, 이후 실제 quota 문제로 완전 대체 |
| 장면 이미지 | gemini-2.5-flash-image (Nano Banana) | 참조 이미지 지시문 추가 후 양호 | 빠름 | Imagen과 별도 quota | 높음 | ✅ 최종 | generate_content 기반, Imagen 지원종료(2026-08-17) 예고에도 영향 없음 |
| 히어로 클립(i2v) | veo-3.1-fast/lite-generate-preview | - | - | - | - | ❌ 탈락 | 이 프로젝트에서 quota 소진 — 빌링 켜진 상태에서도 429 반복, preview 모델 RPM 제약 확인 |
| 히어로 클립(i2v) | gemini-omni-flash-preview | 안정적(429 없음) | 보통 | Veo와 분리된 quota | 양호 | ✅ 최종 | Veo와 별개 벤더 내부 모델. interactions.create(task=image_to_video)로 대체 |

- 최종 조합과 이유: 분석·훅·Gate 판정은 모두 gemini-2.5-flash로 통일해 "같은 모델이 분석하고 같은 모델이 채점"하는 일관성을 유지했다. 이미지·비디오 생성은 원래 Imagen·Veo로 계획했으나 실제 실행 중 둘 다 quota/가용성 문제에 부딪혀, 같은 벤더(Google) 안에서 quota가 분리된 대체 모델(Nano Banana, Gemini Omni Flash)로 교체했다 — "계획한 모델이 항상 쓸 수 있다고 가정하지 않고, 실패 시 즉시 대체재를 찾아 실행을 이어가는" 실전 대응이 이번 과제에서 가장 크게 드러난 역량이라고 본다.

## 4. 시간이 더 있었다면 (Next)
<!-- "품질 개선" 같은 막연한 말 금지. 구체적 다음 액션 + 시스템 사고가 드러나게. -->
1. **duration 수정 반영 재생성**: `_measure_video_duration()` 추가로 코드는 고쳤으니, 이걸 반영해 다시 3편을 생성하고 Gate가 duration/cut_count를 통과하는지 확인하는 게 최우선이다. 가장 적은 노력으로 가장 확실히 상태를 개선할 수 있는 항목.
2. **beat count 불일치 재현·수정**: product_hero·result_glow 같은 정지 이미지 shot들이 비전 판정에서 서로 구분되지 않는 게 원인이라는 가설을 검증하려면, 각 shot의 프롬프트에 variation(카메라 앵글·타이밍 차이)을 넣어 실제로 distinct scene 수가 늘어나는지 A/B로 확인해야 한다.
3. **인물 일관성의 기본 동작 설계**: `--creator-photo` 없이 `--background`만 줬을 때 인물이 매번 임의로 바뀌는 문제. `--background`를 줄 때 `--creator-photo`를 강제하는 CLI 검증, 또는 프로파일에 중립적인 기본 인물 묘사 필드를 추가하는 두 방향 중 하나를 실제로 구현해야 한다.
4. **Noto Color Emoji + CJK 폰트 주입**: P0에서 이모지를 제거한 채 자막을 렌더링했다. PIL에 `assets/fonts/NotoColorEmoji.ttf`와 `NotoSansCJK-Regular.ttf`를 로드해 이모지·한글 자막을 정상 렌더링하는 것이 다음 우선순위. 파일 하나 추가로 자막 완성도가 확 달라진다.
5. **실제 BGM 트랙 확보**: 현재는 레퍼런스에서 추출한 BGM(ref1_bgm.mp3)을 실제로 쓰고 있어 이 항목은 해결됐지만, 라이선스 안전성 검증(저작권 클리어런스)은 별도로 필요하다.
6. **Veo quota 정상화 또는 완전 대체 확정**: 지금은 Gemini Omni Flash로 우회했지만 이 역시 preview 모델이라 같은 리스크가 있다. 시간이 있다면 Veo GA(Vertex AI 경로) 접근을 별도로 구성하거나, Omni Flash의 실제 안정성을 더 많은 run으로 검증해야 한다.

## 5. 자기판정 Gate 설계 (가산점 — 진심으로 쓸 것)
<!-- 실제 구현 못 했어도 설계만 명확하면 점수 나온다. 결정적 체크 / 의미 체크로 나눠라. -->

- **결정적 체크:** (ffprobe·CV로 자동) 9:16 / 길이 범위 / 컷 수 / 음악 존재 / 자막 슬롯 픽셀 영역 텍스트 유무 → pass/fail
- **의미 체크:** 생성 final.mp4를 Gemini에 재입력 → style_profile의 genre·mood·beat 일치도 0~1 스코어. 분석과 동일 모델이라 기준 일관.
- **루프:** 임계값 미달 시 재생성(--retry N), 결과를 gate.json에 기록
- 한계/오탐 가능성:
  - 분석·판정이 같은 모델(gemini-2.5-flash)이라 같은 편향을 통과시킬 위험이 있다 — 예를 들어 이 모델이 특정 스타일 특징을 체계적으로 못 보면 분석 단계도 못 뽑고 Gate도 그 결함을 못 잡는다.
  - `cut_count` 결정적 체크는 실제 씬 감지가 아니라 `duration / avg_shot_len_sec` 나눗셈 추정값이다 — 실제로 duration 버그가 있을 때 이 추정값도 같이 틀어져서 "가짜 실패"가 겹쳐 나온 걸 직접 확인했다(`outputs/20260702_045447_4823d9/gate.json`: duration 13.03s FAIL + cut_count 15 FAIL이 같은 원인에서 파생). 결정적 체크라도 파생 지표는 원인이 하나여도 여러 항목이 동시에 FAIL로 보일 수 있어, Gate 결과를 볼 때 "실패 항목 개수"보다 "근본 원인이 몇 개인지"를 먼저 따져야 한다는 걸 실전에서 배웠다.
  - vision_judgment의 "beat count(narrative scenes) 불일치" 판정은 AI 비전의 주관적 씬 구분에 의존한다 — 같은 영상도 재판정 시 다른 값이 나올 수 있어(비결정적 모델) Gate 자체가 재현 가능한 결과를 보장하지 않는다.

---

## (의도적) 면접 떡밥
<!-- retro 본문 곳곳에 "이건 의도적으로 단순화했다 / 의도적으로 선택했다"를 1~2개 심어라.
     면접관이 그걸 물으면 준비된 답이 나오고, 대화를 네 강점으로 끌고 온다. -->
- 떡밥 1 (SDK 선택): `google-generativeai`(deprecated) 대신 신 SDK `google-genai` 채택. → 예상 질문: "왜 신 SDK?" → 답: deprecated 경고 + future-proof + Files API 통합이 mp4 업로드(분석 단계)에 자연스러움.
- 떡밥 2 (단일 벤더 lock-in): Gemini 단일 벤더 → vendor_client.py 한 곳에 격리해 OpenAI/Runway 교체 가능하게 추상화. → 예상 질문: "lock-in 위험?" → 답 준비됨.
- 떡밥 3 (음악 단순화): 음악 선택을 mood 단어 교집합 스코어링으로 단순화했다. cosine similarity나 임베딩 기반 매칭이 더 정확하지만, mood 문자열이 `_`로 분리된 영어 키워드 형태여서 단어 교집합으로 충분히 의미 있는 매칭이 가능하다고 판단. → 예상 질문: "더 정교한 매칭은?" → 임베딩 기반 방식과 트레이드오프 설명 가능.
- 떡밥 4 (image/video 입력 단순화): `--input`이 text/image/video 세 종류를 받지만, 실제로는 셋 다 동일한 파이프라인(Imagen→Veo i2v)을 타고 image/video는 파일 내용을 읽지 않고 파일명(stem)만 프롬프트의 Subject로 치환한다(`plan.py`의 `product_subject` 추출, `hook_gen.py`도 동일). 원래 설계는 이미지 입력 시 Imagen 대신 image-to-image로 그 이미지를 직접 활용하고, 영상 입력 시 프레임 추출 등 별도 처리를 하는 것이었으나, 제출 기한 안에서 우선순위가 아니라고 판단해 세 입력을 "프롬프트 텍스트 소스"로 통일했다. → 예상 질문: "이미지 넣으면 그 이미지가 실제로 쓰이나요?" → 답: 아니요, 현재는 파일명만 텍스트로 활용하는 단순화된 구현이고, image-to-image/영상 프레임 추출은 다음 우선순위로 명확히 알고 있다(트레이드오프 인지 후 시간 제약으로 스코프 축소).
- 떡밥 5 (벤더 모델 가용성 리스크를 실전에서 흡수): 계획한 두 모델(Imagen, Veo) 모두 실제 실행 중 문제(403, 429 quota 소진)에 부딪혔는데, 둘 다 완전히 새 벤더로 갈아타지 않고 "같은 벤더(Google) 안의 다른 모델"로 대체했다(Imagen→Nano Banana, Veo→Gemini Omni Flash). `vendor_client.py`에 모든 Google API 호출을 격리해뒀던 설계(떡밥 2)가 이 교체를 인터페이스 변경 없이 가능하게 했다. → 예상 질문: "Veo quota 문제를 어떻게 해결했나?" → 문서(rate-limits)에서 "preview 모델은 제한이 더 엄격함"을 확인하고, `client.models.list()`로 실제 접근 가능한 모델을 조회해 대체재(Omni Flash)를 찾은 진단 과정을 설명 가능. GA 모델(veo-2.0)은 Vertex AI 전용이라 이 SDK 경로로 404가 난 것도 함께 설명 가능.


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
  [번복 — 2026-07-02] 이 배분 로직이 실제 Gate 실패의 원인으로 드러남 (ref1.json: beats 7개,
  cut_count 12 → 같은 beat에 2~3컷 배분 → 동일 프롬프트 반복 → 비전이 5 distinct scene으로 병합
  → "beat count 7 불일치"로 Gate FAIL). _distribute_cuts() 제거하고 1 beat = 1 shot 고정으로 교체.
  cut_count_range는 생성에서 손을 뗀 분석 메타데이터로 남김. 상세: analysis.md "구조적 인사이트" 섹션.

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

  Task 10 (Veo — 전체 숏 동영상화 결정):
  관찰: product_hero만 veo_i2v로 처리하면 나머지 숏이 정지 이미지 → "화장품에 액체만 채워지는" 영상
  결정: plan.py에서 모든 role의 asset_type을 veo_i2v로 설정 → 전체 숏이 동영상 클립
  트레이드오프: Veo 호출 6회 × (생성 ~30초 + 폴링 ~30초) ≈ 6~10분 생성 시간
               하지만 완전한 동영상이 나오는 유일한 방법
  현재 상태: plan.py 수정 완료, 실제 생성 실행은 시간 제약으로 스킵 → 11(Gate)로 전환
  → 면접 떡밥: "모든 숏 Veo화의 비용·시간 트레이드오프?" → 히어로만 Veo, 나머지 Ken Burns로 절충안 설명 가능

  Task 최종 (Veo → Gemini Omni Flash 교체, 실전 3편 생성 중):
  증상: 실제로 3편을 생성하려 하니 Veo(veo-3.1-fast-generate-preview)에서 매 image_to_video
        호출마다 429 RESOURCE_EXHAUSTED. 재시도 백오프를 1s→2s→4s에서 30s→60s로 늘리고
        shot 간 최소 20초 간격을 둬도 계속 재발. lite 모델(veo-3.1-lite-generate-preview)로
        바꿔도 몇 번 성공 후 다시 429.
  원인 진단 과정: 처음엔 "빌링 안 켜서 그런가" 의심 → 사용자가 이미 빌링 켠 상태 확인 →
        공식 rate-limits 문서에서 "실험/프리뷰 모델은 비율 제한이 더 엄격함" 확인 →
        client.models.list()로 이 프로젝트가 실제 접근 가능한 Veo 모델 조회 → 3개 전부
        preview(generate/fast-generate/lite-generate)뿐. GA인 veo-2.0-generate-001을
        시도하니 404 — Gemini API가 아니라 Vertex AI 전용이라 이 SDK 경로로는 접근 불가.
        즉 빌링 문제가 아니라 "이 API 경로의 Veo는 전부 preview이고 preview quota가
        이 프로젝트에서 거의 0"이라는 구조적 제약.
  해결: Gemini API가 Veo 외에 Gemini Omni Flash(gemini-omni-flash-preview)라는 별도
        동영상 생성 모델을 제공하고, interactions.create(video_config.task="image_to_video")
        로 image-to-video를 지원한다는 걸 공식 문서에서 발견. Veo와 완전히 분리된 quota를
        쓴다. 실제 스모크 테스트로 정상 동작 확인(2.67MB mp4 생성) 후 vendor_client.py의
        image_to_video()를 Veo의 generate_videos/predictLongRunning 폴링 방식에서
        Omni Flash의 interactions.create() 방식으로 전면 교체.
  트레이드오프: Config.veo_model 필드명은 하위 호환을 위해 유지했지만 실제로는 "Veo 모델"이
        아니라 "image-to-video 모델" 전반을 가리키게 됨 — 필드명과 실제 의미가 어긋나는
        기술 부채. .env의 VEO_MODEL 변수명까지 바꾸면 파급이 커서 docstring으로만 명확히 함.
  남은 위험: Omni Flash도 preview 모델이라 근본적으로 같은 종류의 quota 리스크가 있음.
        다만 지금까지는 Veo와 달리 429 없이 안정적으로 동작.
  → 면접 떡밥: "왜 Veo 대신 Omni Flash?" → preview 모델의 RPM 할당량이 실제로 사용
        불가능한 수준이었고, 같은 벤더(Google) 안에서 quota가 분리된 대체 모델을 찾아
        교체한 실전 트러블슈팅 사례로 설명 가능.

  Task 7.1 (cli.py generate 서브커맨드 — context 오판):
  증상: brief.py가 이미 존재했는데 AI가 컨텍스트 컴팩션 후 "없다"고 판단 → 불필요한 재구현 실행
  원인: 긴 대화 중 컨텍스트 윈도우 압축으로 이전 파일 생성 사실이 소실됨
  영향: 코드 결과는 동일(파일 덮어쓰기), 불필요한 subagent 호출 1회 낭비
  교훈: 컨텍스트 경계를 넘는 작업에서는 "파일 존재 여부를 먼저 확인"하는 단계가 필요

  Task 12.1 (재사용성 통합 테스트 — compose/gate 제외):
  결정: `_run_full_pipeline`에서 `compose_video`와 `run_gate`를 제외하고 brief→shotlist 범위만 테스트
  이유: compose_video는 moviepy + ffmpeg 인코딩을 실제로 실행하는데, 테스트 환경에서
        ffmpeg 경로·코덱 의존성으로 깨질 수 있어 의존성 없이 돌아가는 범위만 선택
  트레이드오프: "다른 입력 → 다른 final.mp4"는 파일 내용 레벨에서 자동 검증되지 않음.
               brief["user_input"]과 shotlist["shots"][0]["prompt"]가 다름을 확인하는 수준.
  개선 방향: compose_video를 MagicMock으로 patch하고 빈 mp4 파일을 직접 만들어 경로를 반환하게 하면
             final.mp4까지 포함한 완전한 통합 테스트가 가능함 (시간 제약으로 미구현)

[LOG C] 모델 벤치 — 모델 바꿀 때마다
  단계: 설정 | 모델: gemini-2.0-flash(기본), imagen-3.0-generate-002, veo-2.0-generate-001
  관찰: 기본값만 설정, 실제 호출은 분석 파이프라인 구현 후 측정 예정

  단계: 분석/비전 | 모델: gemini-2.0-flash
  관찰: analyze_vision.md 프롬프트 — beats/captions/visual/audio JSON 강제 출력
        실제 응답 품질은 Task 3.9 vision.py 실행 시 측정 예정
============================================================
-->