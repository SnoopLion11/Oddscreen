"""Run verify_devig.py's test suite against the PRODUCTION de-vig code.

verify_devig.py is the reference implementation and test suite. Rather than
re-typing its checks (and risking drift), this loads the file, removes its
reference function definitions, and injects production functions under the
same names. Everything else in the file — every check, every tolerance —
runs exactly as written.

The server calls run() at startup and refuses to show prices unless it
returns 0 failures. Also runnable directly:  python -m oddscreen.selftest
"""
from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path

from . import devig

VERIFY_FILE = Path(__file__).resolve().parent.parent / "verify_devig.py"

# Reference definitions replaced by production code.
_REPLACED = {"implied", "mult", "additive", "power", "shin_m", "worst", "ev_pct"}


def _worst(p, i):
    sides = devig.devig_market(p)
    side = sides[i]
    return side.fair, side.method, side.by_method


def _production_namespace() -> dict:
    return {
        "__name__": "verify_devig_production",
        "implied": devig.implied_from_american,
        "mult": devig.multiplicative,
        "additive": devig.additive,
        "power": devig.power,
        "shin_m": devig.shin,
        "worst": _worst,
        "ev_pct": devig.ev_pct,
    }


def _patched_module(path: Path) -> ast.Module:
    tree = ast.parse(path.read_text(), filename=str(path))
    found = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in _REPLACED}
    missing = _REPLACED - found
    if missing:
        raise RuntimeError(f"{path.name} no longer defines {sorted(missing)}; update selftest._REPLACED")
    tree.body = [n for n in tree.body if not (isinstance(n, ast.FunctionDef) and n.name in _REPLACED)]
    return tree


def run(verbose: bool = False, path: Path = VERIFY_FILE) -> tuple[int, str]:
    """Returns (failure_count, captured_output)."""
    code = compile(_patched_module(path), str(path), "exec")
    ns = _production_namespace()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(code, ns)
    out = buf.getvalue()
    if verbose:
        print(out, end="")
    return int(ns["fails"]), out


if __name__ == "__main__":
    fails, _ = run(verbose=True)
    raise SystemExit(1 if fails else 0)
