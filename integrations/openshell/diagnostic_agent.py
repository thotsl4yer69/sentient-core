#!/usr/bin/env python3
"""OpenShell-contained diagnostic client: no device control, shell, or filesystem writes."""
import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://host.openshell.internal:11435/v1"

def endpoints(base_url):
    parsed = urllib.parse.urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname != "host.openshell.internal"
            or parsed.port != 11435 or parsed.path.rstrip("/") != "/v1"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Only the isolated SSH tunnel endpoint is allowed")
    return "http://host.openshell.internal:11435"

def request_json(url, payload=None, timeout=20):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        if res.status != 200:
            raise RuntimeError(f"Model endpoint returned HTTP {res.status}")
        return json.load(res)

def select_model(tags, requested=None):
    names = [m.get("name", "") for m in tags.get("models", [])
             if isinstance(m, dict) and m.get("name")]
    if requested:
        if requested not in names:
            raise ValueError(f"Model {requested!r} not installed on Nano")
        return requested
    for keyword in ("llama3.2:3b", "qwen3.5:4b", "gemma2:2b"):
        for name in names:
            if name.lower().startswith(keyword):
                return name
    if not names:
        raise ValueError("No Ollama models installed on Nano")
    return names[0]

def main(argv=None):
    parser = argparse.ArgumentParser(description="OpenShell -> Orin Nano model smoke test")
    parser.add_argument("--check", action="store_true", help="Read-only model inventory")
    parser.add_argument("--prompt", default="Reply with exactly: SENTIENT CORE CONNECTED")
    parser.add_argument("--model", default=os.environ.get("NANO_OLLAMA_MODEL"))
    args = parser.parse_args(argv)
    host = endpoints(os.environ.get("OPENAI_BASE_URL", DEFAULT_URL))
    tags = request_json(host + "/api/tags")
    chosen = select_model(tags, args.model)
    if args.check:
        print(json.dumps({"connected": True, "model": chosen, "model_count": len(tags.get("models", []))}))
        return 0
    if not args.prompt or len(args.prompt) > 4000:
        parser.error("prompt must be between 1 and 4000 characters")
    body = {"model": chosen, "messages": [{"role": "user", "content": args.prompt}],
            "stream": False, "options": {"num_predict": 96}}
    response = request_json(host + "/v1/chat/completions", body, timeout=90)
    output = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not output:
        raise RuntimeError("Nano returned empty model content")
    print(json.dumps({"connected": True, "model": chosen, "response": output[:3000]}))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, urllib.error.HTTPError,
            urllib.error.URLError, RuntimeError) as err:
        print(json.dumps({"connected": False, "error": str(err)}))
        raise SystemExit(1)
