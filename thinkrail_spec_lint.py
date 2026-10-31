#!/usr/bin/env python3
"""thinkrail-spec-lint: offline spec-graph consistency linter.

Validates a JetBrains/thinkrail "spec" markdown corpus the same way the
`spec_validate` tool does inside the IDE -- dangling links, duplicate ids and
parent cycles -- but as a standalone CLI with no engine, no in-memory agent
tool and no network. See README for the gap it fills.
"""

import argparse
import json
import os
import sys

FIELDS = ("id", "type", "status", "title", "parent", "depends-on",
          "references", "implements", "covers", "tags")
LINK_KINDS = ("parent", "depends-on", "references", "implements")
SINGLE_LINK_FIELDS = ("parent",)

SKIP_DIRS = {".git", "node_modules", "dist", "build", ".claude",
             ".superpowers", ".gsd", ".mockups", ".playwright-mcp"}

FENCE = "---"


class Error(Exception):
    """Exception raised for reportable operational failures (usage / input)."""


def split_frontmatter(text):
    """Return (frontmatter dict, body) for a markdown file, or (None, body)."""
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.split("\n")
    if not lines or lines[0].strip() != FENCE:
        return None, text
    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == FENCE:
            end = i
            break
    if end == -1:
        return None, text
    fm_text = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1:])
    return _parse_yamlish(fm_text), body


def _parse_yamlish(text):
    """Minimal frontmatter YAML parser (scalars and inline lists only)."""
    fm = {}
    buf = None  # current key
    listbuf = []  # current inline list tokens if collecting
    in_block = None
    for raw in text.split("\n"):
        if in_block:
            if raw.strip().startswith(in_block):
                in_block = None
            continue
        line = raw.rstrip("\n")
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().strip("'\"")
            value = value.strip()
            for token in value.split():
                if token in ("|", ">"):
                    in_block = token
                    buf = None
                    continue
            buf = key
            listbuf = []
            if value:
                vals = _parse_inline(value)
                fm[key] = vals if isinstance(vals, list) else vals
            else:
                fm[key] = ""
        else:
            # continuation of a list   - item
            if buf is not None:
                s = line.strip().lstrip("- ").strip()
                if s:
                    cur = fm.get(buf, [])
                    if not isinstance(cur, list):
                        cur = [cur] if cur else []
                    cur.append(_clean(s))
                    fm[buf] = cur
    return fm


def _parse_inline(value):
    """Parse an inline scalar or `- a - b` list, stripping quotes."""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_clean(x) for x in inner.split(",")]
    if value.startswith("- "):
        parts = [v for v in value.split(" - ") if v.strip()]
        return [_clean(v) for v in parts] if parts else []
    return _clean(value)


