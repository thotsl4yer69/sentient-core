# NANO AIRI card

Source for the NANO companion character card used by the Jetson/Orin AIRI deployment.

Build:

```bash
python3 tools/build_airi_card.py
```

Test:

```bash
pytest -q tests/unit/test_airi_card.py
```

The generated ZIP is intentionally not committed because this repository ignores generated archives. Provider/model/voice IDs are blank by design: deployment binds the card to the local AIRI runtime settings rather than forcing a cloud provider.

When the production avatar is ready, add `models/companion.vrm` to the card package and the corresponding `resources.displayModel` entry to `manifest.json` using AIRI's `VRM` display-model format.
