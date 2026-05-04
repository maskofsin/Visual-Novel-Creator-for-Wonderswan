#!/usr/bin/env python3
"""
gen_ambient_sfx.py — generate WonderSwan Color-friendly ambient SFX.

Outputs 8-bit unsigned mono WAV @ 12 kHz and updates a WSC VN Studio JSON
to embed the generated WAVs as data:audio/wav;base64,... assets.

Example:
  /ucrt64/bin/python tools/gen_ambient_sfx.py \
    --json ../json/MyGame.wscvn.json \
    --out  ../Assets/SFX
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import random
import wave
from dataclasses import dataclass
from pathlib import Path


RATE = 4000


def _clamp_u8(x: float) -> int:
    if x <= 0.0:
        return 0
    if x >= 255.0:
        return 255
    return int(x + 0.5)


def _lowpass(samples: list[float], cutoff_hz: float, rate: int) -> list[float]:
    if cutoff_hz <= 0:
        return [0.0] * len(samples)
    dt = 1.0 / rate
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    a = dt / (rc + dt)
    y = 0.0
    out: list[float] = []
    for x in samples:
        y = y + a * (x - y)
        out.append(y)
    return out


def _highpass(samples: list[float], cutoff_hz: float, rate: int) -> list[float]:
    if cutoff_hz <= 0:
        return samples[:]
    dt = 1.0 / rate
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    a = rc / (rc + dt)
    y = 0.0
    x_prev = 0.0
    out: list[float] = []
    for x in samples:
        y = a * (y + x - x_prev)
        x_prev = x
        out.append(y)
    return out


def _mix(*tracks: list[float]) -> list[float]:
    n = min(len(t) for t in tracks) if tracks else 0
    out = [0.0] * n
    for t in tracks:
        for i in range(n):
            out[i] += t[i]
    return out


def _env_exp(n: int, rate: int, tau_s: float) -> list[float]:
    if tau_s <= 0:
        return [0.0] * n
    out = [0.0] * n
    for i in range(n):
        t = i / rate
        out[i] = math.exp(-t / tau_s)
    return out


def _noise(n: int, rng: random.Random) -> list[float]:
    return [(rng.random() * 2.0 - 1.0) for _ in range(n)]


def _sine(n: int, rate: int, hz: float, phase0: float = 0.0) -> list[float]:
    w = 2.0 * math.pi * hz / rate
    return [math.sin(phase0 + w * i) for i in range(n)]


def _chirp(n: int, rate: int, hz0: float, hz1: float, phase0: float = 0.0) -> list[float]:
    # Linear frequency sweep.
    out = [0.0] * n
    phase = phase0
    for i in range(n):
        t = i / max(1, n - 1)
        hz = hz0 + (hz1 - hz0) * t
        phase += 2.0 * math.pi * hz / rate
        out[i] = math.sin(phase)
    return out


def _place_add(dst: list[float], start: int, src: list[float], gain: float = 1.0) -> None:
    n = min(len(src), max(0, len(dst) - start))
    for i in range(n):
        dst[start + i] += src[i] * gain


def _to_wav_u8(samples: list[float], rate: int) -> bytes:
    # Map float [-1..1-ish] to unsigned 8-bit.
    pcm = bytearray(len(samples))
    for i, s in enumerate(samples):
        pcm[i] = _clamp_u8(128.0 + (s * 110.0))
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(rate)
        w.writeframes(bytes(pcm))
    return bio.getvalue()


@dataclass(frozen=True)
class SfxSpec:
    id: str
    filename: str
    seconds: float


SFX_SPECS: list[SfxSpec] = [
    SfxSpec("sfx_rain_light", "rain_light.wav", 4.5),
    SfxSpec("sfx_rain_heavy", "rain_heavy.wav", 6.0),
    SfxSpec("sfx_wind_soft", "wind_soft.wav", 4.5),
    SfxSpec("sfx_wind_gust", "wind_gust.wav", 6.0),
    SfxSpec("sfx_steps_wood", "steps_wood.wav", 3.0),
    SfxSpec("sfx_steps_gravel", "steps_gravel.wav", 3.0),
    SfxSpec("sfx_birds_morning", "birds_morning.wav", 4.0),
    SfxSpec("sfx_crickets_night", "crickets_night.wav", 4.0),
    SfxSpec("sfx_river_stream", "river_stream.wav", 6.0),
    SfxSpec("sfx_thunder_distant", "thunder_distant.wav", 4.5),
]


def _gen_rain(seconds: float, rng: random.Random, heavy: bool) -> list[float]:
    n = int(seconds * RATE)
    base = _noise(n, rng)
    # Bright-ish hiss + some low rumble (rate=4 kHz, Nyquist=2 kHz).
    hiss = _highpass(_lowpass(base, 1900.0, RATE), 450.0, RATE)
    rumble = _lowpass(base, 80.0, RATE)
    out = _mix([x * (0.35 if heavy else 0.22) for x in hiss],
               [x * (0.12 if heavy else 0.07) for x in rumble])
    # Add droplet ticks.
    drops = [0.0] * n
    drop_rate = 16.0 if heavy else 9.0  # drops per second
    count = int(seconds * drop_rate)
    for _ in range(count):
        t0 = rng.random() * max(0.0, seconds - 0.03)
        i0 = int(t0 * RATE)
        dur = int((0.010 if heavy else 0.008) * RATE)
        env = _env_exp(dur, RATE, 0.004 if heavy else 0.0035)
        tick = _highpass(_noise(dur, rng), 600.0, RATE)
        for i in range(dur):
            drops[i0 + i] += tick[i] * env[i] * (0.85 if heavy else 0.7)
    out = _mix(out, drops)
    return [max(-1.2, min(1.2, x)) for x in out]


def _gen_wind(seconds: float, rng: random.Random, gusty: bool) -> list[float]:
    n = int(seconds * RATE)
    base = _noise(n, rng)
    whoosh = _lowpass(base, 200.0 if gusty else 150.0, RATE)
    whoosh = _highpass(whoosh, 15.0, RATE)
    # Slow amplitude motion.
    lfo = _sine(n, RATE, 0.10 if gusty else 0.07, phase0=rng.random() * 6.28)
    out = [whoosh[i] * (0.35 + 0.25 * (0.5 + 0.5 * lfo[i])) for i in range(n)]

    # Occasional gust envelopes.
    if gusty:
        for _ in range(max(1, int(seconds * 1.2))):
            t0 = rng.random() * max(0.0, seconds - 0.6)
            i0 = int(t0 * RATE)
            dur = int(0.55 * RATE)
            env = [min(1.0, (i / (0.12 * RATE))) * math.exp(-(i / RATE) / 0.30) for i in range(dur)]
            g = _lowpass(_noise(dur, rng), 260.0, RATE)
            _place_add(out, i0, [g[i] * env[i] for i in range(dur)], gain=0.7)

    return [max(-1.2, min(1.2, x)) for x in out]


def _gen_steps(seconds: float, rng: random.Random, gravel: bool) -> list[float]:
    n = int(seconds * RATE)
    out = [0.0] * n
    step_count = 6
    for k in range(step_count):
        t0 = (k + 0.6) * (seconds / (step_count + 0.6))
        i0 = int(t0 * RATE)
        dur = int(0.28 * RATE)
        env = [math.exp(-(i / RATE) / 0.12) for i in range(dur)]
        thump = _lowpass(_noise(dur, rng), 160.0, RATE)
        click = _highpass(_noise(dur, rng), 600.0, RATE)
        crunch = _highpass(_lowpass(_noise(dur, rng), 1900.0, RATE), 500.0, RATE)
        for i in range(dur):
            s = 0.55 * thump[i] + 0.20 * click[i]
            if gravel:
                s += 0.35 * crunch[i]
            out[i0 + i] += s * env[i]
    # Bed ambience.
    bed = _lowpass(_noise(n, rng), 140.0, RATE)
    out = _mix(out, [x * 0.07 for x in bed])
    return [max(-1.2, min(1.2, x)) for x in out]


def _gen_birds(seconds: float, rng: random.Random) -> list[float]:
    n = int(seconds * RATE)
    out = [0.0] * n
    # Background air.
    air = _highpass(_lowpass(_noise(n, rng), 1900.0, RATE), 800.0, RATE)
    out = _mix(out, [x * 0.06 for x in air])

    chirps = 9
    for _ in range(chirps):
        t0 = rng.random() * max(0.0, seconds - 0.35)
        i0 = int(t0 * RATE)
        dur = int((0.18 + rng.random() * 0.12) * RATE)
        hz0 = 700.0 + rng.random() * 500.0
        hz1 = 1200.0 + rng.random() * 650.0
        sig = _chirp(dur, RATE, hz0, hz1, phase0=rng.random() * 6.28)
        env = [math.sin(math.pi * (i / max(1, dur - 1))) ** 1.7 for i in range(dur)]
        _place_add(out, i0, [sig[i] * env[i] for i in range(dur)], gain=0.55)
    return [max(-1.2, min(1.2, x)) for x in out]


def _gen_crickets(seconds: float, rng: random.Random) -> list[float]:
    n = int(seconds * RATE)
    out = [0.0] * n
    # Tiny background noise.
    bed = _highpass(_lowpass(_noise(n, rng), 1900.0, RATE), 1000.0, RATE)
    out = _mix(out, [x * 0.03 for x in bed])

    # Chirp pulses around 1.5–1.8 kHz (safe under Nyquist=2 kHz).
    pulse_hz = 1500.0 + rng.random() * 300.0
    p = _sine(int(0.022 * RATE), RATE, pulse_hz, phase0=rng.random() * 6.28)
    env = [math.sin(math.pi * (i / max(1, len(p) - 1))) for i in range(len(p))]
    pulse = [p[i] * env[i] for i in range(len(p))]

    rate_hz = 3.8
    t = 0.25
    while t < seconds - 0.05:
        i0 = int(t * RATE)
        _place_add(out, i0, pulse, gain=0.55)
        # Slightly irregular spacing.
        t += (1.0 / rate_hz) * (0.85 + 0.4 * rng.random())
    return [max(-1.2, min(1.2, x)) for x in out]


def _gen_stream(seconds: float, rng: random.Random) -> list[float]:
    n = int(seconds * RATE)
    base = _noise(n, rng)
    smooth = _lowpass(base, 420.0, RATE)
    bright = _highpass(_lowpass(base, 1900.0, RATE), 900.0, RATE)
    out = _mix([x * 0.22 for x in smooth], [x * 0.12 for x in bright])

    # Add bubble pops.
    pops = [0.0] * n
    for _ in range(int(seconds * 7.0)):
        t0 = rng.random() * max(0.0, seconds - 0.08)
        i0 = int(t0 * RATE)
        dur = int(0.06 * RATE)
        env = _env_exp(dur, RATE, 0.02)
        pop = _chirp(dur, RATE, 550.0 + rng.random() * 220.0, 250.0, phase0=rng.random() * 6.28)
        _place_add(pops, i0, [pop[i] * env[i] for i in range(dur)], gain=0.25)
    out = _mix(out, pops)
    return [max(-1.2, min(1.2, x)) for x in out]


def _gen_thunder(seconds: float, rng: random.Random) -> list[float]:
    n = int(seconds * RATE)
    out = [0.0] * n
    # Main rumble.
    base = _noise(n, rng)
    rumble = _lowpass(base, 55.0, RATE)
    env = [0.0] * n
    for i in range(n):
        t = i / RATE
        # Slow attack then long decay.
        a = min(1.0, t / 0.25)
        env[i] = a * math.exp(-t / (1.9 if seconds >= 3.5 else 1.4))
    out = _mix(out, [rumble[i] * env[i] * 0.70 for i in range(n)])

    # A sharp crack early.
    crack_start = int(0.18 * RATE)
    crack_dur = int(0.11 * RATE)
    crack_env = _env_exp(crack_dur, RATE, 0.03)
    crack = _highpass(_noise(crack_dur, rng), 700.0, RATE)
    _place_add(out, crack_start, [crack[i] * crack_env[i] for i in range(crack_dur)], gain=0.55)

    return [max(-1.2, min(1.2, x)) for x in out]


def generate_sfx_pcm(spec: SfxSpec, rng: random.Random) -> list[float]:
    if spec.id == "sfx_rain_light":
        return _gen_rain(spec.seconds, rng, heavy=False)
    if spec.id == "sfx_rain_heavy":
        return _gen_rain(spec.seconds, rng, heavy=True)
    if spec.id == "sfx_wind_soft":
        return _gen_wind(spec.seconds, rng, gusty=False)
    if spec.id == "sfx_wind_gust":
        return _gen_wind(spec.seconds, rng, gusty=True)
    if spec.id == "sfx_steps_wood":
        return _gen_steps(spec.seconds, rng, gravel=False)
    if spec.id == "sfx_steps_gravel":
        return _gen_steps(spec.seconds, rng, gravel=True)
    if spec.id == "sfx_birds_morning":
        return _gen_birds(spec.seconds, rng)
    if spec.id == "sfx_crickets_night":
        return _gen_crickets(spec.seconds, rng)
    if spec.id == "sfx_river_stream":
        return _gen_stream(spec.seconds, rng)
    if spec.id == "sfx_thunder_distant":
        return _gen_thunder(spec.seconds, rng)
    raise ValueError(f"unknown sfx id: {spec.id}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, help="Path to .wscvn.json to update")
    ap.add_argument("--out", required=True, help="Output directory for WAV files")
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args()

    json_path = Path(args.json)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)

    wav_by_id: dict[str, bytes] = {}
    for spec in SFX_SPECS:
        pcm = generate_sfx_pcm(spec, rng)
        wav = _to_wav_u8(pcm, RATE)
        wav_by_id[spec.id] = wav

        out_path = out_dir / spec.filename
        out_path.write_bytes(wav)

    proj = json.loads(json_path.read_text(encoding="utf-8"))
    assets = proj.setdefault("assets", {})
    sfx_assets = []
    for spec in SFX_SPECS:
        wav = wav_by_id[spec.id]
        data_url = "data:audio/wav;base64," + base64.b64encode(wav).decode("ascii")
        sfx_assets.append(
            {
                "id": spec.id,
                "name": os.path.splitext(spec.filename)[0],
                "dataUrl": data_url,
                "origName": spec.filename,
                "size": len(wav),
            }
        )
    assets["sfx"] = sfx_assets

    # Clear any node references to removed SFX ids.
    sfx_ids = {s["id"] for s in sfx_assets}
    for n in proj.get("nodes", []) or []:
        if n.get("sfx") and n.get("sfx") not in sfx_ids:
            n["sfx"] = ""

    json_path.write_text(json.dumps(proj, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
