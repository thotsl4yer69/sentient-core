from pathlib import Path

ROOT = Path(__file__).parent
STATIC = ROOT / "static"


def test_athena_renderer_preserves_avatar_contract():
    source = (STATIC / "avatar-hologram.js").read_text(encoding="utf-8")
    for api in (
        "setEmotion",
        "setSpeaking",
        "setState",
        "setAttentionState",
        "updateFromChatMessage",
        "processPhonemes",
        "onStreamToken",
        "getDiagnostics",
        "setGaze",
        "setVisionTarget",
    ):
        assert api in source
    assert "AthenaCanvas2D" in source
    assert "WebGL" not in source


def test_athena_kiosk_wires_bridge_and_canvas():
    page = (STATIC / "athena.html").read_text(encoding="utf-8")
    assert 'id="avatar-canvas"' in page
    assert "/static/avatar-hologram.js" in page
    assert ":9001" in page
    assert "window.athenaDemo" in page
    assert "setVisionTarget" in page
    assert "processPhonemes" in page
