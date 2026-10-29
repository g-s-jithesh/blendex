import contextlib
import os
import re
from collections.abc import Iterator

# Only what Maya's ASCII writer emits; any other top-level MEL in a downloaded scene runs on open/import.
ALLOWED_MA_COMMANDS = frozenset({
    "requires", "currentUnit", "fileInfo", "createNode", "rename", "setAttr", "addAttr",
    "connectAttr", "disconnectAttr", "relationship", "select", "parent", "lockNode",
    "dataStructure", "file", "applyMetadata", "sceneUIReplacement", "removeMultiInstance",
})
CHECKED_ARGS = frozenset({"file", "requires"})
MAX_CHECKED_STATEMENT = 8192
FILE_FLAGS = frozenset({"-r", "-rdi", "-ns", "-dr", "-rfn", "-typ", "-op", "-shd", "-gl", "-mnc", "-rpr"})
REQUIRES_FLAGS = frozenset({"-nodeType", "-dataType"})
PLUGIN_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|(\S+)')
IFF_MAGIC = (b"FOR4", b"FOR8")
IFF_GROUPS = (b"FOR", b"LIS", b"CAT", b"PRO")


class UnsafeScene(RuntimeError):
    pass


def _args_ok(statement: str) -> bool:
    tokens = [(m[1] is not None, m[2] if m[1] is None else m[1]) for m in TOKEN.finditer(statement)]
    command, args = tokens[0][1], tokens[1:]
    flags = [t for quoted, t in args if not quoted and t.startswith("-")]
    if command == "file":
        return set(flags) <= FILE_FLAGS and bool({"-r", "-rdi"} & set(flags))
    values = [t for quoted, t in args if quoted or not t.startswith("-")]
    return (set(flags) <= REQUIRES_FLAGS and len(values) == len(flags) + 2
            and PLUGIN_NAME.fullmatch(values[len(flags)]) is not None)


def _refused(first: str | None, statement: str) -> bool:
    return bool(first) and (first not in ALLOWED_MA_COMMANDS or first in CHECKED_ARGS and not _args_ok(statement))


def scan_ma(path: str) -> str | None:
    in_str = in_line = in_block = pend_slash = star = esc = False
    first: str | None = None
    word = statement = ""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        while chunk := f.read(1 << 20):
            for ch in chunk:
                if in_line:
                    in_line = ch != "\n"
                    continue
                if in_block:
                    in_block = not (star and ch == "/")
                    star = ch == "*"
                    continue
                if pend_slash and not in_str:
                    pend_slash = False
                    if ch in "/*":
                        in_line, in_block, star = ch == "/", ch == "*", False
                        continue
                    statement += "/"
                if first is None or first in CHECKED_ARGS:
                    statement += ch
                    if len(statement) > MAX_CHECKED_STATEMENT:
                        return first or "an oversized statement"
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == "/":
                    pend_slash = True
                    statement = statement[:-1]
                    continue
                if ch in ("`", "{", "}"):
                    return ch
                if ch == '"':
                    in_str = True
                    if first is None and word:
                        first, word = word, ""
                    continue
                if ch == ";":
                    if first is None and word:
                        first = word
                    if _refused(first, statement[:-1]):
                        return first
                    first, word, statement = None, "", ""
                    continue
                if ch.isalnum() or ch == "_":
                    if first is None:
                        word += ch
                elif word:
                    first, word = word, ""
    if in_str or in_block:
        return "an unterminated string or comment"
    first = first or word or None
    return first if _refused(first, statement) else None


def _neutralize_expression(node, _data) -> None:
    import maya.api.OpenMaya as om

    fn = om.MFnDependencyNode(node)
    try:
        fn.findPlug("nodeState", False).setInt(2)
        fn.findPlug("nodeState", False).setLocked(True)
        fn.findPlug("ixp", False).setString("")
        fn.findPlug("ixp", False).setLocked(True)
    except Exception:  # noqa: BLE001, S110
        pass


def _is_binary(path: str) -> bool:
    with open(path, "rb") as f:
        return f.read(4) in IFF_MAGIC


def binary_plugins(path: str) -> list[str]:
    names, size = [], os.path.getsize(path)
    with open(path, "rb") as f:
        word = 8 if f.read(4) == b"FOR8" else 4
        header = 16 if word == 8 else 8
        pending = [(0, size)]
        while pending:
            pos, end = pending.pop()
            while pos + header <= end:
                f.seek(pos)
                raw = f.read(header)
                tag, length, body = raw[:4], int.from_bytes(raw[header - word:], "big"), pos + header
                if tag[:3] in IFF_GROUPS:
                    pending.append((body + 4, min(body + length, end)))
                elif tag == b"PLUG":
                    names.append(f.read(min(length, 4096)).split(b"\0", 1)[0].decode("utf-8", "replace"))
                pos = body + -(-length // word) * word
    return names


@contextlib.contextmanager
def untrusted_scene(path: str) -> Iterator[None]:
    # Maya picks the ASCII or binary reader from the content, not the extension, so anything not IFF is scanned.
    if path.lower().endswith((".ma", ".mb")):
        if not _is_binary(path):
            bad = scan_ma(path)
        else:
            bad = next((f"requires {n}" for n in binary_plugins(path) if not PLUGIN_NAME.fullmatch(n)), None)
        if bad:
            raise UnsafeScene(f"scene contains commands Blendex will not run: {bad}")
    import maya.api.OpenMaya as om

    # Expression nodes evaluate while the file is still being read (.ma and .mb alike), before any
    # post-load cleanup could run, so each one is disabled the instant it is created.
    callback = om.MDGMessage.addNodeAddedCallback(_neutralize_expression, "expression")
    try:
        yield
    finally:
        om.MMessage.removeCallback(callback)
