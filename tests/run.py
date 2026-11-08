import argparse
import ast
import concurrent.futures
import datetime
import fnmatch
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tomllib
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MARK = "JUNO_RUN_RESULT "
DEFAULT_SECONDS = 0.5
TARGET_FULL_SECONDS = 60.0
TARGET_CHANGED_SECONDS = 5.0
TIMINGS = (
    os.environ.get("JUNO_TEST_TIMINGS")
    or "/tmp/juno-test-timings-%s.json" % (hashlib.sha1(ROOT.encode()).hexdigest()[:10])
)
FULL_TRIGGERS = ("tests/run.py", "pyproject.toml", "tests/__init__.py")
FULL_PREFIXES = ("tests/support/", "tests/fixtures/")
SOURCE_DIRS = ("juno/", "tests/")


def rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def module_of(relpath: str) -> str:
    stem = relpath[:-3] if relpath.endswith(".py") else relpath
    parts = stem.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def python_files() -> list[str]:
    found = []
    for top in ("juno", "tests"):
        for base, dirs, files in os.walk(os.path.join(ROOT, top)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            found += [rel(os.path.join(base, f)) for f in files if f.endswith(".py")]
    return sorted(found)


def imports_of(relpath: str, known: set[str]) -> set[str]:
    with open(os.path.join(ROOT, relpath), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    module = module_of(relpath)
    package = module if relpath.endswith("__init__.py") else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join(anchor + ([node.module] if node.module else []))
            found.add(base)
            found.update(
                f"{base}.{alias.name}" for alias in node.names if f"{base}.{alias.name}" in known
            )
    internal = {name for name in found if name.split(".")[0] in ("juno", "tests")}
    for name in list(internal):
        parts = name.split(".")
        internal.update(".".join(parts[:i]) for i in range(1, len(parts)))
    return internal


def load_map() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as handle:
        section = tomllib.load(handle).get("tool", {}).get("juno-tests", {})
    return section.get("inputs", {}), section.get("targets", {})


def build_graph() -> dict[str, set[str]]:
    files = python_files()
    known = {module_of(f) for f in files}
    graph = {module_of(f): imports_of(f, known) for f in files}
    for key, targets in load_map()[1].items():
        graph.setdefault(key, set()).update(targets)
    return graph


def reverse_closure(graph: dict[str, set[str]], changed: set[str]) -> set[str]:
    reverse: dict[str, set[str]] = {}
    for module, deps in graph.items():
        for dep in deps:
            reverse.setdefault(dep, set()).add(module)
    seen = set(changed)
    queue = list(changed)
    while queue:
        for importer in reverse.get(queue.pop(), ()):
            if importer not in seen:
                seen.add(importer)
                queue.append(importer)
    return seen


def forward_closure(graph: dict[str, set[str]], start: str) -> set[str]:
    seen = {start}
    queue = [start]
    while queue:
        for dep in graph.get(queue.pop(), ()):
            if dep not in seen:
                seen.add(dep)
                queue.append(dep)
    return seen


def test_modules(e2e: bool = False) -> list[str]:
    folder = os.path.join(HERE, "e2e") if e2e else HERE
    prefix = "tests.e2e." if e2e else "tests."
    names = sorted(
        f[:-3] for f in os.listdir(folder) if f.startswith("test_") and f.endswith(".py")
    )
    return [prefix + n for n in names]


def input_tests(path: str, inputs: dict[str, list[str]]) -> set[str] | None:
    hits = [tests for pattern, tests in inputs.items() if fnmatch.fnmatch(path, pattern)]
    if not hits:
        return None
    return {f"tests.{name}" for tests in hits for name in tests}


def select(changed_files: list[str]) -> tuple[set[str] | None, list[str]]:
    inputs, _ = load_map()
    graph = build_graph()
    chosen: set[str] = set()
    modules: set[str] = set()
    for path in changed_files:
        if path in FULL_TRIGGERS or path.startswith(FULL_PREFIXES):
            return None, [f"{path} selects the full suite"]
        mapped = input_tests(path, inputs)
        if mapped is not None:
            chosen |= mapped
        elif path.endswith(".py") and path.startswith(SOURCE_DIRS):
            modules.add(module_of(path))
        else:
            return None, [f"{path} is unmapped; full suite"]
    chosen |= {m for m in reverse_closure(graph, modules) if m.startswith("tests.test_")}
    existing = set(test_modules())
    return chosen & existing, []


def unreachable() -> list[str]:
    inputs, targets = load_map()
    graph = build_graph()
    mapped = {f"tests.{n}" for names in inputs.values() for n in names}
    missing = []
    for module in test_modules():
        reach = {m for m in forward_closure(graph, module) if m.startswith("juno")}
        if not reach and module not in mapped and module not in targets:
            missing.append(module)
    return missing


def git(*args: str) -> list[str] | None:
    done = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)
    return None if done.returncode else done.stdout.splitlines()


def changed_files() -> list[str] | None:
    if git("rev-parse", "--verify", "HEAD") is None:
        return None
    tracked = git("diff", "--name-only", "HEAD")
    untracked = git("ls-files", "--others", "--exclude-standard")
    if tracked is None or untracked is None:
        return None
    return sorted(set(tracked) | set(untracked))


def flatten(suite: unittest.TestSuite) -> list[unittest.TestCase]:
    tests: list[unittest.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            tests += flatten(item)
        else:
            tests.append(item)
    return tests


def marks_of(test: unittest.TestCase) -> frozenset[str]:
    method = getattr(test, getattr(test, "_testMethodName", ""), None)
    return frozenset(getattr(type(test), "juno_marks", ())) | frozenset(
        getattr(method, "juno_marks", ())
    )


def worker(module: str, pattern: str, ids: list[str] | None, serial: bool) -> int:
    sys.path.insert(0, ROOT)
    tests = flatten(unittest.defaultTestLoader.loadTestsFromName(module))
    selected, deferred = [], []
    for test in tests:
        tid = test.id()
        if ids is not None and tid not in ids:
            continue
        if pattern and pattern not in tid:
            continue
        if ids is None and not serial and "timing" in marks_of(test):
            deferred.append(tid)
            continue
        selected.append(test)
    suite = unittest.TestSuite(selected)
    stream = io.StringIO()
    outcomes: dict[str, str] = {}

    class Result(unittest.TextTestResult):
        def addSuccess(self, test: unittest.TestCase) -> None:
            super().addSuccess(test)
            outcomes[test.id()] = "PASS"

        def addFailure(self, test: unittest.TestCase, err: object) -> None:
            super().addFailure(test, err)  # type: ignore[arg-type]
            outcomes[test.id()] = "FAIL"

        def addError(self, test: unittest.TestCase, err: object) -> None:
            super().addError(test, err)  # type: ignore[arg-type]
            outcomes[test.id()] = "FAIL"

        def addSkip(self, test: unittest.TestCase, reason: str) -> None:
            super().addSkip(test, reason)
            outcomes[test.id()] = "SKIP"

    started = datetime.datetime.now()
    result = unittest.TextTestRunner(stream=stream, resultclass=Result, verbosity=0).run(suite)
    seconds = (datetime.datetime.now() - started).total_seconds()
    harness = sys.modules.get("tests.e2e.harness")
    payload = {
        "module": module,
        "tests": result.testsRun,
        "ok": result.wasSuccessful(),
        "seconds": seconds,
        "deferred": deferred,
        "outcomes": outcomes,
        "cost": getattr(harness, "COST", 0.0),
        "report": "" if result.wasSuccessful() else stream.getvalue(),
    }
    print(MARK + json.dumps(payload))
    return 0


def run_worker(module: str, pattern: str, ids: list[str] | None, serial: bool) -> dict[str, object]:
    cmd = [sys.executable, os.path.abspath(__file__), "--worker", module]
    if pattern:
        cmd += ["-k", pattern]
    if ids is not None:
        cmd += ["--ids", ",".join(ids)]
    if serial:
        cmd.append("--serial")
    done = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    for line in done.stdout.splitlines():
        if line.startswith(MARK):
            return dict(json.loads(line[len(MARK) :]))
    return {
        "module": module,
        "tests": 0,
        "ok": False,
        "seconds": 0.0,
        "deferred": [],
        "outcomes": {},
        "cost": 0.0,
        "report": f"worker for {module} crashed\n{done.stdout}\n{done.stderr}",
    }


def load_timings() -> dict[str, float]:
    try:
        with open(TIMINGS, encoding="utf-8") as handle:
            data = json.load(handle)
        return {k: float(v) for k, v in data.items()}
    except OSError, ValueError:
        return {}


def save_timings(timings: dict[str, float]) -> None:
    try:
        with open(TIMINGS, "w", encoding="utf-8") as handle:
            json.dump(timings, handle)
    except OSError:
        pass


def execute(modules: list[str], pattern: str, jobs: int, label: str) -> bool:
    started = datetime.datetime.now()
    timings = load_timings()
    order = sorted(modules, key=lambda m: timings.get(m, DEFAULT_SECONDS), reverse=True)
    results: list[dict[str, object]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [pool.submit(run_worker, m, pattern, None, False) for m in order]
        results = [f.result() for f in futures]
    parallel_done = datetime.datetime.now()
    serial_results: list[dict[str, object]] = []
    for item in results:
        deferred = item["deferred"]
        assert isinstance(deferred, list)
        if deferred:
            serial_results.append(run_worker(str(item["module"]), pattern, deferred, True))
    finished = datetime.datetime.now()
    everything = results + serial_results
    total = sum(int(str(r["tests"])) for r in everything)
    failed = [r for r in everything if not r["ok"]]
    for r in failed:
        print(f"--- {r['module']}\n{r['report']}")
    for r in everything:
        timings[str(r["module"])] = float(str(r["seconds"]))
    save_timings(timings)
    slow = sorted(everything, key=lambda r: float(str(r["seconds"])), reverse=True)[:3]
    names = [(str(r["module"]).removeprefix("tests."), float(str(r["seconds"]))) for r in slow]
    print("slowest: " + ", ".join(f"{n} {s:.1f}s" for n, s in names))
    wall = (finished - started).total_seconds()
    print(
        f"{label}: {total} tests in {len(modules)} modules, wall {wall:.1f}s "
        f"(parallel {(parallel_done - started).total_seconds():.1f}s, "
        f"serial {(finished - parallel_done).total_seconds():.1f}s) "
        + ("OK" if not failed else f"FAILED in {len(failed)} modules")
    )
    limit = TARGET_FULL_SECONDS if label == "full" else TARGET_CHANGED_SECONDS
    if wall > limit:
        print(f"warning: {label} run took {wall:.1f}s, target is {limit:.0f}s")
    return not failed


def importtime() -> int:
    hooks = os.path.join(ROOT, "juno", "hooks")
    env = {"PATH": os.environ.get("PATH", ""), "JUNO_DISABLE": "1"}
    for name in sorted(f for f in os.listdir(hooks) if f.endswith(".py") and f != "__init__.py"):
        done = subprocess.run(
            [sys.executable, "-X", "importtime", "-I", "-S", os.path.join(hooks, name)],
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        rows = []
        for line in done.stderr.splitlines():
            match = re.match(r"import time:\s+(\d+) \|\s+(\d+) \| (\s*)(\S+)", line)
            if match:
                rows.append((int(match.group(1)), int(match.group(2)), match.group(4)))
        total = sum(r[0] for r in rows) / 1000
        top = sorted(rows, key=lambda r: r[1], reverse=True)[:3]
        print(
            f"{name[:-3]:<22} {total:6.1f} ms  top: "
            + ", ".join(f"{n} {c / 1000:.1f}" for _, c, n in top)
        )
    return 0


def commit_label() -> str:
    head = git("rev-parse", "--short", "HEAD")
    dirty = git("status", "--porcelain")
    return (head[0] if head else "unknown") + ("+dirty" if dirty else "")


def update_e2e_doc(outcomes: dict[str, str]) -> None:
    path = os.path.join(ROOT, "docs", "e2e.md")
    if not os.path.exists(path):
        return
    per_case: dict[str, str] = {}
    for tid, status in outcomes.items():
        parts = tid.split(".")
        case = "/".join(parts[:-2]) + ".py::" + parts[-2]
        previous = per_case.get(case)
        per_case[case] = (
            "FAIL"
            if "FAIL" in (previous, status)
            else ("PASS" if "PASS" in (previous, status) else "SKIP")
        )
    today = datetime.date.today().isoformat()
    commit = commit_label()
    lines = []
    with open(path, encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            cells = line.split("|")
            if len(cells) >= 9 and cells[2].strip() in per_case:
                case = cells[2].strip()
                cells[4], cells[5], cells[6] = f" {today} ", f" {per_case[case]} ", f" {commit} "
                line = "|".join(cells)
            lines.append(line)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run_e2e(pattern: str) -> int:
    all_outcomes: dict[str, str] = {}
    cost = 0.0
    ok = True
    for module in test_modules(e2e=True):
        item = run_worker(module, pattern, None, True)
        outcomes = item["outcomes"]
        assert isinstance(outcomes, dict)
        all_outcomes.update(outcomes)
        cost += float(str(item["cost"]))
        if not item["ok"]:
            ok = False
            print(f"--- {module}\n{item['report']}")
    counts = {s: list(all_outcomes.values()).count(s) for s in ("PASS", "FAIL", "SKIP")}
    print(f"e2e: {counts} total cost ${cost:.4f}")
    update_e2e_doc(all_outcomes)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        del os.environ[key]
    parser = argparse.ArgumentParser(prog="tests/run.py")
    parser.add_argument("mode", nargs="?", choices=["changed"], help="run only affected tests")
    parser.add_argument("-k", dest="pattern", default="", help="run tests whose id contains PAT")
    parser.add_argument("-j", dest="jobs", type=int, default=min(8, os.cpu_count() or 4))
    parser.add_argument("--importtime", action="store_true")
    parser.add_argument("--e2e", action="store_true", help="run the end-to-end cases serially")
    parser.add_argument("--install-hooks", action="store_true")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--ids", help=argparse.SUPPRESS)
    parser.add_argument("--serial", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        ids = args.ids.split(",") if args.ids else None
        return worker(args.worker, args.pattern, ids, args.serial)
    if args.install_hooks:
        done = subprocess.run(["git", "-C", ROOT, "config", "core.hooksPath", ".githooks"])
        return done.returncode
    if args.importtime:
        return importtime()
    if args.e2e:
        return run_e2e(args.pattern)
    modules = test_modules()
    label = "full"
    if args.mode == "changed":
        files = changed_files()
        chosen, reasons = (
            (None, ["git state unavailable; full suite"]) if files is None else select(files)
        )
        if chosen is not None:
            if not chosen:
                print("changed: no test is affected")
                return 0
            modules, label = sorted(chosen), "changed"
        else:
            print("changed: " + "; ".join(reasons))
    return 0 if execute(modules, args.pattern, args.jobs, label) else 1


if __name__ == "__main__":
    sys.exit(main())
