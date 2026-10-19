import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import maya_safety

HEAD = ('//Maya ASCII 2027 scene\n//Name: a.ma\nrequires maya "2027";\n'
        'requires -nodeType "aiStandardSurface" "mtoa" "5.6.2";\ncurrentUnit -l centimeter -a degree -t film;\n'
        'fileInfo "application" "maya";\ncreateNode transform -n "pCube1";\n'
        'setAttr ".t" -type "double3" 0 1 0 ;\nsetAttr ".p" -type "string" "a//b/*c;d`e";\n')
REFS = ('file -rdi 1 -ns "child" -rfn "childRN" -typ "mayaAscii" "C:/x/child.ma";\n'
        'file -r -ns "child" -dr 1 -rfn "childRN" -typ "mayaAscii" "C:/x/child.ma";\n')


def scan(tmp_path: Path, text: str, name: str = "a.ma") -> str | None:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return maya_safety.scan_ma(str(path))


@pytest.mark.parametrize("body", ["", REFS, 'requires "stereoCamera" "10.0";\n', "// End of a.ma\n"])
def test_scenes_maya_writes_pass(tmp_path, body):
    assert scan(tmp_path, HEAD + body) is None


@pytest.mark.parametrize(("body", "refused"), [
    ("polyCube;\n", "polyCube"),
    ('setAttr ".a" 1; /* x */ polyCube;\n', "polyCube"),
    ("polyCube", "polyCube"),
    ('file -i "C:/x/other.ma";\n', "file"),
    ('file "C:/x/other.ma";\n', "file"),
    ('file -r -command "x" "C:/x/other.ma";\n', "file"),
    ('requires "C:/x/plugin.py" "1.0";\n', "requires"),
    ('requires "../plugin" "1.0";\n', "requires"),
    ('requires "mtoa" "1.0" "extra";\n', "requires"),
    ('workspace -fr "scene" "C:/x";\n', "workspace"),
    ('setAttr ".a" `polyCube`;\n', "`"),
    ('setAttr ".p" -type "string" "abc\n', "an unterminated string or comment"),
])
def test_other_statements_are_refused(tmp_path, body, refused):
    assert scan(tmp_path, HEAD + body) == refused


def test_content_not_extension_decides_the_format(tmp_path):
    ascii_as_mb = tmp_path / "a.mb"
    ascii_as_mb.write_text(HEAD + "polyCube;\n", encoding="utf-8")
    iff_as_ma = tmp_path / "b.ma"
    iff_as_ma.write_bytes(b"FOR8" + bytes(12) + b"Maya")
    with pytest.raises(maya_safety.UnsafeScene, match="polyCube"), maya_safety.untrusted_scene(str(ascii_as_mb)):
        pass
    assert maya_safety._is_binary(str(iff_as_ma))


def chunk(tag: bytes, data: bytes) -> bytes:
    return tag + bytes(4) + len(data).to_bytes(8, "big") + data + bytes(-len(data) % 8)


def form(kind: bytes, *chunks: bytes) -> bytes:
    return chunk(b"FOR8", kind + b"".join(chunks))


@pytest.mark.parametrize(("plugin", "refused"), [(b"mtoa", False), (b"C:/x/plugin.py", True), (b"../p", True)])
def test_binary_plugin_requests_must_be_plain_names(tmp_path, plugin, refused):
    path = tmp_path / "a.mb"
    head = form(b"HEAD", chunk(b"VERS", b"2027"))
    path.write_bytes(form(b"Maya", head, chunk(b"PLUG", plugin + b"\x001.0\x00"), form(b"XFRM", chunk(b"CREA", b"x"))))
    assert maya_safety.binary_plugins(str(path)) == [plugin.decode()]
    if refused:
        with pytest.raises(maya_safety.UnsafeScene, match="requires"), maya_safety.untrusted_scene(str(path)):
            pass
