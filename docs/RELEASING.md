# Releasing Blendex

Releases are built and published by [`.github/workflows/release.yml`](../.github/workflows/release.yml) : the Windows installer on a Windows runner and the Linux tarball on an `ubuntu-22.04` runner. Nobody uploads files by hand.

## What the workflow does

| Trigger | Builds `Blendex-<version>-Setup.exe` and `Blendex-<version>-linux-x86_64.tar.gz` | Uploads it as a workflow artifact | Creates the GitHub release |
|---|---|---|---|
| Push a tag `vX.Y.Z` | yes | yes | yes, with generated notes, the installer and the Linux tarball attached |
| Run it by hand (Actions → Release → Run workflow) | yes | yes | no |
| Pull request touching `packaging/**`, `requirements*.txt` or the workflow | yes | yes | no |

The build installs `requirements.txt` + `packaging/requirements.txt` on Python 3.14 and Node 24, installs Inno Setup 6 if the runner lacks it, and runs `python packaging/build.py`. On a tag it first checks that the tag, `VERSION` in `backend/app.py` and `version` in `frontend/package.json` all match, and fails otherwise.

The Linux job apt-installs the libraries PyInstaller bundles for Qt WebEngine (NSS, ALSA, the xcb/xkb helpers, EGL/GL; the list is in the workflow), installs the same requirements and runs `python packaging/build.py`, which on Linux skips Inno Setup and writes `release/Blendex-<version>-linux-x86_64.tar.gz`. Building on 22.04 makes glibc 2.35 the minimum; don't move the job to a newer image without accepting that.

## Cutting a release

1. In a pull request, bump the version in both places, for example to `1.2.0`:
   - `VERSION = "1.2.0"` in `backend/app.py`
   - `"version": "1.2.0"` in `frontend/package.json` (and run `npm --prefix frontend install` so `package-lock.json` follows)
2. Merge it once the checks pass.
3. Tag the merge commit on `main` and push the tag:

   ```bat
   git checkout main
   git pull
   git tag v1.2.0
   git push origin v1.2.0
   ```

4. Watch the **Release** run under Actions. When it finishes, the release page has the notes, `Blendex-1.2.0-Setup.exe` and `Blendex-1.2.0-linux-x86_64.tar.gz`. Edit the notes if needed.

If the version check fails, delete the tag (`git push --delete origin v1.2.0` and `git tag -d v1.2.0`), fix the versions in a pull request and tag again.

To try a build without releasing, use **Run workflow** and download `Blendex-Setup` or `Blendex-linux` from the run's artifacts. Locally, `.venv\Scripts\python packaging\build.py` does the same on Windows (it needs Inno Setup 6: `winget install JRSoftware.InnoSetup`), and `python packaging/build.py` on Linux (Python 3.14, Node 24, the apt packages from `release.yml`).

## Free code signing (SignPath Foundation)

The installer is unsigned for now, so Windows SmartScreen shows "Windows protected your PC" on first run. The [SignPath Foundation](https://signpath.org/) signs open source Windows software for free.

**Eligibility** (full list in the [terms](https://signpath.org/terms)):

- An OSI-approved license (Blendex is MIT) with no proprietary components, actively maintained, and already released in the form to be signed (the GitHub release installer).
- Only binaries built from this repository by an automated CI build are signed. `release.yml` covers this.
- Every team member uses multi-factor authentication on GitHub and SignPath, and each signing request is approved by hand by a project member.
- The certificate is issued to "SignPath Foundation", which shows as the publisher. It can't carry the project's own name.
- The README and download page need a "Code signing policy" section. It must contain the attribution ("Free code signing provided by SignPath.io, certificate by SignPath Foundation"), the team roles (authors, reviewers, approvers, with the people who fill them), and a privacy statement.
- Software that contacts systems the user didn't choose needs a privacy policy and a way to turn that off. The README privacy section is the statement, and anything that goes online must stay opt-in.
- The installer must have an uninstaller (Inno Setup provides one).

**Apply** at <https://signpath.org/apply>, giving the repository URL, the license, a description of what is signed (the Inno Setup installer from `release.yml`) and the team members. Approval is at SignPath's discretion.

**After approval**, SignPath sets up an organization and project. The maintainer then:

1. Adds the predefined GitHub.com trusted build system to the SignPath organization and links it to the project.
2. Creates an artifact configuration with a `<zip-file>` root containing the `<pe-file>` `Blendex-*-Setup.exe`, because `upload-artifact` zips the artifact.
3. Stores the submitter API token as the repository secret `SIGNPATH_API_TOKEN`.
4. Stores the organization id as the repository **variable** `SIGNPATH_ORGANIZATION_ID`, and names the SignPath project `blendex` with the signing policy `release-signing`. `release.yml` already contains the signing steps below and switches them on by itself once the secret and the variable exist; before that, tagged releases stay unsigned.
5. (Already done in `release.yml`.) In `release.yml`, adds a signing step to the `installer` job right after the `upload` step, guarded with `if: github.ref_type == 'tag'`. The step uses `signpath/github-action-submit-signing-request@v3` with `api-token: ${{ secrets.SIGNPATH_API_TOKEN }}`, the organization id, the project and signing policy slugs, `github-artifact-id: ${{ steps.upload.outputs.artifact-id }}`, `wait-for-completion: true` and `output-artifact-directory: signed`. A second `upload-artifact` step then uploads `signed/*.exe` as `Blendex-Setup-signed`. Finally, the `release` job downloads that artifact instead of `Blendex-Setup`. The run waits until an approver accepts the request in SignPath.

The Linux tarball isn't signed; SignPath's Windows signing doesn't apply to it. Only the setup file gets signed this way. Inno Setup compresses `Blendex.exe` into the installer, so signing the app inside too means a SignPath request before `ISCC` runs, which needs `packaging/build.py` split into two steps. SmartScreen only checks the downloaded installer, so start without it.