def _clean(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    if s.startswith("#"):
        return s[1:].strip()
    return s


def scalar(fm, key):
    v = fm.get(key)
    if isinstance(v, list):
        return str(v[0]) if v else None
    if v is None:
        return None
    v = str(v)
    return v or None


def listval(fm, key):
    v = fm.get(key)
    if isinstance(v, list):
        return [str(x) for x in v if str(x)]
    v = scalar(fm, key)
    return [v] if v else []


def link_targets(fm, kind):
    if kind in SINGLE_LINK_FIELDS:
        t = scalar(fm, kind)
        return [t] if t else []
    return listval(fm, kind)


def is_spec(fm):
    """A file is a spec once its frontmatter carries id and type."""
    return scalar(fm, "id") is not None and scalar(fm, "type") is not None


def scan_files(root):
    """Walk root, yield spec frontmatter entries (path, fm)."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            fm, _ = split_frontmatter(text)
            if fm is None:
                continue
            if is_spec(fm):
                entries.append((path, fm))
    return entries


def build_graph(entries):
    nodes = {}
    paths_by_id = {}
    for path, fm in entries:
        i = scalar(fm, "id")
        if i is None:
            continue
        paths_by_id.setdefault(i, []).append(path)
        if i not in nodes:
            nodes[i] = {"id": i, "type": scalar(fm, "type"),
                        "title": scalar(fm, "title"), "path": path, "fm": fm}

    dangling = []
    for node in nodes.values():
        for kind in LINK_KINDS:
            for target in link_targets(node["fm"], kind):
                if target not in nodes:
                    dangling.append({"from": node["id"], "fromPath": node["path"],
                                     "kind": kind, "target": target})

    duplicates = {i: p for i, p in paths_by_id.items() if len(p) > 1}
    cycles = find_parent_cycles(nodes)
    return nodes, dangling, duplicates, cycles


def find_parent_cycles(nodes):
    cycles = []
    seen = set()
    for start_id in nodes:
        if start_id in seen:
            continue
        path = []
        on_path = {}
        current = start_id
        while current is not None and current in nodes:
            if current in on_path:
                ring = path[on_path[current]:]
                key = tuple(sorted(ring))
                if key not in {tuple(sorted(c)) for c in cycles}:
                    cycles.append(ring)
                break
            if current in seen:
                break
            on_path[current] = len(path)
            path.append(current)
            fm = nodes[current]["fm"]
            current = scalar(fm, "parent")
        for id_ in path:
            seen.add(id_)
    return cycles


def validate(root):
    entries = scan_files(root)
    nodes, dangling, duplicates, cycles = build_graph(entries)
    total = sum(1 for _ in entries)
    return {
        "root": root,
        "specFiles": total,
        "specCount": len(nodes),
        "danglingLinks": dangling,
        "duplicateIds": [{"id": i, "paths": p} for i, p in duplicates.items()],
        "parentCycles": [{"ids": c} for c in cycles],
    }


def is_valid(report):
    return (not report["danglingLinks"] and not report["duplicateIds"]
            and not report["parentCycles"])


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="thinkrail-spec-lint",
        description="Offline spec-graph consistency linter for "
                    "JetBrains/thinkrail-style markdown specs.")
    parser.add_argument("paths", nargs="*", help="file or directory to lint "
                        "(default: cwd)")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON on stdout")
    parser.add_argument("--quiet", action="store_true",
                        help="no human-readable output")
    args = parser.parse_args(argv)

    paths = args.paths or ["."]
    results = []
    for p in paths:
        if not os.path.exists(p):
            print("input error: no such path: %s" % p, file=sys.stderr)
            return 2
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            fm, _ = split_frontmatter(text)
            nodes = {}
            if fm is not None and is_spec(fm):
                nodes = {"p": {"id": scalar(fm, "id"),
                               "type": scalar(fm, "type"),
                               "title": scalar(fm, "title"), "path": p,
                               "fm": fm}}
            report = {
                "root": p, "specFiles": 1 if nodes else 0, "specCount": len(nodes),
                "danglingLinks": [], "duplicateIds": [], "parentCycles": []}
            for node in nodes.values():
                for kind in LINK_KINDS:
                    for target in link_targets(node["fm"], kind):
                        if target not in nodes:
                            report["danglingLinks"].append(
                                {"from": node["id"], "fromPath": p, "kind": kind,
                                 "target": target})
            results.append(report)
        else:
            results.append(validate(p))

    all_valid = all(is_valid(r) for r in results)

    if args.json:
        payload = {"valid": all_valid, "reports": results}
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        for r in results:
            _human(r)

    return 0 if all_valid else 1


def _human(r):
    print("== %s ==" % r["root"])
    print("  spec files: %d  nodes: %d" % (r["specFiles"], r["specCount"]))
    for d in r["danglingLinks"]:
        print("  DANGLE %s[%s] -> %s (%s)" % (d["from"], d["kind"], d["target"],
                                              d["fromPath"]))
    for d in r["duplicateIds"]:
        print("  DUPLICATE %s in %s" % (d["id"], ", ".join(d["paths"])))
    for c in r["parentCycles"]:
        print("  CYCLE %s" % " -> ".join(c["ids"]) + " -> " + c["ids"][0])
    if is_valid(r):
        print("  OK")


if __name__ == "__main__":
    sys.exit(main())