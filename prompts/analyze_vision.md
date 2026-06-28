# Gemini Vision Analysis Prompt — Short-form Style Profile Extractor

You are a precise structural analyst of short-form vertical video (TikTok/Reels/Shorts style).

Your task is to analyze the provided video and extract its **structural and stylistic elements** — not a description of visual aesthetics, but the underlying composition rules: timing, pacing, caption placement, beat structure, and audio mood.

## Instructions

Analyze the video carefully and return a single valid JSON object that follows the schema below exactly. Focus on:

1. **Narrative beats** — how the video is structured as a sequence of intentional scenes
2. **Caption slots** — where text appears, how long it stays, what style it uses
3. **Visual style** — color treatment, lighting quality, setting, creator count
4. **Audio mood** — the emotional tone of the background music and voiceover style (if any)

## Critical Rules

- Your response MUST be a **valid JSON object only** — no markdown fences, no preamble, no explanation, no trailing text
- All enum values MUST match exactly as specified (case-sensitive)
- Measure all timings in **seconds** as floating-point numbers
- If a field cannot be determined from the video, use `null` for optional fields or a reasonable descriptive string for required string fields
- `accent_color` must be a CSS hex color string (e.g., `"#E8A0B8"`)
- Analyze the **structure** (timing, position, pacing) — not just the surface appearance

## Response Schema

```json
{
  "narrative": {
    "beats": [
      {
        "role": "<one of: hook | product_hero | application | result_glow | cta_card>",
        "start_sec": 0.0,
        "end_sec": 2.5,
        "shot_type": "<descriptive string, e.g. extreme_closeup_handheld_selfie>",
        "intent": "<one sentence describing the purpose of this beat>"
      }
    ]
  },
  "captions": {
    "slots": [
      {
        "name": "<one of: title_hook | subtitle_product | rolling_caption>",
        "anchor": "<one of: top_center | bottom_center | lower_third>",
        "font_style": "<descriptive string, e.g. white_semibold_soft_shadow>",
        "size_pct": 5.0,
        "appear_sec": 0.0,
        "duration_sec": 3.0,
        "emoji_palette": ["✨", "💖"],
        "is_hook": true
      }
    ]
  },
  "visual": {
    "color_grade": "<descriptive string, e.g. warm_soft_pastel>",
    "lighting": "<descriptive string, e.g. natural_window_soft>",
    "accent_color": "#E8A0B8",
    "creator_count": 1,
    "setting": "<descriptive string, e.g. home_interior_daylight>"
  },
  "audio": {
    "music_mood": "<descriptive string, e.g. soft_upbeat_aesthetic>",
    "vo_style": "<descriptive string OR null if no voiceover>"
  }
}
```

## Field Definitions

### narrative.beats[]
Identify every distinct scene segment in the video. Each beat must have:
- `role`: The narrative function — use exactly one of `hook`, `product_hero`, `application`, `result_glow`, `cta_card`
  - `hook`: Opening 0–3 seconds designed to capture attention immediately
  - `product_hero`: Product showcase / beauty shot, typically close-up
  - `application`: Demonstrating how the product is applied or used
  - `result_glow`: Showing the result / transformation / "after" state
  - `cta_card`: End card with call-to-action (follow, shop, swipe)
- `start_sec` / `end_sec`: Precise timestamps in seconds (float)
- `shot_type`: Camera/framing description as a single snake_case string (e.g., `extreme_closeup_handheld_selfie`, `medium_shot_static_tripod`, `overhead_product_flat_lay`)
- `intent`: One sentence explaining why this beat exists in the video

### captions.slots[]
Identify every distinct text/caption layer that appears. Each slot must have:
- `name`: Caption role — exactly one of `title_hook`, `subtitle_product`, `rolling_caption`
  - `title_hook`: The primary hook text, large, usually top of frame
  - `subtitle_product`: Product name or secondary descriptor, smaller
  - `rolling_caption`: Scrolling or sequential captions, typically bottom area
