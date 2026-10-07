import json
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "airi" / "nano"


def load():
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    card = json.loads((SOURCE / "card.json").read_text(encoding="utf-8"))
    return manifest, card


def test_current_airi_package_contract():
    manifest, card = load()
    assert manifest == {
        "format": "airi-character-card",
        "version": 1,
        "card": {"path": "card.json", "spec": "chara_card_v3"},
    }
    assert card["spec"] == "chara_card_v3"
    assert card["spec_version"] == "3.0"


def test_nano_profile_and_runtime_extension_are_complete():
    _, card = load()
    data = card["data"]
    for field in (
        "name", "description", "personality", "scenario", "first_mes",
        "creator_notes", "system_prompt", "post_history_instructions",
    ):
        assert isinstance(data[field], str) and data[field].strip()

    airi = data["extensions"]["airi"]
    assert airi["agents"] == {}
    assert set(airi["modules"]) == {"consciousness", "vision", "speech"}
    assert airi["modules"]["consciousness"] == {"provider": "", "model": ""}
    assert airi["modules"]["vision"] == {"provider": "", "model": ""}
    assert airi["modules"]["speech"] == {"provider": "", "model": "", "voice_id": ""}


def test_stage_control_prompt_and_greetings():
    _, card = load()
    data = card["data"]
    prompt = data["system_prompt"]
    assert '<|ACT {"emotion"' in prompt
    assert "<|DELAY 1|>" in prompt
    assert '<|CALL ["name"]|>' in prompt
    for greeting in [data["first_mes"], *data["alternate_greetings"]]:
        assert greeting.startswith("<|ACT ")
        assert "|>" in greeting


def test_no_hard_coded_remote_provider():
    _, card = load()
    serialized = json.dumps(card["data"]["extensions"]["airi"]["modules"])
    banned = ("openai", "anthropic", "openrouter", "elevenlabs", "gemini")
    assert not any(name in serialized.lower() for name in banned)


def test_package_round_trip(tmp_path):
    output = tmp_path / "NANO-AIRI-Card-v1.0.0.zip"
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_airi_card", ROOT / "tools" / "build_airi_card.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(output)

    with ZipFile(output) as archive:
        assert archive.namelist() == ["manifest.json", "card.json"]
        manifest = json.loads(archive.read("manifest.json"))
        card = json.loads(archive.read("card.json"))
        assert manifest["card"]["path"] == "card.json"
        assert card["data"]["name"] == "NANO"
