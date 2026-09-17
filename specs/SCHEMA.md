# Delivery profile schema

A profile is one JSON file in this directory describing one delivery
destination. Framecheck loads every `*.json` here at startup. Adding a
destination needs **no code change**: write the JSON, run the tests, open a PR.

A profile says two separate things, and Framecheck keeps them apart on purpose:

| Section | Read by | Answers |
| --- | --- | --- |
| `requirements` | the validator | "Does this file meet the spec?" |
| `target` | the encoder | "What should an export produce?" |

The validator never builds an FFmpeg command and the encoder never reads a
rule. That separation is what stops validation quietly disagreeing with what
was actually encoded.

---

## Top-level fields

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `id` | string | yes | Stable slug: lowercase letters, digits, `-`, `_`. Must be unique across `specs/`. Used in filenames and settings, so do not rename it casually. |
| `name` | string | yes | What the user sees, e.g. `"Streaming / CTV"`. |
| `category` | string | no | Grouping for the UI. Defaults to `"video"`. |
| `description` | string | no | One sentence explaining the destination. |
| `filename_tag` | string | no | Short code stamped into output filenames: `CTV` gives `FC_CTV-spot.mp4`. Keep it to a few characters — it sits in front of every deliverable's name. Defaults to the `id` when omitted. |
| `source_url` | string | no | Where the numbers came from, so the next person can check them. **Omit it rather than guessing a URL.** |
| `notes` | list of strings | no | Things a human should know that are not machine-checkable rules. |
| `requirements` | object | no | Field key -> rule. See below. |
| `target` | object | no | What an export should produce. See below. |

Any other top-level key is a load error.

---

## Requirements

Each entry maps a **canonical field key** to a rule object:

```json
"video.frame_rate": {"allowed": [23.976, 24, 25, 29.97, 30], "severity": "fail", "fixable": true}
```

### Rule keys

| Key | Type | Meaning |
| --- | --- | --- |
| `equals` | string or number | The value must match exactly. Strings compare case-insensitively. |
| `allowed` | list | The value must be one of these. |
| `min` | number | Inclusive lower bound. |
| `max` | number | Inclusive upper bound. |
| `preferred` | string or number | The ideal value. On its own it means "this exact value is wanted"; alongside `min`/`max` it is guidance only and is shown in the expectation text. |
| `tolerance` | number | Accepted `+/-` band around `preferred`, in the same units. Requires `preferred`. Used by loudness: `{"preferred": -24, "tolerance": 2}`. |
| `severity` | string | `fail`, `warning`, `manual` or `info`. Defaults to `fail`. |
| `fixable` | bool | True when an export can correct this. Drives the "fix on export" affordance. Defaults to false. |
| `manual` | bool | True when no machine can decide this. Always reports MANUAL_REVIEW. |
| `label` | string | Override the derived label. |
| `guidance` | string | Shown when the rule is not satisfied. Say what to do, not just what is wrong. |
| `unit` | string | Override the unit from the field table. Rarely needed. |

Combinations that are rejected at load time:

* `equals` with `allowed`
* `equals` or `allowed` with `min`/`max`
* `tolerance` without `preferred`
* `min` greater than `max`
* a non-manual rule with no comparison at all

If `label` is absent it is derived from the key: `video.codec` ->
"Video codec", `audio.sample_rate_hz` -> "Audio sample rate" (the unit suffix
is stripped because the unit is displayed separately).

### Severity, and the two rules that matter

**A preference is never a failure.** If a value is merely recommended, it must
use `"severity": "warning"`. `fail` means the destination will reject the file.
Getting this wrong makes the whole tool untrustworthy: a panel full of red for
things nobody will reject teaches people to ignore red.

**Anything a machine cannot determine must use `"manual": true`.** Disclaimers,
safe zones, brand approval, whether the creative is the right cut. Manual
checks always report MANUAL_REVIEW -- never PASS, never FAIL -- whatever is in
the file. A profile with no manual check is usually an incomplete profile.

| Severity | Status when unsatisfied |
| --- | --- |
| `fail` | FAIL |
| `warning` | WARNING |
| `manual` | MANUAL_REVIEW (always, satisfied or not) |
| `info` | NOT_APPLICABLE -- reported for context, never moves the verdict |

