# Avatar contract addendum

This is the list WS6 must merge into `contract/mqtt.yaml` and the generated topic constants. WS11 does not edit those files. The machine-readable copy is `sentient/services/avatar_stream/schema/sc-v1-avatar-addendum.yaml`.

The approved stack design uses the prefix `sc/v1`. The renderer default `topicPrefix` is `sc/v1`. The open WS6 branch `ws/contract-brain` currently publishes `orin/v1`. If that prefix is what lands, set `topicPrefix` in the display config. The fields below are the same either way.

PCM does not go on MQTT. The display opens a WebSocket to `sc-avatar-stream` (port 8077). The first message is `{"type":"auth","role":"display","token":"..."}`. A bad token closes with code 4401. A turn is a text header (`turn_id`, `sample_rate`, `visemes`, `vtimes`, `vdurations`) followed by one binary frame of mono s16le PCM. `{"type":"cancel","turn_id":"..."}` stops that turn.

## turn/cancel

Add subscribers:

- `sc-avatar-display`
- `sc-avatar-stream`

Payload stays `turn_id`, `ts`, `source`, `reason`.

## tts/state

Add publisher `sc-avatar-display`.

Add subscriber `sc-speech`. The self-trigger guard and barge-in need the display speaker edges, not only the synthesis states.

Add enum values:

- `playback_start`
- `playback_end`

Both carry `turn_id`, `ts`, `source` (`sc-avatar-display`). Existing states (`queued`, `started`, `playing`, `completed`, `stopped`, `failed`) stay. The guard should arm on `playback_start` and release on `playback_end` for that `turn_id`.

## avatar/state

Add subscriber `sc-avatar-display`.

Add properties:

| Field | Type | Values |
| --- | --- | --- |
| mood | string | neutral, happy, angry, sad, fear, disgust, love, sleep |
| gaze | object | `x` and `y`, each from -1 to 1 |
| gesture | string | handup, index, ok, thumbup, thumbdown, side, shrug, namaste |

`state`, `emotion`, `turn_id`, `ts` and `source` stay. `emotion` is accepted as a mood alias when `mood` is absent.

## health

`sc-avatar-stream` can publish the existing `health/{service}` pattern as `sc/v1/health/avatar-stream`. No new health topic. Add the service name only if WS6 enumerates publishers.

## What sc-speech must call later

WS5 is not edited here. When it owns alignments it should open the ingest role and send the same turn header with `alignments: [{phoneme, num_samples}, ...]` plus the PCM frame. The stream maps phonemes before the display sees them.
