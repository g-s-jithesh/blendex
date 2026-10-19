# Contributing to Blendex

Thanks for helping. Bug reports, ideas, docs fixes and code are all welcome. By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md), and by submitting a pull request you agree to license your work under the project's [MIT License](LICENSE).

## Ways to help

- **Report a bug** with the bug report form under [Issues](https://github.com/g-s-jithesh/blendex/issues/new/choose). Include your Blendex, Windows and Blender versions and the end of `%APPDATA%\dam\blendex.log`.
- **Suggest a feature** with the feature request form. For open-ended ideas or questions, use [Discussions](https://github.com/g-s-jithesh/blendex/discussions).
- **Fix something.** Issues labelled `good first issue` or `help wanted` are good starting points, as are the "Known limitations" in the [README](README.md#known-limitations).
- **Security problems** go through private reporting, never public issues. See [SECURITY.md](SECURITY.md).

For anything bigger than a small fix, please open an issue or discussion first so we can agree on the approach before you spend time on it.

## Setting up

You need Windows 10/11, Git, [Python 3.14](https://www.python.org/downloads/) (3.11+ works, but only 3.14 can read zstd-compressed `.blend` previews without Blender) and [Node.js](https://nodejs.org/) 20 or newer. [Blender](https://www.blender.org/download/) 4.2+ is optional, but needed for real thumbnails and previews and for the tests marked `blender`.

```bat
git clone https://github.com/<you>/blendex.git
cd blendex
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
npm --prefix frontend install
start.bat
```

`start.bat` runs the backend on port 8765 and the UI with hot reload on http://127.0.0.1:5173. It also creates the venv and installs packages on first run.

## Tests and checks

Run these before opening a pull request. GitHub runs the same checks on every pull request.

```bat
.venv\Scripts\python -m ruff check --config backend/ruff.toml backend tests scripts packaging
.venv\Scripts\python -m pytest -m "not blender"
npm --prefix frontend run build
npm --prefix frontend run test
```

- `.venv\Scripts\python -m pytest` without `-m` also runs the tests that drive a real Blender. They are skipped if Blender is not installed.
- The browser tests need Google Chrome: `npm --prefix tests/e2e install`, then `npx --prefix tests/e2e playwright test -c tests/e2e`.
- Add or update tests for any behaviour you change. Backend tests live in `tests/`, frontend unit tests in `tests/frontend/`, and browser tests in `tests/e2e/`.

## Code style

- Keep diffs small and focused: one change per pull request.
- Comments explain *why*, not *what*. There are at most 5 comments per file, and no docstrings or commented-out code.
- Python: type hints on public functions, and `ruff` must pass. TypeScript: `strict` mode, no `any`.
- Don't add abstractions with a single implementation, or new dependencies without discussing them first.

## Changing contracts

- **API:** `docs/api_spec.json` is the source of truth, and a test checks that every route matches it. Update it, and the tables in [CLAUDE.md](CLAUDE.md), in the same pull request.
- **Database:** add a new numbered file in `backend/migrations/` (for example `002_add_tags.sql`). Never edit a migration that has been released.
- **Security rules:** read the "Security Boundaries" section of [CLAUDE.md](CLAUDE.md) before touching subprocesses, file serving or the Blender bridge.

## Pull requests

1. Fork the repo and create a branch from `main`.
2. Make your change, with tests.
3. Open a pull request and fill in the template. Add a screenshot or short clip for UI changes.
4. The checks must pass, and a maintainer reviews and merges.

## AI-assisted contributions

This project was built with Claude Code. `CLAUDE.md`, `PLAN.md` and `.claude/agents/` describe the architecture and conventions for AI agents and humans alike. AI-assisted pull requests are welcome, but you are responsible for understanding, testing and standing behind every line you submit.

## Releases

Maintainers cut releases by bumping the version and pushing a `vX.Y.Z` tag. The Release workflow then builds the installer and publishes the GitHub release. See [docs/RELEASING.md](docs/RELEASING.md), which also covers free code signing through the SignPath Foundation.