### Values that cannot be determined

The validator never silently passes an unknown:

* The rule cannot apply to this file at all -- an `audio.*` rule on a file with
  no audio stream -- reports **NOT_APPLICABLE**.
* The rule applies but nothing measured the value -- loudness before an
  analysis pass, a container that reports no bitrate -- reports
  **MANUAL_REVIEW**.

---

## Canonical field keys

These are the only keys the validator understands. Anything else is a load
error naming the file and the key.

| Key | Source | Unit |
| --- | --- | --- |
| `container` | format name. Matches if the requested name is among the formats ffprobe lists, so `"mp4"` matches `mov,mp4,m4a,3gp,3g2,mj2` | |
| `video.codec` | video stream codec, e.g. `h264` | |
| `video.profile` | codec profile, e.g. `High` | |
| `video.width` | pixels | px |
| `video.height` | pixels | px |
| `video.resolution` | `"1920x1080"` | |
| `video.aspect_ratio` | display aspect if the container reports one, otherwise reduced from width/height. `"16:9"` and `"1.778"` compare equal within 1% | |
| `video.frame_rate` | exact rational rate. Write decimals (`29.97`); they are parsed to `30000/1001` and compared exactly | fps |
| `video.frame_rate_mode` | `"cfr"` or `"vfr"`, inferred from the container's two reported rates | |
| `video.scan_type` | `"progressive"` or `"interlaced"`, from field order | |
| `video.bitrate_mbps` | video stream bitrate | Mb/s |
| `video.pixel_format` | e.g. `yuv420p` | |
| `audio.codec` | e.g. `aac` | |
| `audio.channels` | count | |
| `audio.sample_rate_hz` | e.g. `48000` | Hz |
| `audio.bitrate_kbps` | audio stream bitrate | kbps |
| `audio.loudness_lkfs` | integrated loudness from an analysis pass; MANUAL_REVIEW until measured | LKFS |
| `file.size_mb` | file size | MB |
| `file.duration_seconds` | container duration | s |
| `review.*` | anything a human must confirm. Always manual, whatever else the rule says | |

`review.*` keys are free-form: `review.disclaimer`, `review.safe_zones`,
`review.sound_off` are all valid. Pick a name that reads well in a checklist.

### Two fields with fixed behaviour

* **`video.frame_rate_mode`** -- a VFR verdict comes from container metadata,
  which is a heuristic, not proof. The check still reports FAIL when the
  profile requires CFR, and its guidance says the determination is heuristic
  and that packet analysis during conform will confirm it.
* **`video.aspect_ratio`** -- always reported as *not* fixable, whatever the
  JSON says. A transcode can scale and pad; it cannot reframe creative.
  The guidance recommends a version composed for the destination instead.

---

## Target

What an export should produce. Every key is optional; omitted keys take the
defaults shown.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `container` | string | `"mp4"` | |
| `output_extension` | string | `".mp4"` | |
| `video_codec` | string | `"h264"` | |
| `video_profile` | string | `"high"` | |
| `pixel_format` | string | `"yuv420p"` | |
| `width` | int | none | Omit to keep the source width. |
| `height` | int | none | Omit to keep the source height. |
| `aspect_ratio` | string | none | Omit when the destination takes native aspect. Framecheck pads to fit; it never cuts picture off the edges. |
| `frame_rate_behavior` | `"preserve_native_if_allowed"` or `"force"` | preserve | Preserve unless the destination genuinely demands one rate: resampling frame rate damages motion. |
| `allowed_frame_rates` | list of decimals | `[]` | Parsed to exact rationals. |
| `preferred_frame_rate` | decimal | none | Used when the source rate is not allowed, or with `force`. |
| `constant_frame_rate` | bool | `true` | |
| `video_bitrate_mbps` | number | none | Target average. |
| `video_bitrate_max_mbps` | number | none | Cap. |
| `audio.codec` | string | `"aac"` | |
| `audio.channels` | int | `2` | |
| `audio.sample_rate_hz` | int | `48000` | |
| `audio.bitrate_kbps` | int | `320` | |
| `audio.loudness_lkfs` | number | none | Omit to leave levels alone. |
| `audio.true_peak_db` | number | `-2.0` | |
| `faststart` | bool | `true` | Move the MP4 index to the front for web playback. |

