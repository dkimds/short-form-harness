# 레퍼런스 분석 — biodance.json 도출 근거

## 레퍼런스 분석 개요

분석 대상: `refs/reference1.mp4`, `refs/reference2.mp4`
분석 도구: ffprobe(기술 메타), ffmpeg(컷 감지·LUFS), librosa(오디오), Gemini 2.5 Flash(비전)
산출물: `profiles/biodance.json` (`profile_id: 2982d2493118`)

---

## 기술 메타데이터

| 항목 | reference1 | reference2 |
|---|---|---|
| 해상도 | 576×1024 | 576×1024 |
| 종횡비 | 9:16 | 9:16 |
| FPS | 30 | 30 |
| 재생 시간 | 10.7초 | 12.2초 |

두 영상 모두 TikTok 세로 규격(9:16, 30fps)을 준수한다. `format.duration_sec_range`는 두 값을 포함하는 `[10.7, 12.2]`로 도출했다.

---

## 설계 결정 — 병합 가설 파기

분석 초기의 가설은 "두 레퍼런스를 하나의 프로파일로 병합한다"였다. `analyze --refs ref1.mp4 ref2.mp4 --out biodance.json`처럼 N개 레퍼런스를 받아 공통 스타일을 추출하는 구조다.

그러나 페이싱 수치를 실제로 뽑아보니 이 가설이 틀렸음을 확인했다.

- ref1: ~12컷, ~0.9초/컷 → `fast_montage`
- ref2: ~4컷, ~2.4초/컷 → `slow_hold`

병합하면 `avg_shot_len_sec ≈ 1.5초`, `cut_count ≈ 8`이 나오는데, 이 값으로 생성한 영상은 ref1처럼 빠르지도 ref2처럼 느리지도 않은 어색한 중간값이 된다. 이것이 **"평균의 함정"** 이다.

결론: 두 레퍼런스는 같은 브랜드의 서로 다른 스타일이므로, 각각 독립 프로파일로 분리해야 각 스타일이 온전히 재현된다. `analyze`는 `--ref` 단수 인수로 변경해 레퍼런스 1개 → 프로파일 1개를 강제했다.

이 결정은 코드 구조에도 반영됐다. `synthesize_profile.py`에는 `merge_pacing()` 같은 다중 레퍼런스 병합 함수가 없다. CLI도 `--ref`(단수)만 받는다.

---



컷 타임스탬프를 ffmpeg 씬 감지로 추출한 결과:

| 지표 | reference1 | reference2 |
|---|---|---|
| 컷 수 | ~12컷 | ~4컷 |
| 평균 숏 길이 | ~0.9초/컷 | ~2.4초/컷 |
| 리듬 성격 | fast_montage | slow_hold |

두 레퍼런스는 리듬이 정반대다. ref1은 제품 질감을 빠르게 보여주는 몽타주 스타일이고, ref2는 피부 결과를 천천히 드러내는 호흡 스타일이다.

이 차이가 "두 레퍼런스를 하나로 병합하지 않는다"는 설계 결정의 핵심 근거다. 병합하면 0.9초와 2.4초의 중간값(~1.5초)이 나오는데, 이는 두 스타일 어디에도 해당하지 않는 '평균의 함정'이다. 대신 두 프로파일을 독립적으로 유지하고, 공통 범위(`cut_count_range: [4, 12]`)와 `rhythm_mode: mixed`로 표현해 생성 시 샘플링 공간을 확보했다.

`hook_cut_density: high`는 두 영상 모두 0~3초 구간에 컷이 집중되어 있음을 반영한다.

---

## 비트 시트 패턴

Gemini 비전 분석이 추출한 `narrative.beats` 순서:

| 타임코드 | role | 설명 |
|---|---|---|
| 0.0 – 0.9s | `hook` | 제품 질감 클로즈업 — 즉각적 시선 포착 |
| 0.9 – 2.2s | `application` | 얼굴에 미스트 적용 장면 |
| 2.2 – 3.7s | `result_glow` | 미스트 후 피부 글로우 결과 |
| 3.7 – 4.6s | `product_hero` | 제품 보틀 클로즈업 |
| 4.6 – 6.8s | `application` | 손에 제품 덜어 눈가 적용 |
| 6.8 – 9.9s | `result_glow` | 전체 페이스 글로우 + 표정 마무리 |

