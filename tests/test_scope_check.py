"""Static scope check: functions that ASSIGN a module-level global without a
``global`` declaration and READ it before the first local assignment would
raise ``UnboundLocalError`` at runtime (Python treats the name as local
throughout the function). This test walks every module and reports such spots
so the bug class stays dead.

Usage: python tests/test_scope_check.py
"""

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_COMPOUND_SCOPES = (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.ClassDef)


def module_level_names(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue  # names bound inside a def/class body are not module globals
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                names.add(sub.id)
            elif isinstance(sub, ast.Assign):
                for t in sub.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
    # module-level defs/classes names via walk above; also imports
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def analyze_function(func, module_globals, relpath, problems):
    params = set()
    a = func.args
    for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
        params.add(arg.arg)
    if a.vararg:
        params.add(a.vararg.arg)
    if a.kwarg:
        params.add(a.kwarg.arg)

    assigned = set()
    global_declared = set()
    local_only = set()  # locals of nested scopes are irrelevant here

    def collect(node, top_level_only=False):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assigned.add(child.name)
                continue  # separate scopes
            if isinstance(child, ast.Lambda):
                continue
            if isinstance(child, ast.Global):
                global_declared.update(child.names)
            elif isinstance(child, ast.Nonlocal):
                local_only.update(child.names)
            elif isinstance(child, ast.Name):
                if isinstance(child.ctx, ast.Store):
                    assigned.add(child.id)
                elif isinstance(child.ctx, ast.Del):
                    assigned.add(child.id)
            collect(child)

    collect(func)
    # include nested function defs' names as assignments in the enclosing scope
    for child in ast.walk(func):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not func:
            assigned.add(child.name)

    candidates = assigned - params - global_declared - local_only
    if not candidates:
        return

    # read-before-write in statement order (skipping nested scopes)
    seen_store = set()
    func_name = getattr(func, "name", "<lambda>")

    def scan(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                seen_store.add(child.name)
                continue
            if isinstance(child, ast.Lambda):
                continue
            if isinstance(child, ast.Name):
                if isinstance(child.ctx, ast.Load) and child.id in candidates and child.id not in seen_store:
                    if child.id in module_globals:
                        problems.append(
                            f"{relpath}: function '{func_name}' reads global '{child.id}' "
                            "but assigns it locally without 'global' -> UnboundLocalError"
                        )
                elif isinstance(child.ctx, ast.Store):
                    seen_store.add(child.id)
                elif isinstance(child.ctx, ast.Del):
                    seen_store.add(child.id)
            elif isinstance(child, ast.AugAssign):
                if isinstance(child.target, ast.Name) and child.target.id in candidates:
                    if child.target.id in module_globals and child.target.id not in seen_store:
                        problems.append(
                            f"{relpath}: function '{func_name}' aug-assigns global '{child.target.id}' "
                            "without 'global' -> UnboundLocalError"
                        )
                    seen_store.add(child.target.id)
            scan(child)

    scan(func)


def check_file(path) -> list:
    problems = []
    relpath = str(path.relative_to(REPO))
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        problems.append(f"{relpath}: SYNTAX ERROR {e}")
        return problems
    module_globals = module_level_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            analyze_function(node, module_globals, relpath, problems)
    return problems


def main():
    problems = []
    targets = [REPO / "ants", REPO / "nodes.py", REPO / "install.py"]
    files = []
    for t in targets:
        if t.is_dir():
            files.extend(t.rglob("*.py"))
        elif t.exists():
            files.append(t)
    for f in sorted(set(files)):
        problems.extend(check_file(f))
    if problems:
        print(f"FAIL — {len(problems)} scope problem(s):")
        for p in problems:
            print("  " + p)
        return 1
    print("OK — no read-before-write global/local shadowing anywhere.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
