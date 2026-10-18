# Blendex

A local 3D asset manager for Windows. Point it at your project folders and it indexes every `.blend`, `.obj`, `.fbx`, `.glb`, `.stl`, `.usd` and more, shows thumbnails, lets you orbit a 3D preview by hovering a card for 2 seconds, and opens or imports assets into Blender, including into the Blender project you already have open.

Everything runs on your machine. Nothing is uploaded anywhere.

## Download and run

1. Download `Blendex-<version>-win64.zip` from [Releases](https://github.com/g-s-jithesh/blendex/releases) and unzip it anywhere.
2. Double-click `Blendex\Blendex.exe`. Your browser opens at http://127.0.0.1:8765.
3. Paste a folder path into **Add library folder path…** and click **Add**.

Keep the Blendex console window open while you use it; closing it quits the app. Windows SmartScreen may warn the first time because the exe is not code-signed: choose **More info → Run anyway**.

**Requirements:** Windows 10/11 and [Blender](https://www.blender.org/download/) 4.2 or newer (tested with 5.2 LTS). Blender is used for thumbnails, 3D previews and imports. Without it, `.blend` files still show their saved preview image.

## Features

- **Thumbnails and 3D previews.** `.blend` files show the preview Blender saved in the file instantly, then Blender renders a 512px thumbnail and a compressed 3D proxy in the background. `.obj` and `.fbx` get the same.
- **Hover to orbit.** Hover a card with a **3D** badge for 2 seconds to orbit the model, toggle **Wireframe** (`W`) or **Clay** (`C`), and see the polycount.
- **Live updates.** Save a file in Blender and its card updates by itself.
- **Open with.** Each card's menu opens the file in Blender (non-`.blend` formats are imported into a new scene), shows it in Explorer, or offers Windows' **Open with** picker for any other app.
- **Import into an open Blender.** Click **Install Blender X.Y bridge** in the header once (close Blender first). Every Blender you then open appears in the card menus as **Import into Blender X.Y — yourfile.blend**, and the asset is added to that scene as one undoable step. Supports obj, fbx, glb, gltf, stl, ply, usd/usda/usdc/usdz, abc, and `.blend` (appends its objects).

Blendex stores its index in `%APPDATA%\dam` and caches previews in a hidden `.cache` folder inside each library. Deleting either is safe; they are rebuilt.

## Build from source

Needs Python 3.11+ (3.14 recommended, for reading zstd-compressed `.blend` previews without Blender) and Node.js 20+.

```bat
start.bat
```

runs the development version (backend on port 8765, UI on http://127.0.0.1:5173). To build the portable release:

```bat
.venv\Scripts\python -m pip install -r packaging\requirements.txt
.venv\Scripts\python packaging\build.py
```

The zip lands in `release\`.

Tests: `.venv\Scripts\python -m pytest`, `npm --prefix frontend run test`, `npx --prefix tests/e2e playwright test -c tests/e2e`.