`hook → product_hero → application → result_glow` 순환 구조가 이 스타일의 서사 골격이다. 생성 단계는 이 beat sheet를 기준으로 숏리스트를 구성한다.

---

## 자막 슬롯 패턴

`captions.slots`에는 3종류의 슬롯이 정의되어 있다:

- **title_hook** (`top_center`, `is_hook: true`, 0~4초): 영상 첫 4초를 점유하는 메인 훅 텍스트. `✨` 이모지 포함, 반굵은(semibold) 화이트 소프트 쉐도우 스타일.
- **subtitle_product** (`top_center`, `is_hook: false`, 0~4초): 제품명을 훅 아래에 서브라인으로 표시.
- **rolling_caption** (`lower_third` / `bottom_center`, `is_hook: false`, 0~9.9초): 하단 롤링 캡션 4개 슬롯. 각각 0초·4초·5초·7초에 등장해 제품 설명과 CTA를 차례로 전달.

자막 슬롯은 **무슨 말을 할지가 아니라 어디에·언제·어떤 스타일로 뜨느냐**만 규정한다. 실제 문구는 생성 단계의 훅 생성기와 브리프가 채운다.

---

## 오디오 패턴

| 항목 | reference1 | reference2 |
|---|---|---|
| 음악 시작 | ~0초 | ~1초 후 인(in) |
| 목표 LUFS | -23 | -23 |
| 보이스오버 | 없음(비전 분석 결과 없음) | 없음 |

`music_start_sec: 0.046`은 ref1 기준으로 도출되었다. `target_lufs: -23`은 두 영상의 loudnorm 측정 평균값이다. `has_voiceover: true`는 Gemini가 음성 설명 흔적을 감지해 플래그를 세운 것으로, 실제 TTS 생성 여부는 생성 단계에서 결정된다. `music_mood: upbeat_light_kpop_inspired`는 Gemini 비전 분석 결과다.

---

## 비전 분석 결과

| 항목 | 값 |
|---|---|
| 색감(color_grade) | `warm_soft_aesthetic` |
| 조명(lighting) | `natural_window_soft` |
| 강조색(accent_color) | `#ED99BE` (소프트 핑크) |
| 촬영 배경(setting) | `home_interior_daylight` |
| 크리에이터 수 | 1명 |

비전 분석의 역할은 비주얼 키워드를 Imagen 프롬프트 컨텍스트로 전달하는 것이다. 색감·조명·강조색은 생성 단계의 장면 프롬프트에 자동으로 삽입된다.

---

## biodance.json 도출 근거 요약

| 필드 | 출처 | 도출 방법 |
|---|---|---|
| `format.resolution` | ffprobe | 두 영상 동일(576×1024) |
| `format.duration_sec_range` | ffprobe | [10.7, 12.2] |
| `pacing.cut_count_range` | ffmpeg 씬 감지 | ref1: ~12컷, ref2: ~4컷 → [4, 12] |
| `pacing.rhythm_mode` | cut_detect 분류 | 두 영상 리듬이 달라 `mixed` |
| `pacing.hook_cut_density` | 초반 3초 컷 밀도 계산 | 양쪽 모두 집중 → `high` |
| `audio.music_start_sec` | ffmpeg loudnorm | ref1 기준 ~0.046초 |
| `audio.target_lufs` | ffmpeg loudnorm | -23 LUFS |
| `audio.music_mood` | Gemini 비전 | `upbeat_light_kpop_inspired` |
| `narrative.beats` | Gemini 비전 | 6개 beat 자동 추출 |
| `captions.slots` | Gemini 비전 | 6개 슬롯(타이밍·앵커·스타일) |
| `visual.*` | Gemini 비전 | 색감·조명·강조색·배경 |

---

## 핵심 인사이트

> **스타일 = 비주얼이 아니라 구조다.**

ref1과 ref2는 같은 브랜드(biodance)의 영상이지만 컷 리듬이 정반대다. 그럼에도 두 영상이 "같은 스타일"처럼 느껴지는 이유는 비주얼 색감이 비슷해서가 아니라, **자막이 뜨는 위치·타이밍, 훅이 0초에 시작하는 구조, 훅→제품→적용→결과의 beat sheet 순서**가 동일하기 때문이다.

