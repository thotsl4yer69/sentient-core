#!/usr/bin/env python3
"""Benchmark llama.cpp and Ollama through their OpenAI-compatible endpoints.

Gate B2 on nanob (AIRI idle, one backend resident at a time, same GGUF).
Measures time to first token, decode tokens/sec, peak memory and power mode.
Memory comes from tegrastats, then jtop, then /proc/meminfo. Power mode is
read from ``nvpmodel -q`` (15W = 0, 25W = 1, MAXN_SUPER = 2 on the Super
flash config). Off-device tests point both URLs at a local fake server.

Example on nanob, after the chosen GGUF is loaded on each endpoint in turn::

    sudo python3 tests/performance/llm_bench.py \\
        --llama-url http://127.0.0.1:18080 \\
        --ollama-url http://127.0.0.1:11434 \\
        --model gemma-4-E2B_q4_0-it.gguf \\
        --ctx 1024 \\
        --runs 3 \\
        --power-modes 0,1,2 \\
        --apply-power-modes \\
        --out-dir /var/lib/sentient-jetson-models/bench
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


BACKENDS = (
    ("llama.cpp", "llama_url"),
    ("ollama", "ollama_url"),
)

_RAM_RE = re.compile(r"RAM\s+(\d+(?:\.\d+)?)\/(\d+(?:\.\d+)?)MB")
_VDD_RE = re.compile(r"VDD_IN\s+(\d+(?:\.\d+)?)mW")
_MODE_LINE_RE = re.compile(r"power mode:\s*(.+)", re.IGNORECASE)


def build_prompt(context_tokens: int) -> str:
    """Approximate ``context_tokens`` of prefill, then ask for one word.

    Four characters per token is a rough English estimate used only so both
    backends see the same prefill. The server context has to be at least this
    large (llama.cpp is started with ``--ctx-size``).
    """
    sentence = "The quick brown fox jumps over the lazy dog. "
    target_chars = max(len(sentence), int(context_tokens) * 4)
    copies = (target_chars // len(sentence)) + 1
    body = (sentence * copies)[:target_chars]
    return body + "\nReply with the single word READY."


def parse_tegrastats_line(line: str) -> Dict[str, Optional[float]]:
    """Parse one tegrastats sample. RAM figures are mebibytes as printed."""
    ram = _RAM_RE.search(line)
    vdd = _VDD_RE.search(line)
    used_mb = float(ram.group(1)) if ram else None
    return {
        "used_bytes": None if used_mb is None else int(used_mb * 1024 * 1024),
        "vdd_in_mw": float(vdd.group(1)) if vdd else None,
    }


def parse_nvpmodel_query(text: str) -> Dict[str, Any]:
    """Parse ``nvpmodel -q``. The trailing integer line is the mode index."""
    name = None
    index = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _MODE_LINE_RE.search(line)
        if match:
            name = match.group(1).strip()
            continue
        if re.fullmatch(r"-?\d+", line):
            index = int(line)
    return {"index": index, "name": name, "source": "nvpmodel"}


def read_proc_used_bytes() -> int:
    info: Dict[str, int] = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as handle:
        for line in handle:
            key, rest = line.split(":", 1)
            info[key] = int(rest.strip().split()[0]) * 1024
    available = info.get("MemAvailable", info.get("MemFree", 0))
    return max(0, info.get("MemTotal", 0) - available)


def read_power_mode(runner=None) -> Dict[str, Any]:
    """Return the current nvpmodel mode, or an unavailable record."""
    if runner is None and shutil.which("nvpmodel") is None:
        return {"index": None, "name": None, "source": "unavailable"}
    try:
        if runner is None:
            completed = subprocess.run(
                ["nvpmodel", "-q"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            text = (completed.stdout or "") + "\n" + (completed.stderr or "")
            if completed.returncode != 0 and not text.strip():
                return {"index": None, "name": None, "source": "unavailable"}
        else:
            text = runner(["nvpmodel", "-q"])
        parsed = parse_nvpmodel_query(text)
        if parsed["index"] is None and not parsed["name"]:
            return {"index": None, "name": None, "source": "unavailable"}
        return parsed
    except (OSError, subprocess.TimeoutExpired):
        return {"index": None, "name": None, "source": "unavailable"}


def _jtop_sample() -> Optional[Dict[str, Optional[float]]]:
    try:
        from jtop import jtop  # type: ignore
    except ImportError:
        return None
    try:
        with jtop() as jetson:
            if not jetson.ok():
                return None
            ram = jetson.memory["RAM"]["used"]
            vdd = None
            power = getattr(jetson, "power", None) or {}
            total = power.get("tot") if isinstance(power, dict) else None
            if isinstance(total, dict):
                vdd = total.get("power")
            return {"used_bytes": int(ram), "vdd_in_mw": None if vdd is None else float(vdd)}
    except Exception:
        return None


class MemorySampler:
    """Sample peak RAM and VDD_IN while a generation is in flight."""

    def __init__(self):
        self.source = self._choose_source()
        self.peak_bytes = 0
        self.peak_vdd_in_mw: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tegrastats: Optional[subprocess.Popen] = None

    @staticmethod
    def _choose_source() -> str:
        if shutil.which("tegrastats"):
            return "tegrastats"
        if _jtop_sample() is not None:
            return "jtop"
        return "proc"

    def start(self) -> None:
        self._stop.clear()
        self.peak_bytes = 0
        self.peak_vdd_in_mw = None
        self._thread = threading.Thread(target=self._loop, name="llm-bench-mem", daemon=True)
        self._thread.start()

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._tegrastats is not None and self._tegrastats.poll() is None:
            self._tegrastats.terminate()
            try:
                self._tegrastats.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._tegrastats.kill()
        self._tegrastats = None
        return {
            "source": self.source,
            "peak_bytes": self.peak_bytes,
            "peak_mb": round(self.peak_bytes / (1024 * 1024), 2),
            "vdd_in_mw": self.peak_vdd_in_mw,
        }

    def _note(self, used_bytes: Optional[float], vdd_in_mw: Optional[float]) -> None:
        if used_bytes is not None and used_bytes > self.peak_bytes:
            self.peak_bytes = int(used_bytes)
        if vdd_in_mw is not None and (self.peak_vdd_in_mw is None or vdd_in_mw > self.peak_vdd_in_mw):
            self.peak_vdd_in_mw = float(vdd_in_mw)

    def _loop(self) -> None:
        if self.source == "tegrastats":
            self._loop_tegrastats()
        elif self.source == "jtop":
            self._loop_jtop()
        else:
            self._loop_proc()

    def _loop_tegrastats(self) -> None:
        try:
            self._tegrastats = subprocess.Popen(
                ["tegrastats", "--interval", "100"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError:
            self.source = "proc"
            self._loop_proc()
            return
        assert self._tegrastats.stdout is not None
        while not self._stop.is_set():
            line = self._tegrastats.stdout.readline()
            if not line:
                break
            parsed = parse_tegrastats_line(line)
            self._note(parsed["used_bytes"], parsed["vdd_in_mw"])
        if self._tegrastats.poll() is None:
            self._tegrastats.terminate()

    def _loop_jtop(self) -> None:
        while not self._stop.is_set():
            sample = _jtop_sample()
            if sample is None:
                self.source = "proc"
                self._loop_proc()
                return
            self._note(sample["used_bytes"], sample["vdd_in_mw"])
            if self._stop.wait(0.2):
                return

    def _loop_proc(self) -> None:
        self.source = "proc"
        while not self._stop.is_set():
            try:
                self._note(read_proc_used_bytes(), None)
            except OSError:
                pass
            if self._stop.wait(0.05):
                return


def _join_url(base: str, path: str) -> str:
    root = base.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root + path


def _request_json(url: str, timeout: float) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def reported_model_id(base_url: str, timeout: float = 5.0) -> str:
    payload = _request_json(_join_url(base_url, "/v1/models"), timeout)
    data = payload.get("data") or []
    if not data or not data[0].get("id"):
        raise RuntimeError(f"No model id from {base_url}")
    return str(data[0]["id"])


def _sse_content(line: str) -> Optional[str]:
    """Return a content token, ``''`` for end of stream, or None to skip."""
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return None
    body = stripped[5:].strip()
    if body == "[DONE]":
        return ""
    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        return None
    usage = event.get("usage") or {}
    parts = []
    for choice in event.get("choices") or []:
        delta = choice.get("delta") or {}
        content = delta.get("content") or ""
        if isinstance(content, str) and content:
            parts.append(content)
    token = "".join(parts)
    completion = usage.get("completion_tokens")
    if token:
        return token
    if completion is not None:
        return f"\x00usage:{int(completion)}"
    return None


def stream_completion(
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout: float,
) -> Dict[str, Any]:
    """POST one streaming chat completion and time the first content token."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": True,
    }).encode("utf-8")
    request = urllib.request.Request(
        _join_url(base_url, "/v1/chat/completions"),
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    t_start = time.monotonic()
    t_first: Optional[float] = None
    t_end = t_start
    chunks: List[str] = []
    usage_tokens: Optional[int] = None
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            line = raw.decode("utf-8", errors="replace")
            token = _sse_content(line)
            if token is None:
                continue
            if token == "":
                t_end = time.monotonic()
                break
            if token.startswith("\x00usage:"):
                usage_tokens = int(token.split(":", 1)[1])
                continue
            now = time.monotonic()
            if t_first is None:
                t_first = now
            chunks.append(token)
            t_end = now
    completion_tokens = usage_tokens if usage_tokens is not None else len(chunks)
    if t_first is None:
        ttft = None
        tokens_per_sec = 0.0
    else:
        ttft = t_first - t_start
        elapsed = max(t_end - t_start, 1e-6)
        decode = max(t_end - t_first, 1e-6)
        if completion_tokens > 1:
            tokens_per_sec = (completion_tokens - 1) / decode
        else:
            tokens_per_sec = completion_tokens / elapsed
    return {
        "ttft_s": None if ttft is None else round(ttft, 6),
        "tokens_per_sec": round(tokens_per_sec, 3),
        "completion_tokens": completion_tokens,
        "total_s": round(t_end - t_start, 6),
        "text": "".join(chunks),
    }


def _apply_power_mode(index: int) -> None:
    subprocess.run(["nvpmodel", "-m", str(index)], check=True, timeout=15)


def _row_error(backend: str, url: str, model: str, power_mode: Dict[str, Any], error: str) -> Dict[str, Any]:
    return {
        "backend": backend,
        "url": url,
        "ok": False,
        "model": model,
        "reported_model": None,
        "model_mismatch": False,
        "ttft_s": None,
        "tokens_per_sec": None,
        "completion_tokens": 0,
        "total_s": None,
        "memory": {"source": None, "peak_bytes": None, "peak_mb": None, "vdd_in_mw": None},
        "power_mode": power_mode,
        "error": error,
    }


def bench_backend(
    backend: str,
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout: float,
    power_mode: Dict[str, Any],
) -> Dict[str, Any]:
    """One measured generation. A down endpoint is a recorded error, not a crash."""
    try:
        reported = reported_model_id(base_url, timeout=min(timeout, 8))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        return _row_error(backend, base_url, model, power_mode, str(exc))
    request_model = reported or model
    mismatch = bool(model and reported and model not in reported and reported not in model)
    active = MemorySampler()
    active.start()
    measured = None
    error = ""
    try:
        measured = stream_completion(base_url, request_model, prompt, max_tokens, timeout)
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        error = str(exc)
    finally:
        memory = active.stop()
    if error or measured is None:
        row = _row_error(backend, base_url, model, power_mode, error or "no measurement")
        row["reported_model"] = reported
        row["model_mismatch"] = mismatch
        row["memory"] = memory
        return row
    return {
        "backend": backend,
        "url": base_url,
        "ok": measured["ttft_s"] is not None and measured["completion_tokens"] > 0,
        "model": model,
        "reported_model": reported,
        "model_mismatch": mismatch,
        "ttft_s": measured["ttft_s"],
        "tokens_per_sec": measured["tokens_per_sec"],
        "completion_tokens": measured["completion_tokens"],
        "total_s": measured["total_s"],
        "memory": memory,
        "power_mode": power_mode,
        "error": None if measured["ttft_s"] is not None else "no token received",
        "text_preview": measured["text"][:120],
    }


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def summarise_runs(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Average successful runs that share backend and power-mode index."""
    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for row in rows:
        mode = row.get("power_mode") or {}
        key = (row.get("backend"), mode.get("index"), mode.get("name"))
        groups.setdefault(key, []).append(row)
    summary = []
    for (backend, index, name), grouped in groups.items():
        ok_rows = [row for row in grouped if row.get("ok")]
        chosen = ok_rows or grouped
        peaks = [row["memory"]["peak_bytes"] for row in chosen if row.get("memory", {}).get("peak_bytes")]
        vdds = [row["memory"]["vdd_in_mw"] for row in chosen if row.get("memory", {}).get("vdd_in_mw") is not None]
        summary.append({
            "backend": backend,
            "power_mode_index": index,
            "power_mode_name": name,
            "runs": len(grouped),
            "ok_runs": len(ok_rows),
            "ttft_s": _mean([row["ttft_s"] for row in ok_rows if row.get("ttft_s") is not None]),
            "tokens_per_sec": _mean([row["tokens_per_sec"] for row in ok_rows if row.get("tokens_per_sec") is not None]),
            "peak_memory_bytes": max(peaks) if peaks else None,
            "peak_vdd_in_mw": max(vdds) if vdds else None,
            "model_mismatch": any(row.get("model_mismatch") for row in grouped),
            "error": None if ok_rows else (grouped[-1].get("error") if grouped else "no runs"),
        })
    return summary


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# LLM benchmark",
        "",
        f"- Host: `{report.get('host')}`",
        f"- Requested GGUF: `{report.get('model')}`",
        f"- Context target: {report.get('context_tokens')} tokens (same prompt for every backend)",
        f"- Runs per backend: {report.get('runs')}",
        "",
        "Swap the installed engine only if one backend is clearly better on time to first token and tokens/sec for this GGUF. This report does not change the running server.",
        "",
        "| Backend | Power mode | TTFT (s) | tok/s | Peak RAM (MB) | VDD_IN (mW) | Memory source | OK |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in report.get("summary") or []:
        peak = row.get("peak_memory_bytes")
        peak_mb = "" if peak is None else f"{peak / (1024 * 1024):.1f}"
        vdd = row.get("peak_vdd_in_mw")
        vdd_text = "" if vdd is None else f"{vdd:.0f}"
        mode = row.get("power_mode_index")
        mode_name = row.get("power_mode_name") or ""
        mode_text = "unavailable" if mode is None and not mode_name else f"{mode if mode is not None else ''} {mode_name}".strip()
        lines.append(
            "| {backend} | {mode} | {ttft} | {tps} | {peak} | {vdd} | {source} | {ok} |".format(
                backend=row.get("backend"),
                mode=mode_text,
                ttft="" if row.get("ttft_s") is None else f"{row['ttft_s']:.4f}",
                tps="" if row.get("tokens_per_sec") is None else f"{row['tokens_per_sec']:.2f}",
                peak=peak_mb,
                vdd=vdd_text,
                source=report.get("memory_source") or "",
                ok=f"{row.get('ok_runs')}/{row.get('runs')}",
            )
        )
    mismatches = [row for row in report.get("runs_detail") or [] if row.get("model_mismatch")]
    if mismatches:
        lines.extend(["", "Reported model ids differed from the requested GGUF:"])
        for row in mismatches:
            lines.append(f"- {row.get('backend')}: requested `{row.get('model')}`, server reported `{row.get('reported_model')}`")
    errors = [row for row in report.get("summary") or [] if row.get("error")]
    if errors:
        lines.extend(["", "Errors:"])
        for row in errors:
            lines.append(f"- {row.get('backend')}: {row.get('error')}")
    lines.append("")
    return "\n".join(lines)


def run_benchmark(
    llama_url: str = "http://127.0.0.1:18080",
    ollama_url: str = "http://127.0.0.1:11434",
    model: str = "",
    prompt: Optional[str] = None,
    context_tokens: int = 1024,
    max_tokens: int = 64,
    runs: int = 1,
    timeout: float = 180.0,
    power_modes: Optional[List[int]] = None,
    apply_power_modes: bool = False,
    out_dir: Optional[str] = None,
    power_mode_reader=None,
) -> Dict[str, Any]:
    """Benchmark both endpoints sequentially and write JSON plus markdown.

    ``power_modes`` lists nvpmodel indexes to record. With ``apply_power_modes``
    the harness calls ``nvpmodel -m`` and restores the original mode afterwards.
    Off-device, leave that flag unset: the current mode is recorded as
    unavailable when ``nvpmodel`` is not installed.
    """
    urls = {"llama_url": llama_url, "ollama_url": ollama_url}
    text = prompt if prompt is not None else build_prompt(context_tokens)
    reader = power_mode_reader or read_power_mode
    original = reader()
    modes = list(power_modes) if power_modes else [original.get("index")]
    detail: List[Dict[str, Any]] = []
    applied: List[int] = []

    def collect(mode_record: Dict[str, Any]) -> None:
        for _run in range(max(1, runs)):
            for backend, url_key in BACKENDS:
                detail.append(bench_backend(
                    backend, urls[url_key], model, text, max_tokens, timeout, mode_record,
                ))

    try:
        if apply_power_modes:
            if shutil.which("nvpmodel") is None:
                collect(original)
            else:
                for index in modes:
                    if index is None:
                        collect(original)
                        continue
                    _apply_power_mode(int(index))
                    applied.append(int(index))
                    time.sleep(1.0)
                    collect(reader())
        else:
            collect(original)
    finally:
        if applied and original.get("index") is not None and shutil.which("nvpmodel"):
            try:
                _apply_power_mode(int(original["index"]))
            except (OSError, subprocess.CalledProcessError):
                pass

    summary = summarise_runs(detail)
    sources = {row.get("memory", {}).get("source") for row in detail if row.get("memory")}
    sources.discard(None)
    report = {
        "schema_version": 1,
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "model": model,
        "context_tokens": context_tokens,
        "prompt_characters": len(text),
        "runs": runs,
        "power_modes_requested": power_modes or [],
        "apply_power_modes": apply_power_modes,
        "memory_source": next(iter(sources), "proc"),
        "summary": summary,
        "runs_detail": detail,
    }
    if out_dir:
        destination = Path(out_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "llm-bench.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (destination / "llm-bench.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark llama.cpp and Ollama on the same GGUF")
    parser.add_argument("--llama-url", default="http://127.0.0.1:18080")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", required=True, help="GGUF filename or id loaded on both backends")
    parser.add_argument("--ctx", type=int, default=1024, help="Target prefill size in tokens (default 1024)")
    parser.add_argument("--prompt", default=None, help="Override the generated prefill prompt")
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--power-modes", default="", help="Comma-separated nvpmodel indexes, e.g. 0,1,2")
    parser.add_argument("--apply-power-modes", action="store_true", help="Switch nvpmodel during the run and restore it after")
    parser.add_argument("--out-dir", default="/tmp/orin-llm-bench")
    args = parser.parse_args(argv)
    modes = [int(part) for part in args.power_modes.split(",") if part.strip() != ""]
    report = run_benchmark(
        llama_url=args.llama_url,
        ollama_url=args.ollama_url,
        model=args.model,
        prompt=args.prompt,
        context_tokens=args.ctx,
        max_tokens=args.max_tokens,
        runs=args.runs,
        timeout=args.timeout,
        power_modes=modes or None,
        apply_power_modes=args.apply_power_modes,
        out_dir=args.out_dir,
    )
    print(render_markdown(report))
    ok = any(row.get("ok_runs") for row in report["summary"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