Unknown keys in `target` or `target.audio` are load errors.

---

## Complete annotated example

```jsonc
{
  "id": "streaming_ctv",              // slug, unique, stable
  "name": "Streaming / CTV",          // shown in the UI
  "category": "video",
  "description": "Connected-TV and streaming ad delivery.",
  "source_url": "https://example.com/spec",   // omit if you do not have one
  "notes": ["Broadcast-safe mix expected."],  // human context, not a rule

  "requirements": {
    // Hard requirements: the destination rejects the file without them.
    "container":            {"equals": "mp4", "severity": "fail", "fixable": true},
    "video.codec":          {"equals": "h264", "severity": "fail", "fixable": true},
    "video.width":          {"equals": 1920, "severity": "fail", "fixable": true},
    "video.height":         {"equals": 1080, "severity": "fail", "fixable": true},

    // Aspect is never advertised as fixable: a transcode cannot reframe.
    "video.aspect_ratio":   {"equals": "16:9", "severity": "fail", "fixable": false},

    // Decimals in, exact rationals out: 29.97 becomes 30000/1001.
    "video.frame_rate":     {"allowed": [23.976, 24, 25, 29.97, 30], "severity": "fail", "fixable": true},
    "video.frame_rate_mode":{"equals": "cfr", "severity": "fail", "fixable": true},
    "video.scan_type":      {"equals": "progressive", "severity": "fail", "fixable": true},

    // A range with a preferred value: inside 15-30 passes, and the panel still
    // shows that 20 is preferred.
    "video.bitrate_mbps":   {"min": 15, "max": 30, "preferred": 20, "severity": "warning", "fixable": true},

    "audio.codec":          {"equals": "aac", "severity": "fail", "fixable": true},
    "audio.channels":       {"equals": 2, "severity": "fail", "fixable": true},
    "audio.sample_rate_hz": {"equals": 48000, "severity": "warning", "fixable": true},
    "audio.bitrate_kbps":   {"min": 192, "preferred": 320, "severity": "warning", "fixable": true},

    // preferred + tolerance: -26 to -22 passes, anything else warns.
    "audio.loudness_lkfs":  {"preferred": -24, "tolerance": 2, "severity": "warning", "fixable": true},

    // Manual: always MANUAL_REVIEW, never PASS, never FAIL.
    "review.disclaimer": {
      "manual": true,
      "label": "Legal disclaimer",
      "guidance": "Confirm required disclaimer is present, legible and correct."
    }
  },

  "target": {
    "container": "mp4", "output_extension": ".mp4",
    "video_codec": "h264", "video_profile": "high", "pixel_format": "yuv420p",
    "width": 1920, "height": 1080, "aspect_ratio": "16:9",
    "frame_rate_behavior": "preserve_native_if_allowed",
    "allowed_frame_rates": [23.976, 24, 25, 29.97, 30],
    "preferred_frame_rate": 29.97,
    "constant_frame_rate": true,
    "video_bitrate_mbps": 20, "video_bitrate_max_mbps": 30,
    "audio": {"codec": "aac", "channels": 2, "sample_rate_hz": 48000,
              "bitrate_kbps": 320, "loudness_lkfs": -24, "true_peak_db": -2.0},
    "faststart": true
  }
}
```

(JSON has no comments. Strip them before saving a real profile.)

---

## Contributing a profile

1. Copy the closest existing file in `specs/` and edit it. No Python changes
   are needed -- the loader picks up any `*.json` in this directory.
2. Use a new, unique `id`. A duplicate id is reported as a load error and the
   second file is ignored.
3. Cite `source_url` where the destination publishes its spec. If you cannot
   find one, leave it out; an invented URL is worse than none.
4. Mark preferences `"severity": "warning"`, hard rejections `"severity": "fail"`,
   and everything a human must eyeball `"manual": true`.
5. Run the tests:

   ```
   python -m pytest tests -q
   ```

   `tests/test_profile_schema.py` loads every file in this directory, so a
   malformed profile fails there with the file and key named.

A profile that fails to load does not stop Framecheck starting: it is skipped
and reported in the loader's error list. That is a safety net for users, not a
reason to ship a broken file.