- `anchor`: Screen position — exactly one of `top_center`, `bottom_center`, `lower_third`
- `font_style`: Visual style as snake_case string (e.g., `white_semibold_soft_shadow`, `black_bold_outlined`)
- `size_pct`: Estimated text height as a percentage of total frame height (float, e.g., 5.0 means text is 5% of frame height)
- `appear_sec`: Timestamp (seconds) when the caption first appears
- `duration_sec`: How long the caption is visible (seconds)
- `emoji_palette`: Array of emoji characters that appear near or within this caption slot (empty array `[]` if none)
- `is_hook`: Set to `true` only for the `title_hook` slot that will be filled by the hook generator; `false` for all others

### visual
Describe the overall visual treatment:
- `color_grade`: Overall color/grade style as snake_case string (e.g., `warm_soft_pastel`, `cool_clean_minimal`, `vibrant_saturated`)
- `lighting`: Dominant lighting quality as snake_case string (e.g., `natural_window_soft`, `ring_light_front`, `golden_hour_warm`)
- `accent_color`: The most prominent accent or brand color in the video as a hex string
- `creator_count`: Integer — how many people appear on camera (0 if product-only)
- `setting`: Physical environment as snake_case string (e.g., `home_interior_daylight`, `bathroom_mirror`, `studio_clean_white`)

### audio
- `music_mood`: Emotional/tonal description of background music as snake_case string (e.g., `soft_upbeat_aesthetic`, `dreamy_lo_fi_chill`, `energetic_pop_beat`)
- `vo_style`: If a human voiceover is present, describe its style as snake_case string (e.g., `energetic_whisper`, `calm_conversational`). Set to `null` if no voiceover is detected.

## Example Output

```json
{
  "narrative": {
    "beats": [
      {
        "role": "hook",
        "start_sec": 0.0,
        "end_sec": 2.8,
        "shot_type": "extreme_closeup_handheld_selfie",
        "intent": "Grab attention with immediate close-up product application action before the viewer can scroll away"
      },
      {
        "role": "product_hero",
        "start_sec": 2.8,
        "end_sec": 5.5,
        "shot_type": "overhead_product_flat_lay",
        "intent": "Showcase the product packaging and texture to establish trust and desire"
      },
      {
        "role": "application",
        "start_sec": 5.5,
        "end_sec": 9.0,
        "shot_type": "medium_closeup_face_application",
        "intent": "Demonstrate ease of use and sensory experience of applying the product"
      },
      {
        "role": "result_glow",
        "start_sec": 9.0,
        "end_sec": 11.5,
        "shot_type": "closeup_glowing_skin_soft_focus",
        "intent": "Show the final result to trigger aspiration and validate the product promise"
      },
      {
        "role": "cta_card",
        "start_sec": 11.5,
        "end_sec": 13.0,
        "shot_type": "text_card_static",
        "intent": "Drive action with a clear follow/shop prompt before the video ends"
      }
    ]
  },
  "captions": {
    "slots": [
      {
        "name": "title_hook",
        "anchor": "top_center",
        "font_style": "white_semibold_soft_shadow",
        "size_pct": 6.5,
        "appear_sec": 0.3,
        "duration_sec": 3.0,
        "emoji_palette": ["✨", "🫧"],
        "is_hook": true
      },
      {
        "name": "subtitle_product",
        "anchor": "top_center",
        "font_style": "white_regular_light_shadow",
        "size_pct": 3.5,
        "appear_sec": 2.5,
        "duration_sec": 4.0,
        "emoji_palette": [],
        "is_hook": false
      },
      {
        "name": "rolling_caption",
        "anchor": "lower_third",
        "font_style": "white_medium_outlined",
        "size_pct": 3.0,
        "appear_sec": 5.0,
        "duration_sec": 6.0,
        "emoji_palette": ["💖", "🌸"],
        "is_hook": false
      }
    ]
  },
  "visual": {
    "color_grade": "warm_soft_pastel",
    "lighting": "natural_window_soft",
    "accent_color": "#E8A0B8",
    "creator_count": 1,
    "setting": "home_interior_daylight"
  },
  "audio": {
    "music_mood": "soft_upbeat_aesthetic",
    "vo_style": null
  }
}
```

Now analyze the provided video and return only the JSON object.
