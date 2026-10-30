"""Hardware-independent regression tests for the llama.cpp-compatible client.

Run: python3 -m unittest tests.unit.test_local_openai -v
"""
import importlib.util
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


if __name__ == "__main__":
    unittest.main()
