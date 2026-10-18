# Security policy

## Reporting a vulnerability

Please do not report security problems in public issues, discussions or pull requests.

Report them privately through GitHub: open this repository's **Security** tab and choose **Report a vulnerability**. Only the maintainers can see the report. Include what you found, the steps to reproduce it, the Blendex and Windows versions, and the impact you expect.

You should get a first reply within a week. Once a fix is released, you are credited in the release notes unless you prefer not to be.

## Supported versions

Only the latest release receives security fixes.

## Scope

Blendex is a local desktop app, so the most relevant areas are:

- The local API on `127.0.0.1:8765`, for example reading files outside a library's `.cache` folder or starting programs other than the detected apps.
- The Blender bridge add-on (`scripts/dam_bridge.py`), which listens on `127.0.0.1` and must only import 3D files with a valid per-session token.
- Parsing untrusted `.blend` files (`backend/blend_header.py`, `backend/blend_preview.py`).
- The installer and the packaged app.

Problems that need an attacker who already controls your Windows account are generally out of scope.
