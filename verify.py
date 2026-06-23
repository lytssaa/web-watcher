import py_compile, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
for f in ["watcher.py", "run_github.py"]:
    py_compile.compile(f, doraise=True)
    print(f"[OK] {f}")
