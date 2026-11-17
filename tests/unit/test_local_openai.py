"""Hardware-independent regression tests for the llama.cpp-compatible client.

Run: python3 -m unittest tests.unit.test_local_openai -v
"""
import asyncio
import importlib.util
import json
import unittest
from pathlib import Path

from aiohttp import web

# Load the small adapter directly: device-specific Sentient Core dependencies
# are deliberately not installed in the hardware-independent CI job.
module_path = Path(__file__).resolve().parents[2] / "sentient/services/contemplation/local_openai.py"
spec = importlib.util.spec_from_file_location("local_openai_under_test", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
LocalOpenAIClient = module.LocalOpenAIClient


class LocalOpenAITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.posts = []
        app = web.Application()

        async def models(request):
            return web.json_response({"data": [{"id": "loaded-gguf.gguf"}]})

        async def completions(request):
            payload = await request.json()
            self.posts.append(payload)
            if payload.get("stream"):
                response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
                await response.prepare(request)
                chunks = [
                    b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
                    b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
                    b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
                    b'data: [DONE]\n\n',
                ]
                for chunk in chunks:
                    await response.write(chunk[:max(1, len(chunk)//2)])
                    await response.write(chunk[max(1, len(chunk)//2):])
                await response.write_eof()
                return response
            return web.json_response({"choices": [{"message": {"content": "READY"}}]})

        app.router.add_get("/v1/models", models)
        app.router.add_post("/v1/chat/completions", completions)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.client = LocalOpenAIClient(f"http://127.0.0.1:{port}")

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()

    async def test_generate_resolves_real_model_and_forwards_stop(self):
        text = await self.client.generate(
            "stale-ollama-name", "Say READY", system="Be brief",
            max_tokens=16, stop=["stop-now"],
        )
        self.assertEqual(text, "READY")
        self.assertEqual(self.posts[-1]["model"], "loaded-gguf.gguf")
        self.assertEqual(self.posts[-1]["messages"][0]["role"], "system")
        self.assertEqual(self.posts[-1]["stop"], ["stop-now"])
        self.assertFalse(self.posts[-1]["stream"])

    async def test_chat_stream_decodes_sse_and_ignores_metadata(self):
        parts = [
            token async for token in self.client.chat_stream(
                "stale-name", [{"role": "user", "content": "Hi"}]
            )
        ]
        self.assertEqual("".join(parts), "Hello world")
        self.assertTrue(self.posts[-1]["stream"])

    async def test_generate_stream_uses_chat_messages(self):
        tokens = [
            token async for token in self.client.generate_stream(
                "unused", "Hi", system="Kind"
            )
        ]
        self.assertEqual("".join(tokens), "Hello world")
        self.assertEqual(self.posts[-1]["messages"][0]["role"], "system")


class ContractServerMixin:
    """Fake OpenAI-compatible server with a slow stream and /api/ps."""

    async def _start_contract_server(self):
        self.posts = []
        self.headers = []
        self.ps_models = []
        app = web.Application()

        async def models(_request):
            return web.json_response({"data": [{"id": "loaded-gguf.gguf"}]})

        async def api_ps(_request):
            return web.json_response({"models": self.ps_models})

        async def completions(request):
            payload = await request.json()
            self.posts.append(payload)
            self.headers.append(dict(request.headers))
            message = ""
            for item in payload.get("messages") or []:
                if item.get("role") == "user":
                    message = item.get("content") or ""
            if payload.get("stream") and "SLOW_STREAM" in message:
                response = web.StreamResponse(
                    status=200, headers={"Content-Type": "text/event-stream"}
                )
                await response.prepare(request)
                for index in range(40):
                    try:
                        body = (
                            '{"choices":[{"delta":{"content":"%s"}}]}\n\n' % index
                        )
                        await response.write(b"data: " + body.encode())
                        await asyncio.sleep(0.05)
                    except (ConnectionError, ConnectionResetError, RuntimeError):
                        return response
                try:
                    await response.write(b"data: [DONE]\n\n")
                    await response.write_eof()
                except (ConnectionError, ConnectionResetError, RuntimeError):
                    return response
                return response
            if "EMPTY" in message:
                return web.json_response({"choices": [{"message": {"content": "  "}}]})
            return web.json_response({"choices": [{"message": {"content": "READY"}}]})

        app.router.add_get("/v1/models", models)
        app.router.add_get("/api/ps", api_ps)
        app.router.add_post("/v1/chat/completions", completions)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"

    def _user_text(self, payload):
        for item in payload.get("messages") or []:
            if item.get("role") == "user":
                return item.get("content") or ""
        return ""


class AdapterContractTests(ContractServerMixin, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await self._start_contract_server()
        self.client = LocalOpenAIClient(self.url)

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()

    async def test_turn_id_priority_and_voice_token_cap(self):
        text = await self.client.generate(
            "stale-ollama-name", "Say READY", max_tokens=512,
            turn_id="turn-7", priority="voice",
        )
        self.assertEqual(text, "READY")
        self.assertEqual(self.posts[-1]["max_tokens"], 256)
        self.assertEqual(self.headers[-1]["X-Turn-Id"], "turn-7")
        self.assertEqual(self.headers[-1]["X-Sc-Priority"], "voice")
        self.assertEqual(self.client.outcomes["turn-7"].status, "ok")
        self.assertTrue(self.client.outcomes["turn-7"].count_attempt)
        self.assertFalse(self.client.outcomes["turn-7"].requeue)

    async def test_ollama_backend_keeps_the_requested_model(self):
        client = LocalOpenAIClient(self.url, backend="ollama")
        try:
            text = await client.generate("llama3.2:3b", "Say READY", turn_id="ollama-1")
        finally:
            await client.close()
        self.assertEqual(text, "READY")
        self.assertEqual(self.posts[-1]["model"], "llama3.2:3b")

    async def test_cancel_inflight_stream_is_requeued_without_an_attempt(self):
        received = []

        async def consume():
            async for token in self.client.chat_stream(
                "loaded-gguf.gguf",
                [{"role": "user", "content": "SLOW_STREAM"}],
                turn_id="turn-cancel",
                priority="background",
                max_duration=10,
                deadline=5,
            ):
                received.append(token)
                if len(received) == 1:
                    await self.client.cancel("turn-cancel")

        await asyncio.wait_for(consume(), timeout=3)
        outcome = self.client.outcomes["turn-cancel"]
        self.assertEqual(outcome.status, "cancelled")
        self.assertFalse(outcome.count_attempt)
        self.assertTrue(outcome.requeue)
        pending = self.client.drain_requeue()
        self.assertEqual([item.turn_id for item in pending], ["turn-cancel"])
        self.assertEqual(self.client.drain_requeue(), [])
        self.assertFalse(await self.client.cancel("missing-turn"))

    async def test_voice_turn_cancels_non_voice_holder(self):
        held = []

        async def hold():
            async for token in self.client.chat_stream(
                "loaded-gguf.gguf",
                [{"role": "user", "content": "SLOW_STREAM"}],
                turn_id="background-hold",
                priority="background",
                max_duration=10,
                deadline=5,
            ):
                held.append(token)

        task = asyncio.create_task(hold())
        for _ in range(50):
            if held:
                break
            await asyncio.sleep(0.02)
        self.assertTrue(held)
        text = await self.client.generate(
            "loaded-gguf.gguf", "Say READY",
            turn_id="voice-now", priority="voice", max_duration=5, deadline=2,
        )
        await asyncio.wait_for(task, timeout=3)
        self.assertEqual(text, "READY")
        self.assertEqual(self.client.outcomes["background-hold"].status, "cancelled")
        self.assertTrue(self.client.outcomes["background-hold"].requeue)
        self.assertFalse(self.client.outcomes["background-hold"].count_attempt)
        self.assertEqual(self.client.outcomes["voice-now"].status, "ok")

    async def test_start_deadline_while_slot_is_held(self):
        held = []

        async def hold():
            async for token in self.client.chat_stream(
                "loaded-gguf.gguf",
                [{"role": "user", "content": "SLOW_STREAM"}],
                turn_id="holder",
                priority="background",
                max_duration=10,
                deadline=5,
            ):
                held.append(token)

        task = asyncio.create_task(hold())
        for _ in range(50):
            if held:
                break
            await asyncio.sleep(0.02)
        self.assertTrue(held)
        started = asyncio.get_running_loop().time()
        text = await self.client.generate(
            "loaded-gguf.gguf", "Say READY",
            turn_id="waiter", priority="chat", deadline=0.25, max_duration=5,
        )
        elapsed = asyncio.get_running_loop().time() - started
        self.assertEqual(text, "")
        self.assertEqual(self.client.outcomes["waiter"].status, "deadline")
        self.assertTrue(self.client.outcomes["waiter"].count_attempt)
        self.assertFalse(self.client.outcomes["waiter"].requeue)
        self.assertLess(elapsed, 1.5)
        await self.client.cancel("holder")
        await asyncio.wait_for(task, timeout=3)

    async def test_max_duration_stops_a_stream(self):
        parts = []
        async for token in self.client.chat_stream(
            "loaded-gguf.gguf",
            [{"role": "user", "content": "SLOW_STREAM"}],
            turn_id="long",
            max_duration=0.3,
            deadline=2,
        ):
            parts.append(token)
        outcome = self.client.outcomes["long"]
        self.assertEqual(outcome.status, "duration")
        self.assertTrue(outcome.count_attempt)
        self.assertFalse(outcome.requeue)
        self.assertGreaterEqual(len(parts), 1)
        self.assertLess(len(parts), 40)


class FailoverTests(ContractServerMixin, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await self._start_contract_server()

    async def asyncTearDown(self):
        await self.runner.cleanup()

    def _stub(self):
        stub = _FallbackStub(self.url)
        return stub

    async def test_skips_fallback_when_nothing_is_loaded(self):
        primary = LocalOpenAIClient(self.url)
        fallback = self._stub()
        client = module.FailoverClient(primary, fallback)
        self.ps_models = []
        try:
            text = await client.generate("loaded-gguf.gguf", "EMPTY")
        finally:
            await client.close()
        self.assertEqual(text, "")
        self.assertEqual(fallback.calls, 0)
        self.assertEqual(client.last_outcome.status, "empty")

    async def test_uses_fallback_only_when_a_model_is_already_loaded(self):
        primary = LocalOpenAIClient(self.url)
        fallback = self._stub()
        client = module.FailoverClient(primary, fallback)
        self.ps_models = [{"name": "llama3.2:3b", "size": 1}]
        try:
            text = await client.generate("loaded-gguf.gguf", "EMPTY")
        finally:
            await client.close()
        self.assertEqual(text, "FROM_FALLBACK")
        self.assertEqual(fallback.calls, 1)

    async def test_cancelled_primary_is_not_sent_to_fallback(self):
        primary = _CancelledPrimary()
        fallback = self._stub()
        client = module.FailoverClient(primary, fallback)
        self.ps_models = [{"name": "llama3.2:3b"}]
        text = await client.generate("model", "hi")
        self.assertEqual(text, "")
        self.assertEqual(fallback.calls, 0)
        self.assertEqual(client.last_outcome.status, "cancelled")


class _FallbackStub:
    def __init__(self, host):
        self.host = host
        self.calls = 0
        self.last_outcome = None

    async def generate(self, *args, **kwargs):
        self.calls += 1
        self.last_outcome = module.GenerationOutcome(status="ok", text="FROM_FALLBACK")
        return "FROM_FALLBACK"

    async def chat_stream(self, *args, **kwargs):
        self.calls += 1
        yield "FALL"

    async def generate_stream(self, *args, **kwargs):
        self.calls += 1
        yield "FALL"

    async def cancel(self, turn_id):
        return False

    def drain_requeue(self):
        return []

    async def close(self):
        return None


class _CancelledPrimary:
    def __init__(self):
        self.last_outcome = module.GenerationOutcome(
            status="cancelled", count_attempt=False, requeue=True, turn_id="c1"
        )
        self.requeue = []
        self.host = "http://127.0.0.1:9"

    async def generate(self, *args, **kwargs):
        return ""

    async def cancel(self, turn_id):
        return False

    def drain_requeue(self):
        return []

    async def close(self):
        return None


class BenchmarkHarnessTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        bench_path = Path(__file__).resolve().parents[1] / "performance" / "llm_bench.py"
        spec = importlib.util.spec_from_file_location("llm_bench_under_test", bench_path)
        cls.bench = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.bench)

    async def asyncSetUp(self):
        app = web.Application()

        async def models(_request):
            return web.json_response({"data": [{"id": "same-model.gguf"}]})

        async def completions(request):
            payload = await request.json()
            self.assertTrue(payload["stream"])
            self.assertEqual(payload["model"], "same-model.gguf")
            response = web.StreamResponse(
                status=200, headers={"Content-Type": "text/event-stream"}
            )
            await response.prepare(request)
            for piece in ("Hel", "lo"):
                await response.write(
                    f'data: {{"choices":[{{"delta":{{"content":"{piece}"}}}}]}}\n\n'.encode()
                )
            await response.write(
                b'data: {"choices":[{"delta":{}}],"usage":{"completion_tokens":2}}\n\n'
            )
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
            return response

        app.router.add_get("/v1/models", models)
        app.router.add_post("/v1/chat/completions", completions)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.runner.cleanup()

    def test_parsers(self):
        sample = (
            "RAM 3456/7620MB (lfb 12x4MB) SWAP 0/1905MB "
            "CPU [1%@729] EMC_FREQ 2133 GR3D_FREQ 15 "
            "VDD_IN 5123mW/4980mW VDD_CPU_GPU_CV 1200mW/1100mW"
        )
        parsed = self.bench.parse_tegrastats_line(sample)
        self.assertEqual(parsed["used_bytes"], 3456 * 1024 * 1024)
        self.assertEqual(parsed["vdd_in_mw"], 5123.0)
        mode = self.bench.parse_nvpmodel_query("NV Power Mode: 25W\n1\n")
        self.assertEqual(mode["index"], 1)
        self.assertEqual(mode["name"], "25W")
        self.assertGreater(self.bench.read_proc_used_bytes(), 0)

    async def test_fake_server_writes_json_and_markdown(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            report = await asyncio.to_thread(
                self.bench.run_benchmark,
                self.url,
                self.url,
                "same-model.gguf",
                "Say READY",
                32,
                8,
                1,
                10.0,
                None,
                False,
                tmp,
            )
            json_path = Path(tmp) / "llm-bench.json"
            md_path = Path(tmp) / "llm-bench.md"
            self.assertTrue(json_path.is_file())
            self.assertTrue(md_path.is_file())
            saved = json.loads(json_path.read_text())
            self.assertEqual(saved["model"], "same-model.gguf")
            self.assertEqual(len(saved["runs_detail"]), 2)
            backends = {row["backend"] for row in saved["runs_detail"]}
            self.assertEqual(backends, {"llama.cpp", "ollama"})
            for row in saved["runs_detail"]:
                self.assertTrue(row["ok"])
                self.assertIsNotNone(row["ttft_s"])
                self.assertGreaterEqual(row["ttft_s"], 0)
                self.assertGreater(row["tokens_per_sec"], 0)
                self.assertEqual(row["completion_tokens"], 2)
                self.assertGreater(row["memory"]["peak_bytes"], 0)
                self.assertIn(row["memory"]["source"], ("proc", "tegrastats", "jtop"))
                self.assertIn(row["power_mode"]["source"], ("unavailable", "nvpmodel"))
            markdown = md_path.read_text()
            self.assertIn("llama.cpp", markdown)
            self.assertIn("ollama", markdown)
            self.assertIn("tok/s", markdown)
            self.assertEqual(report["summary"][0]["ok_runs"], 1)

    def test_launcher_pins_image_and_keeps_memory_cap(self):
        script = (Path(__file__).resolve().parents[2] / "scripts" / "jetson-models.sh").read_text()
        self.assertIn("b8708-r36.4-tegra-aarch64-cu126-22.04", script)
        self.assertNotIn("latest-jetson-orin", script)
        self.assertIn('--memory="${LLM_MEMORY}"', script)
        self.assertIn('--memory-swap="${LLM_MEMORY}"', script)
        self.assertIn("3300m", script)


if __name__ == "__main__":
    unittest.main()
