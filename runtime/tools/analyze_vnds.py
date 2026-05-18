#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


REF_COMMANDS_BG = {"bgload"}
REF_COMMANDS_FG = {"setimg"}
REF_COMMANDS_SOUND = {"sound", "music"}
SUPPORTED = {
    "bgload",
    "setimg",
    "text",
    "delay",
    "sound",
    "music",
    "jump",
    "goto",
    "label",
    "if",
    "fi",
    "choice",
    "setvar",
    "gsetvar",
}
COMMON_TYPO = {
    "bglood": "bgload",
    "bglod": "bgload",
    "soind": "sound",
    "deelay": "delay",
}


def norm_name(name: str) -> str:
    return name.replace("\\", "/").strip().lower()


def strip_arg_token(arg: str) -> str:
    arg = arg.strip()
    if not arg or arg == "~":
        return ""
    return arg.split()[0].strip()


def line_parts(line: str) -> tuple[str, str] | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?::|\s+|$)(.*)$", raw)
    if not m:
        return raw.lower(), ""
    cmd, arg = m.group(1), m.group(2)
    cmd = cmd.strip().lower()
    cmd = COMMON_TYPO.get(cmd, cmd)
    return cmd, arg.strip()


def build_file_index(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in root.rglob("*"):
        if p.is_file():
            out.setdefault(norm_name(p.name), p)
            try:
                out.setdefault(norm_name(str(p.relative_to(root))), p)
            except ValueError:
                pass
    return out


def resolve_ref(root: Path, file_index: dict[str, Path], kind: str, ref: str) -> Path | None:
    token = strip_arg_token(ref)
    if not token:
        return None
    token_n = norm_name(token)
    search_dirs = {
        "bg": ["background"],
        "fg": ["foreground", "background"],
        "sound": ["sound"],
        "script": ["script"],
    }.get(kind, [""])
    for d in search_dirs:
        p = root / d / token
        if p.is_file():
            return p
        p = root / d / token_n
        if p.is_file():
            return p
    return file_index.get(token_n)


def analyze(root: Path, first_scene: str | None) -> dict:
    script_dir = root / "script"
    if not script_dir.is_dir():
        raise SystemExit(f"Missing script directory: {script_dir}")

    file_index = build_file_index(root)
    scripts = sorted(script_dir.glob("*.scr"), key=lambda p: p.name.lower())
    commands = Counter()
    unsupported = Counter()
    refs = defaultdict(Counter)
    missing = defaultdict(list)
    choices = []
    scene_stats = {}

    for script in scripts:
        local = Counter()
        local_refs = defaultdict(set)
        text_lines = 0
        max_text = 0
        for line_no, line in enumerate(script.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            parts = line_parts(line)
            if not parts:
                continue
            cmd, arg = parts
            commands[cmd] += 1
            local[cmd] += 1
            if cmd not in SUPPORTED:
                unsupported[cmd] += 1
            if cmd == "text":
                text = arg.strip()
                if text != "~":
                    text_lines += 1
                    max_text = max(max_text, len(text))
            elif cmd in REF_COMMANDS_BG:
                token = strip_arg_token(arg)
                if token:
                    refs["backgrounds"][token] += 1
                    local_refs["backgrounds"].add(token)
                    if not resolve_ref(root, file_index, "bg", token):
                        missing["backgrounds"].append(f"{script.name}:{line_no}: {token}")
            elif cmd in REF_COMMANDS_FG:
                token = strip_arg_token(arg)
                if token:
                    refs["foregrounds"][token] += 1
                    local_refs["foregrounds"].add(token)
                    if not resolve_ref(root, file_index, "fg", token):
                        missing["foregrounds"].append(f"{script.name}:{line_no}: {token}")
            elif cmd in REF_COMMANDS_SOUND:
                token = strip_arg_token(arg)
                if token:
                    refs["audio"][token] += 1
                    local_refs["audio"].add(token)
                    if token != "~" and not resolve_ref(root, file_index, "sound", token):
                        missing["audio"].append(f"{script.name}:{line_no}: {token}")
            elif cmd in {"jump", "goto"}:
                token = strip_arg_token(arg)
                if token and token.lower().endswith(".scr") and not resolve_ref(root, file_index, "script", token):
                    missing["scripts"].append(f"{script.name}:{line_no}: {token}")
            elif cmd == "choice":
                opts = [x for x in arg.split("|") if x]
                choices.append({"script": script.name, "line": line_no, "count": len(opts), "text": arg[:160]})

        scene_stats[script.name] = {
            "commands": dict(local),
            "textLines": text_lines,
            "maxTextLength": max_text,
            "backgrounds": len(local_refs["backgrounds"]),
            "foregrounds": len(local_refs["foregrounds"]),
            "audio": len(local_refs["audio"]),
        }

    candidates = []
    for name, stats in scene_stats.items():
        if name.lower() in {"main.scr", "chapters.scr"}:
            continue
        score = (
            stats["textLines"]
            + stats["backgrounds"] * 8
            + stats["foregrounds"] * 4
            + stats["audio"]
        )
        candidates.append((score, name, stats))
    candidates.sort(key=lambda x: (x[0], x[1].lower()))

    selected = first_scene or (candidates[0][1] if candidates else "")
    return {
        "root": str(root),
        "scriptCount": len(scripts),
        "commands": dict(commands.most_common()),
        "unsupportedCommands": dict(unsupported.most_common()),
        "assetReferences": {k: {"unique": len(v), "total": sum(v.values())} for k, v in refs.items()},
        "missingReferences": {k: v[:50] for k, v in missing.items()},
        "choiceLimits": {
            "totalChoices": len(choices),
            "overRuntimeLimit4": [c for c in choices if c["count"] > 4][:25],
        },
        "recommendedFirstScenes": [
            {"script": name, "score": score, **stats} for score, name, stats in candidates[:8]
        ],
        "selectedFirstScene": selected,
        "selectedStats": scene_stats.get(selected, {}),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze a VNDS-style game folder for WSC VN porting.")
    ap.add_argument("root", type=Path)
    ap.add_argument("--first-scene")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = analyze(args.root, args.first_scene)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    print(f"VNDS folder: {report['root']}")
    print(f"Scripts: {report['scriptCount']}")
    print("\nCommands:")
    for name, count in report["commands"].items():
        print(f"  {name:10s} {count}")
    print("\nAsset references:")
    for kind, data in report["assetReferences"].items():
        print(f"  {kind:12s} {data['unique']} unique, {data['total']} total")
    if report["unsupportedCommands"]:
        print("\nUnsupported commands:")
        for name, count in report["unsupportedCommands"].items():
            print(f"  {name:10s} {count}")
    if report["missingReferences"]:
        print("\nMissing references, first 50 per kind:")
        for kind, items in report["missingReferences"].items():
            print(f"  {kind}: {len(items)} shown")
            for item in items[:10]:
                print(f"    {item}")
    over = report["choiceLimits"]["overRuntimeLimit4"]
    print(f"\nChoices over runtime limit of 4: {len(over)}")
    for item in over[:6]:
        print(f"  {item['script']}:{item['line']} has {item['count']} options")
    print("\nRecommended first scenes:")
    for item in report["recommendedFirstScenes"]:
        print(
            f"  {item['script']:12s} text={item['textLines']:4d} "
            f"bg={item['backgrounds']:3d} fg={item['foregrounds']:3d} audio={item['audio']:4d}"
        )
    print(f"\nSelected first scene: {report['selectedFirstScene']}")


if __name__ == "__main__":
    main()
