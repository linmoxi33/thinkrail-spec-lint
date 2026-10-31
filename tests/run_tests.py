#!/usr/bin/env python3
"""Test suite for thinkrail-spec-lint (offline, no network)."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(ROOT, "..", "thinkrail_spec_lint.py")
BIN = os.path.abspath(BIN)


def run(args, cwd="."):
    return subprocess.run([sys.executable, BIN] + args, cwd=cwd,
                          capture_output=True, text=True)


def make_spec(d, path, fm, body="Body"):
    full = os.path.join(d, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write("---\n%s\n---\n\n%s\n" % (fm.strip(), body))


class _C:
    pass


def count():
    C = _C()
    C.n = 0
    return C


def track(tests, f):
    tname = f.__name__
    d = tempfile.mkdtemp(prefix="specs")
    try:
        f(d)
        # run with --json covering the whole dir
        p = run(["--json", d], cwd=d)
        report = json.loads(p.stdout) if p.stdout else {}
        tests.append({"name": tname, "pass": True, "rc": p.returncode,
                      "report": report})
    except AssertionError as e:
        tests.append({"name": tname, "pass": False, "rc": None, "err": str(e)})
    finally:
        pass


def t_valid_dir(d):
    """A clean, mutually-referencing graph exits 0 and parses as JSON."""
    make_spec(d, "a.md", "id: a1\ntype: module-design\ntitle: A\nparent: b1")
    make_spec(d, "b.md", "id: b1\ntype: task-spec\ntitle: B")
    p = run(["--json", d], cwd=d)
    assert p.returncode == 0, "clean graph should exit 0: %s" % p.stdout
    data = json.loads(p.stdout)
    assert data["valid"] is True


def t_dangling_exit1(d):
    """A dangling parent link exits 1 with no json crash."""
    make_spec(d, "a.md", "id: a1\ntype: module-design\ntitle: A\nparent: missing")
    p = run(["--json", d], cwd=d)
    assert p.returncode == 1
    data = json.loads(p.stdout)
    assert data["valid"] is False
    assert any(x["target"] == "missing" for x in data["reports"][0]["danglingLinks"])


def t_duplicate_id(d):
    """Duplicate id across two files reported."""
    make_spec(d, "x1.md", "id: same\ntype: task-spec\ntitle: one")
    make_spec(d, "x2.md", "id: same\ntype: task-spec\ntitle: two")
    p = run(["--json", d], cwd=d)
    assert p.returncode == 1
    data = json.loads(p.stdout)
    assert len(data["reports"][0]["duplicateIds"]) == 1
    assert data["reports"][0]["duplicateIds"][0]["id"] == "same"


def t_parent_cycle(d):
    """a->b->a parent cycle detected."""
    make_spec(d, "a.md", "id: cyc\ntype: module-design\ntitle: A\nparent: cyb")
    make_spec(d, "b.md", "id: cyb\ntype: module-design\ntitle: B\nparent: cyc")
    p = run(["--json", d], cwd=d)
    assert p.returncode == 1
    data = json.loads(p.stdout)
    assert len(data["reports"][0]["parentCycles"]) == 1


def t_depends_on_list_dangle(d):
    """depends-on list field dangling link detected."""
    make_spec(d, "a.md", "id: a1\ntype: module-design\ntitle: A\ndepends-on: [nope, b1]")
    make_spec(d, "b.md", "id: b1\ntype: task-spec\ntitle: B")
    p = run(["--json", d], cwd=d)
    data = json.loads(p.stdout)
    assert data["valid"] is False
    got = [x["target"] for x in data["reports"][0]["danglingLinks"]]
    assert "nope" in got and "b1" not in got


def t_self_cycle(d):
    """parent pointing to itself is a cycle."""
    make_spec(d, "a.md", "id: selfx\ntype: module-design\ntitle: A\nparent: selfx")
    p = run(["--json", d], cwd=d)
    data = json.loads(p.stdout)
    assert data["valid"] is False
    assert len(data["reports"][0]["parentCycles"]) == 1


def t_skips_node_modules(d):
    """node_modules dir is skipped and not a spec source."""
    make_spec(d, "node_modules/pkg.md", "id: hidden\ntype: task-spec\ntitle: H")
    p = run(["--json", d], cwd=d)
    data = json.loads(p.stdout)
    assert data["reports"][0]["specCount"] == 0


def t_non_spec_no_id(d):
    """File without id+type is not counted as a spec."""
    make_spec(d, "notes.md", "title: no id here\ntype: task-spec")
    p = run(["--json", d], cwd=d)
    data = json.loads(p.stdout)
    assert data["reports"][0]["specCount"] == 0


def t_usage_missing_path(d):
    """Nonexistent path exits 2 (usage/input error)."""
    p = run([os.path.join(d, "nope")], cwd=d)
    assert p.returncode == 2


def t_single_file_lint(d):
    """Linting a single file directly works and reports a dangling link."""
    make_spec(d, "a.md", "id: a1\ntype: module-design\ntitle: A\nparent: gone")
    p = run(["--json", os.path.join(d, "a.md")], cwd=d)
    assert p.returncode == 1
    data = json.loads(p.stdout)
    assert data["reports"][0]["root"].endswith("a.md")
    assert len(data["reports"][0]["danglingLinks"]) == 1


def t_json_is_parseable_with_python(d):
    """--json output parses with python json.load (hardening)."""
    make_spec(d, "a.md", "id: a1\ntype: module-design\ntitle: A")
    p = subprocess.run("python3 -c \"import json,sys;json.load(sys.stdin)\" ",
                       cwd=d, capture_output=True, text=True, shell=True,
                       input=p.stdout if False else "")
    # use our own json parse via python -c reading our BIN output
    p2 = subprocess.run(
        "python3 %s --json %s | python3 -c 'import json,sys;json.load(sys.stdin)'"
        % (BIN, d), cwd=d, shell=True, capture_output=True, text=True)
    assert p2.returncode == 0, p2.stderr


def t_dedup_spec_dir(d):
    """A directory passed explicitly is scanned (not only cwd)."""
    make_spec(d, "a.md", "id: a1\ntype: module-design\ntitle: A\nparent: zzz")
    p = run(["--json", d], cwd="/tmp")
    data = json.loads(p.stdout)
    assert data["valid"] is False


def t_cycle_ring_order_reported(d):
    """Three-node cycle reported once."""
    make_spec(d, "a.md", "id: c1\ntype: module-design\ntitle: A\nparent: c2")
    make_spec(d, "b.md", "id: c2\ntype: module-design\ntitle: B\nparent: c3")
    make_spec(d, "c.md", "id: c3\ntype: module-design\ntitle: C\nparent: c1")
    p = run(["--json", d], cwd=d)
    data = json.loads(p.stdout)
    cycles = data["reports"][0]["parentCycles"]
    assert len(cycles) == 1
    assert len(cycles[0]["ids"]) == 3


def t_no_dangle_when_link_defined(d):
    """Implements-to-existing-target is not a dangling link."""
    make_spec(d, "a.md", "id: a1\ntype: module-design\ntitle: A\nimplements: [b1]")
    make_spec(d, "b.md", "id: b1\ntype: task-spec\ntitle: B")
    p = run(["--json", d], cwd=d)
    data = json.loads(p.stdout)
    assert data["valid"] is True


TESTS = [t_valid_dir, t_dangling_exit1, t_duplicate_id, t_parent_cycle,
         t_depends_on_list_dangle, t_self_cycle, t_skips_node_modules,
         t_non_spec_no_id, t_usage_missing_path, t_single_file_lint,
         t_json_is_parseable_with_python, t_dedup_spec_dir,
         t_cycle_ring_order_reported, t_no_dangle_when_link_defined]


def main():
    results = []
    for t in TESTS:
        results.append((t.__name__, t))
    passed = 0
    for name, fn in results:
        d = tempfile.mkdtemp(prefix="specs_")
        try:
            fn(d)
            passed += 1
            print("PASS %s" % name)
        except AssertionError as e:
            print("FAIL %s: %s" % (name, e))
        except Exception as e:  # noqa
            print("FAIL %s: %s" % (name, e))
    total = len(results)
    print("\n%d/%d tests passed" % (passed, total))
    # behavior-test counting: >= 3 test behavior (input->output/exit codes)
    behavior = {"t_valid_dir", "t_dangling_exit1", "t_duplicate_id",
                "t_parent_cycle", "t_usage_missing_path", "t_dedup_spec_dir"}
    print("behavior tests run: %d" % len(
        [n for n, _ in results if n in behavior]))
    if passed < total:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())