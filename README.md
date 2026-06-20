# EZVIZ Open API — Home Assistant integration

Adds your EZVIZ / Hik-Connect cameras and video doorbells (calling panels) to
Home Assistant as **camera entities** using the official **EZVIZ Open Platform
API**. The live stream URL (HLS/RTMP/FLV) is **fetched fresh every time the stream
starts**, so the short-lived cloud URLs never go stale.

## Why this instead of the built-in `ezviz` integration?

The built-in integration uses the private app API and exposes **local RTSP** —
great on the LAN, but it can't pull video remotely. This one uses the **Open API**,
which returns a real cloud-relayed HLS/RTMP/FLV URL that works from anywhere
(and that ffmpeg/HA's stream component can ingest directly).

## Requirements

1. An EZVIZ Open Platform developer app. Create one (free) at
   <https://isgpopen.ezviz.com> → **Console → App Key Management** → copy the
   **AppKey** and **Secret**. (The website `open.ezviz.com` is dead — use
   `isgpopen.ezviz.com`.)
2. The credentials are tied to your EZVIZ account, so they see the same devices.
   If you registered a *separate* developer account, share the device to it first.

## Install via HACS

1. HACS → ⋮ → **Custom repositories** → add this repo, category **Integration**.
2. Install **EZVIZ Open API**, restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → EZVIZ Open API**.
4. Enter AppKey, Secret, pick your **Region** (EU for `apiieu` accounts), submit.

Each camera channel becomes a camera entity (e.g. the doorbell's *Main Door
Station* on channel 1). Add a Picture/Camera card and you get the live view.

## Options (gear icon on the integration)

| Option | Meaning |
|--------|---------|
| **Stream protocol** | `hls` (default, most compatible), `rtmp`, or `flv` |
| **Refresh interval** | how often the device/channel list is polled (default 120 s) |
| **Verify codes** | for *encrypted* devices, one per line: `SERIAL=CODE` |

## How the auto-refresh works

- Access token (appKey/secret → token) is cached in-memory and auto-renewed
  (valid ~7 days; refreshed on `10002`).
- The **live URL is requested on every stream (re)start** via
  `live/address/get`, so the ~30-minute URL expiry is invisible to you — HA just
  asks for a new one when it (re)connects.

## Limitations

- A device must be **online** to stream.
- Snapshots/thumbnails use the Open API `device/capture` endpoint (best-effort).
- Two-way talk / PTZ are not implemented (live video only) — could be added.

## Notes

Behind a TLS-intercepting corporate proxy you can untick *Verify TLS certificate*
during setup. On a normal home network leave it enabled.