따라서 이 하네스가 style_profile에서 가장 무게를 두는 필드는 `pacing`, `narrative.beats`, `captions.slots`다. 새로운 브랜드 레퍼런스를 분석할 때도 이 세 구조만 바뀌면 완전히 다른 스타일의 영상이 만들어진다 — 코드 변경 없이.

---

## 구조적 인사이트 — pacing과 narrative는 서로 다른 것을 측정한다

`ref1.json`(`pacing.cut_count_range: [12, 12]`, `narrative.beats`: 7개)으로 생성해보니 Gate의 비전 판정이 실패했다:

```
"Visual Style Match: Fail - The video contains 5 distinct narrative scenes/cuts,
 which does not match the expected beat count of 7."
```

원인을 추적하니 `plan.py`의 설계 자체가 두 필드의 성격을 혼동하고 있었다. `cut_count_range`(12)를 총 컷 수로 샘플링한 뒤, 이를 7개 beat에 비율로 배분하면 일부 beat가 2~3컷을 받는다. 그런데 같은 beat 안의 컷들은 `_build_prompt_text()`가 만드는 프롬프트가 완전히 동일하므로, 실질적으로 "똑같은 장면"이 여러 번 반복될 뿐이다. AI 비전은 이걸 하나의 scene으로 병합해서 세므로, shotlist에 12개 샷이 있어도 distinct scene은 5개로 관측됐다.

**여기서 확인한 것: `pacing.cut_count_range`와 `narrative.beats`는 서로 다른 측정 축이다.**

- `cut_count_range`는 ffmpeg 씬 감지로 뽑은 **리듬 지표**다 — "이 레퍼런스는 몇 번 화면이 바뀌는가"라는 순수 관찰값.
- `narrative.beats`는 Gemini 비전이 뽑은 **서사 구조**다 — "이 레퍼런스가 어떤 의미 단위로 나뉘는가"라는 해석값.

두 값은 같은 영상에서 나왔지만 독립적으로 추출된 별개의 신호다(ref1 실측: beats 7개, 컷 12개 — 애초에 1:1이 아니었다). 그런데 생성 단계는 이 둘을 "총 컷 수를 beat에 배분한다"는 절차로 강제 결합했다. 리듬 지표를 서사 구조에 억지로 끼워 넣는 순간, 프롬프트가 없는 "빈 배분"이 생기고 그게 중복 프롬프트로 채워지면서 구조가 깨졌다.

**해결 방향은 결합을 끊는 것이었다.** `narrative.beats`를 유일한 샷 생성 출처로 삼아 1 beat = 1 shot으로 고정하고(`build_shotlist()`), `cut_count_range`는 생성에 관여하지 않는 분석 메타데이터로 남겼다. 레퍼런스의 빠른 리듬(12컷)을 실제로 재현하려면 beats 자체를 12개로 세분화해야 한다 — 즉 리듬을 표현하는 단위는 "같은 beat 안의 컷 수"가 아니라 "beat의 개수와 길이"여야 한다.

이건 앞선 "스타일 = 비주얼이 아니라 구조다"라는 핵심 인사이트의 연장선이다. narrative.beats가 스토리의 골격이라면, pacing은 그 골격을 얼마나 빠르게 넘기는지에 대한 별도의 관찰이지 골격을 쪼개는 규칙이 될 수 없다. 두 축을 분리해야 각자의 의미가 유지된다.

---

## 권장 항목 — 크리에이터(인물) 사진 참조

과제의 "권장 - 크리에이터(인물)" 항목을 검토하면서, 기존 구조의 공백을 하나 더 발견했다. `visual.creator_count`가 분석 단계에서 추출은 되지만 `plan.py`의 `_build_prompt_text()`가 이 필드를 전혀 쓰지 않았다. 인물 묘사는 순전히 사용자가 `--input` 텍스트로 적은 값("late-20s Korean woman...")에 의존했는데, 이는 "생성형 모델이 인물을 만든다"가 아니라 "사용자가 인물을 지정한다"에 가깝다.

