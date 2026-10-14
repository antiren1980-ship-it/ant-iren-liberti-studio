# Studio toolkit

Reusable Python tools we build and run in production.

| File | What it does |
|------|--------------|
| `mail.py` | IMAP/SMTP mail reader & sender (multi-profile, search, wait-for-code) |
| `vision.py` | Image understanding via Gemini (see / read text / compare) |
| `browser.py` | Anti-detect browser control (navigate, snapshot, type, click, upload) |
| `leads_finder.py` | AI leads finder — reads context, separates a real client from another dev |
| `control_tests.py` | Regression tests with model tagging (`--save` / `--diff`) |
| `safecopy.py` | Safe project copy without secrets / DBs / logs |
| `ot_client.py` | DPoP (ES256) client for an agent marketplace API |

> All secrets are read from env / a secret store — nothing hardcoded.
