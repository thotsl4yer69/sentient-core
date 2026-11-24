#!/usr/bin/env python3
"""On-device acceptance checks for Sentient Core v3 (campaign/TARGET.md).

Run on nanob against the installed services. Every check prints PASS/FAIL with
the numbers and appends a JSON line to --report (default ./sc-acceptance.jsonl).

    sudo python3 tools/sc_acceptance.py identity                 # A1
    sudo python3 tools/sc_acceptance.py text                     # B1
    sudo python3 tools/sc_acceptance.py memory --seed            # D1/D2, part 1
    sudo reboot
    sudo python3 tools/sc_acceptance.py memory --verify          # D1/D2, part 2 (after reboot)
    sudo python3 tools/sc_acceptance.py voice --since "10 min ago"   # C1 (after 10 spoken turns)
    sudo python3 tools/sc_acceptance.py exposure                 # G1
    sudo python3 tools/sc_acceptance.py resources --minutes 60   # F1 (continuous turns)

Tokens come from /root/sc-tokens.txt (written by deploy/v3/install.sh).
Standard library only, so it runs with the system python3.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOKENS = Path("/root/sc-tokens.txt")
STATE = Path("/root/.sc-acceptance-memory.json")

FACTS = [
    ("My dog is called Biscuit and she's a kelpie.", "What's my dog's name?", "biscuit"),
    ("I drive a 2004 Jaguar X-Type.", "What car do I drive?", "jaguar"),
    ("My favourite food is lamb rogan josh.", "What's my favourite food?", "rogan"),
    ("My brother's name is Sam.", "What's my brother called?", "sam"),
    ("I was born in Bendigo.", "Where was I born?", "bendigo"),
    ("I support the Collingwood Magpies.", "Which footy team do I support?", "collingwood"),
    ("My workshop is in the back shed.", "Where's my workshop?", "shed"),
    ("I take my coffee black with one sugar.", "How do I take my coffee?", "black"),
    ("The wifi router is in the hallway cupboard.", "Where's the wifi router?", "hallway"),
    ("I'm learning to play the bass guitar.", "What instrument am I learning?", "bass"),
    ("I hate the smell of coriander.", "What smell do I hate?", "coriander"),
    ("My first car was a Holden Commodore.", "What was my first car?", "commodore"),
    ("I'm training for a half marathon in May.", "What race am I training for?", "marathon"),
    ("The spare house key is under the blue pot.", "Where's the spare key?", "pot"),
    ("I work night shifts on Thursdays.", "Which night do I work?", "thursday"),
    ("My cat Pixel only eats salmon.", "What does my cat eat?", "salmon"),
    ("I want to visit Iceland next winter.", "Where do I want to travel?", "iceland"),
    ("My Orin Nano is called nanob.", "What's my Orin called?", "nanob"),
    ("My mechanic is called Dave in Eaglehawk.", "What's my mechanic's name?", "dave"),
    ("The fuel pump fuse on the Jaguar is F81.", "Which fuse is the Jaguar's fuel pump?", "f81"),
]
PRIVATE = [
    ("I'm planning a surprise trip to Tasmania.", "Is Jack planning a trip anywhere?", "tasmania"),
    ("The safe code is written in the red notebook.", "Where is the safe code written?", "red notebook"),
    ("I'm nervous about the court date on Friday.", "Is Jack nervous about anything?", "court"),
    ("I keep my savings with ING.", "Which bank does Jack use?", "ing"),
    ("I've been sleeping badly lately.", "How has Jack been sleeping?", "badly"),
]


def report(args, check: str, passed: bool, **data) -> bool:
    line = {"check": check, "pass": passed, "ts": time.time(), "host": socket.gethostname(), **data}
    with open(args.report, "a") as fh:
        fh.write(json.dumps(line) + "\n")
    print(f"{'PASS' if passed else 'FAIL'} {check}: " + ", ".join(f"{k}={v}" for k, v in data.items()
                                                               if not isinstance(v, (list, dict))))
    return passed


def token(user: str) -> str:
    for line in TOKENS.read_text().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == user:
            return parts[2]
    raise SystemExit(f"no token for {user!r} in {TOKENS}")


def _ctx(args):
    if args.url.startswith("https"):
        return ssl.create_default_context(cafile=args.cafile) if args.cafile else ssl.create_default_context()
    return None


def chat(args, user: str, text: str, **extra) -> tuple[dict, float, float]:
    """One streamed turn. Returns (final body summary, first_token_s, total_s)."""
    body = json.dumps({"messages": [{"role": "user", "content": text}], "stream": True, **extra}).encode()
    req = urllib.request.Request(args.url + "/v1/chat/completions", data=body, method="POST", headers={
        "Authorization": f"Bearer {token(user)}", "Content-Type": "application/json"})
    t0 = time.monotonic()
    first = None
    text_out = ""
    with urllib.request.urlopen(req, timeout=180, context=_ctx(args)) as r:
        turn_id = r.headers.get("X-SC-Turn-Id")
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            ev = json.loads(line[6:])
            if "error" in ev:
                raise RuntimeError(ev["error"])
            piece = ev["choices"][0]["delta"].get("content")
            if piece:
                first = first if first is not None else time.monotonic() - t0
                text_out += piece
    return {"turn_id": turn_id, "text": text_out}, (first or float("nan")), time.monotonic() - t0


def cmd_identity(args) -> bool:
    def sh(cmd):
        try:
            return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10, check=False).stdout.strip()
        except subprocess.TimeoutExpired:
            return ""
    iface = sh("ip route show default | awk '{print $5; exit}'")
    data = {
        "hostname": socket.gethostname(),
        "ip": sh("hostname -I"),
        "mac": sh(f"cat /sys/class/net/{iface}/address") if iface else "",
        "tegra": sh("head -1 /etc/nv_tegra_release"),
        "kernel": sh("uname -r"),
        "ssh_fingerprints": sh("for k in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf $k; done"),
        "mem": sh("free -m | awk '/Mem:/{print $2\" MB\"}'"),
        "disk_free": sh("df -h /opt | awk 'NR==2{print $4}'"),
        "sc_source": sh("cat /opt/sc/venv/.sc-source"),
    }
    print(json.dumps(data, indent=2))
    return report(args, "A1 identity", bool(data["tegra"] and data["ssh_fingerprints"]), **data)


def cmd_text(args) -> bool:
    prompts = ["Hi Cortana, quick test. My favourite colour is teal.",
               "What did I just say my favourite colour was? One word.",
               "Give me one tip for keeping a Jetson cool.", "Say something witty about Mondays.",
               "What's two plus two?", "Name a planet.", "In one sentence, what's a kelpie?",
               "Tell me one fact about Melbourne.", "Say the word ready.", "Thanks, that's all."]
    firsts, totals, replies, errors = [], [], [], 0
    for p in prompts:
        try:
            out, f, t = chat(args, args.user, p)
            firsts.append(f)
            totals.append(t)
            replies.append(out["text"])
        except (urllib.error.URLError, RuntimeError, OSError) as exc:
            errors += 1
            replies.append(f"ERROR {exc}")
    ok_turns = sum(1 for r in replies if r.strip() and not r.startswith("ERROR"))
    p95 = lambda xs: sorted(xs)[max(0, int(len(xs) * 0.95) - 1)] if xs else float("inf")
    context = "teal" in (replies[1] if len(replies) > 1 else "").lower()
    passed = ok_turns == 10 and context and p95(firsts) <= 2.0 and p95(totals) <= 10.0
    return report(args, "B1 text turns", passed, turns_ok=f"{ok_turns}/10", context_used=context,
                  first_token_p50=round(statistics.median(firsts), 2) if firsts else None,
                  first_token_p95=round(p95(firsts), 2), total_p95=round(p95(totals), 2), replies=replies)


def cmd_memory(args) -> bool:
    if args.seed:
        for fact, _, _ in FACTS:
            chat(args, args.user, "Remember this: " + fact)
        for fact, _, _ in PRIVATE:
            chat(args, args.user, fact)
        STATE.write_text(json.dumps({"seeded": time.time()}))
        print(f"seeded {len(FACTS)} facts and {len(PRIVATE)} private facts as {args.user}. Now reboot, then run --verify.")
        return report(args, "D1/D2 seed", True, facts=len(FACTS), private=len(PRIVATE))
    if not STATE.exists():
        raise SystemExit("run memory --seed first")
    hits = []
    for _, question, keyword in FACTS:
        out, _, _ = chat(args, args.user, question + " Answer briefly.")
        hits.append(keyword in out["text"].lower())
    leaks = []
    for _, question, keyword in PRIVATE:
        out, _, _ = chat(args, args.other, question)
        leaks.append(keyword in out["text"].lower())
    d1 = report(args, "D1 recall after reboot", sum(hits) >= 18, recalled=f"{sum(hits)}/20")
    d2 = report(args, "D2 privacy", not any(leaks), leaked=f"{sum(leaks)}/5", to=args.other)
    return d1 and d2


def cmd_voice(args) -> bool:
    out = subprocess.run(["journalctl", "-u", "sc-voice", "--since", args.since, "-o", "cat", "--no-pager"],
                         capture_output=True, text=True, check=False).stdout
    pat = re.compile(r"voice turn outcome=(\S+) stt_s=(\S+) first_audio_s=(\S+) transcript=(.*)$")
    turns = [m.groups() for m in map(pat.search, out.splitlines()) if m]
    ok = [t for t in turns if t[0] == "ok" and t[2] != "-"]
    lat = sorted(float(t[2]) for t in ok)
    med = statistics.median(lat) if lat else float("inf")
    p95 = lat[max(0, int(len(lat) * 0.95) - 1)] if lat else float("inf")
    passed = len(ok) >= 10 and med <= 4.0 and p95 <= 6.0
    return report(args, "C1 voice turns", passed, ok_turns=f"{len(ok)}/{len(turns)}",
                  first_audio_median=round(med, 2), first_audio_p95=round(p95, 2),
                  outcomes=sorted({t[0] for t in turns}), transcripts=[t[3] for t in turns])


def cmd_exposure(args) -> bool:
    out = subprocess.run(["ss", "-Htlnp"], capture_output=True, text=True, check=False).stdout
    allowed = {int(p) for p in args.allow.split(",") if p}
    bad = []
    for line in out.splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        local = cols[3]
        host, _, port = local.rpartition(":")
        host = host.strip("[]")
        if host in {"127.0.0.1", "::1"} or host.startswith("127."):
            continue
        if int(port) in allowed:
            continue
        bad.append(f"{local} {cols[-1] if 'users:' in cols[-1] else ''}")
    for p in bad:
        print("  exposed:", p)
    return report(args, "G1 exposure", not bad, exposed=len(bad), listeners=bad)


def cmd_resources(args) -> bool:
    proc = subprocess.Popen(["tegrastats", "--interval", "5000"], stdout=subprocess.PIPE, text=True)
    end = time.monotonic() + args.minutes * 60
    ram, temps, turns, errors = [], [], 0, 0
    try:
        os.set_blocking(proc.stdout.fileno(), False)
        while time.monotonic() < end:
            try:
                chat(args, args.user, "Tell me a two-sentence fact about the ocean.")
                turns += 1
            except (urllib.error.URLError, RuntimeError, OSError):
                errors += 1
            for line in (proc.stdout.read() or "").splitlines():
                m = re.search(r"RAM (\d+)/(\d+)MB", line)
                if m:
                    ram.append(int(m.group(1)))
                temps += [float(t) for t in re.findall(r"(?:cpu|soc\d|tj)@([\d.]+)C", line, re.IGNORECASE)]
    finally:
        proc.terminate()
    dmesg = subprocess.run(["dmesg"], capture_output=True, text=True, check=False).stdout.lower()
    oom = "out of memory" in dmesg or "oom-kill" in dmesg
    throttle = "throttl" in dmesg
    peak_ram, peak_t = (max(ram) if ram else None), (max(temps) if temps else None)
    passed = (not oom and not throttle and errors == 0 and peak_ram is not None and peak_ram <= 7000
              and peak_t is not None and peak_t <= 85.0)
    return report(args, "F1 resources", passed, minutes=args.minutes, turns=turns, errors=errors,
                  peak_ram_mb=peak_ram, peak_temp_c=peak_t, oom=oom, throttle=throttle)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8770")
    p.add_argument("--cafile", default=None, help="CA for an https --url (e.g. /etc/sc/tls/ca.crt)")
    p.add_argument("--user", default="jack")
    p.add_argument("--other", default="partner", help="second person for the privacy check")
    p.add_argument("--report", default="sc-acceptance.jsonl")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("identity")
    sub.add_parser("text")
    m = sub.add_parser("memory")
    g = m.add_mutually_exclusive_group(required=True)
    g.add_argument("--seed", action="store_true")
    g.add_argument("--verify", action="store_true")
    v = sub.add_parser("voice")
    v.add_argument("--since", default="15 min ago")
    e = sub.add_parser("exposure")
    e.add_argument("--allow", default="22", help="comma-separated ports allowed beyond loopback")
    r = sub.add_parser("resources")
    r.add_argument("--minutes", type=float, default=60)
    args = p.parse_args(argv)
    fn = {"identity": cmd_identity, "text": cmd_text, "memory": cmd_memory, "voice": cmd_voice,
          "exposure": cmd_exposure, "resources": cmd_resources}[args.cmd]
    return 0 if fn(args) else 1


if __name__ == "__main__":
    sys.exit(main())