이를 보완하기 위해 `--creator-photo`라는 별도 선택 입력을 추가했다. `--input`(제품/주제)과 완전히 독립적인 경로로, 있으면 Nano Banana의 `generate_content` 호출에 참조 이미지로 함께 전달해 hook·application 장면에서 인물 일관성을 유지한다. Veo는 그 결과 이미지를 움직이게만 하므로 인물 일관성은 이미지 생성 단계에서 확보되면 충분하다.

적용 범위를 hook·application으로 제한한 것은 임의 선택이 아니라 기존 `plan.py`의 `_VEO_ROLES` 경계(Veo 처리 대상)와 동일하게 맞춘 것이다 — 움직임이 있는 장면(사람이 직접 등장하는 장면)에만 인물 참조가 의미 있고, product_hero·result_glow 같은 정지 이미지 장면은 제품 클로즈업이 주 내용이라 인물 참조가 불필요하다.

설계상 이 입력은 필수가 아니라 선택이다. 사진이 없으면 기존처럼 텍스트 인물 묘사로 폴백한다.

---

## 권장 항목 — 배경(setting)

같은 검토 과정에서 "권장 - 배경"도 공백이었다. `visual.setting`(예: `home_interior_daylight_plant_background`)이 비전 분석에서 추출은 됐지만 `_build_prompt_text()`가 `color_grade`·`lighting`·`accent_color`만 Style 절에 넣고 setting은 조립하지 않았다.

`setting`을 Style에 얹기보다는 별도 `Setting:` 절로 분리했다. Style(색감·조명)과 Setting(물리적 환경)은 다른 축이라 — 예를 들어 "warm_soft_bright, natural_window_soft"는 어떤 배경에서도 적용될 수 있는 색·조명 처리이고, "home_interior_daylight_plant_background"는 그 처리가 일어나는 구체적 공간이다. 하나의 Style 문자열에 섞으면 Imagen/Nano Banana 프롬프트에서 어느 것이 색감 지시고 어느 것이 배경 지시인지 모호해진다.

snake_case 원본값(`home_interior_daylight_plant_background`)은 언더스코어를 공백으로 치환해 프롬프트에 넣는다 — Imagen 계열 모델은 자연어 문장을 기대하므로 snake_case 그대로 넣는 것보다 읽기 쉬운 형태가 이미지 생성 결과에 유리하다.

배경을 실행 시점에 바꿀 수 있게 `--background` 옵션도 추가했다. `--creator-photo`가 참조 이미지를 넣는 방식이었던 것과 달리, 배경은 텍스트 프롬프트 override로 충분하다고 판단했다 — `visual.setting`이 원래도 텍스트 필드였고, "배경만 바꾼 영상"을 만드는 목적에는 참조 이미지 없이 프롬프트 텍스트 교체가 가장 직접적인 방법이다. `profile.setdefault("visual", {})["setting"] = args.background`로 프로파일 dict를 override한 뒤 기존 `_build_prompt_text()` 경로를 그대로 태우므로, 별도 분기 없이 Setting 절 구현을 재사용한다.

---

## 재생시간 — 음악 길이에 맞춘다 (15초 하한은 권장, 필수 아님)

과제 스펙 재확인: "생성 영상의 길이/해상도는 자유. 단, 숏폼의 범주(15~60초, 세로 비율) 안에서 선택하세요"라는 문구에서 15초는 **권장값**이고 필수 제약이 아니다. 기존 `normalize_profile_duration()`은 이걸 필수 클램프로 취급해 10.7초짜리 원본을 항상 15초로 늘렸는데, 이 늘어난 5초를 채우기 위해 `compose.py`의 `_build_audio_track()`이 실제 추출 BGM(`ref1_bgm.mp3`, 10.74초, 레퍼런스에서 그대로 뽑은 멜로디 있는 곡)을 루프시켜야 했다. 실제 곡을 루프하면 이어붙이는 지점에서 이질감이 생긴다.

해결: `normalize_profile_duration()`에 `enforce_min` 플래그를 추가해 15초 하한 클램프를 선택적으로 끌 수 있게 했다(60초 상한은 항상 유지 — 이쪽은 안전장치). `cli.py`는 `--duration`을 명시하지 않으면 `get_music_duration()`으로 선택될 음악 트랙의 실제 길이(`music_start_sec` 오프셋 제외)를 계산해 그걸 target으로 쓰고 `enforce_min=False`로 넘긴다. 결과적으로 영상 길이가 음악 길이와 정확히 일치해 음악이 루프 없이 한 번만 재생되고 끝난다.

