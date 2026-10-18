import { describe, expect, it } from "vitest";
import { sessionFile, sessionLabel } from "../../frontend/src/AssetCard";
import type { Session } from "../../frontend/src/api";

const session = (file: string | null): Session => ({
  id: "blender-1", app: "Blender", version: "5.2", pid: 1, file, exts: ["obj"],
});

describe("session labels", () => {
  it("names the open .blend file for Windows and POSIX paths", () => {
    expect(sessionLabel(session("C:\\proj\\house.blend"))).toBe("Import into Blender 5.2 — house.blend");
    expect(sessionFile(session("/home/a/scene.blend"))).toBe("scene.blend");
  });

  it("calls an unsaved session untitled", () => {
    expect(sessionLabel(session(null))).toBe("Import into Blender 5.2 — untitled");
    expect(sessionFile(session(""))).toBe("untitled");
  });
});