이 방식은 "재생시간은 자유"라는 스펙과 "레퍼런스에서 추출한 실제 BGM을 쓴다"는 기존 설계 결정(retro.md 참고) 둘 다를 만족시킨다 — 길이를 임의로 정하지 않고 이미 존재하는 근거(음악 실측 길이)에 맞춘 것이라 임의성이 없다.

세 영상(인물만 변경/배경만 변경/둘 다 변경)을 같은 프로파일로 만들면 `--duration`을 지정하지 않아도 셋 다 정확히 같은 음악 길이로 생성된다 — beats duration이 비율 스케일링되어 shotlist 총합이 항상 이 target과 일치하고, `compose.py`의 `adjust_durations()`가 최종적으로 강제한다.

---

## image-to-video 모델 교체 — Veo(preview) → Gemini Omni Flash

3편 생성을 실제로 돌리면서 Veo(`veo-3.1-fast-generate-preview`, 이후 `lite`도 시도)가 계속 `429 RESOURCE_EXHAUSTED`로 막혔다. 처음엔 "결제를 안 켜서 그런가" 의심했지만, 사용자가 이미 빌링을 켠 상태였다. Google 공식 문서(`ai.google.dev/gemini-api/docs/rate-limits`)를 확인하니 "실험 모델과 프리뷰 모델의 비율 제한이 더 엄격합니다"라는 문구가 있었고, 실제 이 API 키에서 `client.models.list()`로 조회한 결과 이 프로젝트에서 쓸 수 있는 Veo는 `veo-3.1-generate-preview`/`veo-3.1-fast-generate-preview`/`veo-3.1-lite-generate-preview` 셋 다 preview뿐이었다 — GA(veo-2.0, veo-3.0)는 Gemini API가 아니라 Vertex AI 전용이라 이 SDK(`google.genai.Client`, Gemini API 엔드포인트)로는 접근할 수 없다(실제로 `veo-2.0-generate-001`을 시도하니 404).

즉 이건 빌링 문제가 아니라 **Gemini API 경로로 접근 가능한 Veo 모델이 전부 preview이고, preview 모델의 RPM 할당량 자체가 이 프로젝트에서 거의 0에 가깝게 잡혀 있는** 상태였다. 재시도 백오프를 30초/60초로 늘리고 호출 간 최소 간격(20초)을 둬도, quota window 자체가 그보다 타이트하면 429가 반복된다.

해결책은 Veo가 아닌 **완전히 다른 모델로 image-to-video를 수행**하는 것이었다. `ai.google.dev/gemini-api/docs/video`에서 Gemini API가 동영상 생성에 Veo 외에 **Gemini Omni Flash**(`gemini-omni-flash-preview`)도 제공한다는 걸 확인했다. 이 모델은 `interactions.create()` API로 `video_config.task="image_to_video"`를 지정해 이미지→동영상 변환을 지원하고, Veo와는 **완전히 분리된 quota**를 쓴다. 실제로 동일 API 키로 스모크 테스트하니 문제없이 2.67MB mp4가 생성됐다(Veo는 매번 429).

`vendor_client.py`의 `image_to_video()`를 Veo의 `generate_videos`/`predictLongRunning` 폴링 방식에서 Omni Flash의 `interactions.create()` 방식으로 교체했다. `Config.veo_model` 필드명은 하위 호환을 위해 그대로 두고 기본값만 `gemini-omni-flash-preview`로 바꿨다 — 필드 의미가 "image-to-video 모델"로 넓어진 셈이지만, `.env`의 `VEO_MODEL` 변수명 변경까지 하면 파급이 커서 필드명은 유지하고 docstring으로 의미를 명확히 했다.

이 결정도 preview 모델을 preview 모델로 바꾼 것이라 근본적으로 안전하지 않을 수 있다는 걸 인지하고 있다. Veo 전용으로 만들어둔 안전장치(호출 간 최소 간격 20초, 실패 시 30s→60s 백오프)는 성격이 같아 그대로 유지했다.
