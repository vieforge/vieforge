"""Zero-Dependency Token-Budgeted Repository Map with SQLite Cache (100% stdlib, MIT).

Builds a ranked architectural map of a repository (files, classes, function signatures,
and import relationships) that fits cleanly inside a caller-specified `--budget` token cap:
  - Uses Python `ast` for exact class/function/signature extraction on `.py` files.
  - Uses lightweight regex extractors for JS/TS, Go, Rust, JSON, and Markdown headings.
  - Ranks files by inbound import references (central "hub" modules first) + symbol density.
  - Caches per-file extractions in `<repo>/.vieforge/repo_map_cache.db` keyed by `(mtime_ns, size)`.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".vieforge",
    ".next",
    ".mypy_cache",
    "target",
}
_SUPPORTED_LANGS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".md": "markdown",
    ".json": "json",
}

_JS_SYMBOL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:(?:function|class|interface|type)\s+([A-Za-z_$][A-Za-z0-9_$]*)|"
    r"(?:const|let)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)"
)
_JS_IMPORT_RE = re.compile(r"""(?:import\s+.*?\s+from\s+|require\s*\(\s*)['"]([^'"]+)['"]""")
_GO_SYMBOL_RE = re.compile(r"^(?:func\s+(?:\([^)]+\)\s*)?([A-Za-z_][A-Za-z0-9_]*)|type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:struct|interface))")
_RUST_SYMBOL_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)")
_MD_HEADING_RE = re.compile(r"^(#{1,2})\s+(.+?)\s*$")


@dataclass
class RepoFileSummary:
    rel_path: str
    language: str
    line_count: int
    symbols: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    inbound_refs: int = 0
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RepoMapReport:
    repo_root: str
    total_files: int
    included_files: int
    token_budget: int
    estimated_tokens: int
    cache_hits: int
    files: list[RepoFileSummary] = field(default_factory=list)
    markdown: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "total_files": self.total_files,
            "included_files": self.included_files,
            "token_budget": self.token_budget,
            "estimated_tokens": self.estimated_tokens,
            "cache_hits": self.cache_hits,
            "files": [f.to_dict() for f in self.files],
            "markdown": self.markdown,
        }


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def _format_py_args(args: ast.arguments) -> str:
    names = [a.arg for a in args.posonlyargs] + [a.arg for a in args.args]
    if args.vararg:
        names.append(f"*{args.vararg.arg}")
    names.extend(a.arg for a in args.kwonlyargs)
    if args.kwarg:
        names.append(f"**{args.kwarg.arg}")
    if len(names) > 5:
        return ", ".join(names[:4]) + ", ..."
    return ", ".join(names)


def _extract_python_summary(text: str) -> tuple[list[str], list[str]]:
    symbols: list[str] = []
    imports: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return symbols, imports

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod:
                imports.append(mod)
        elif isinstance(node, ast.ClassDef):
            methods = [
                n.name
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not n.name.startswith("__")
            ][:6]
            method_part = f" [{', '.join(methods)}]" if methods else ""
            symbols.append(f"class {node.name}{method_part}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_") and node.name != "__init__":
                continue
            sig = _format_py_args(node.args)
            symbols.append(f"def {node.name}({sig})")
    return symbols[:24], sorted(set(imports))[:24]


def _extract_generic_summary(lang: str, text: str) -> tuple[list[str], list[str]]:
    symbols: list[str] = []
    imports: list[str] = []
    if lang == "json":
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                symbols = [f"key:{k}" for k in list(obj.keys())[:12]]
        except json.JSONDecodeError:
            pass
        return symbols, imports

    for ln in text.splitlines():
        if lang in ("javascript", "typescript"):
            m = _JS_SYMBOL_RE.match(ln)
            if m:
                sym = m.group(1) or m.group(2)
                if sym:
                    symbols.append(sym)
            for im in _JS_IMPORT_RE.findall(ln):
                imports.append(im)
        elif lang == "go":
            m = _GO_SYMBOL_RE.match(ln)
            if m:
                sym = m.group(1) or m.group(2)
                if sym:
                    symbols.append(sym)
        elif lang == "rust":
            m = _RUST_SYMBOL_RE.match(ln)
            if m:
                symbols.append(m.group(1))
        elif lang == "markdown":
            m = _MD_HEADING_RE.match(ln)
            if m:
                symbols.append(f"{m.group(1)} {m.group(2)[:60]}")
    return symbols[:16], sorted(set(imports))[:16]


def _module_key_from_relpath(rel_path: str) -> str:
    p = rel_path.replace("\\", "/")
    if p.endswith("/__init__.py"):
        p = p[: -len("/__init__.py")]
    elif p.endswith(".py"):
        p = p[:-3]
    return p.replace("/", ".")


def _open_cache(cache_db: Path) -> sqlite3.Connection:
    cache_db.parent.mkdir(parents=True, exist_ok=True)
    gitignore = cache_db.parent / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# Auto-generated by VieForge\n*.db\n*.db-wal\n*.db-shm\n", encoding="utf-8")
    conn = sqlite3.connect(str(cache_db), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS file_summaries (
            rel_path TEXT PRIMARY KEY,
            mtime_ns INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            language TEXT NOT NULL,
            line_count INTEGER NOT NULL,
            symbols_json TEXT NOT NULL,
            imports_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def build_repo_map(
    repo_root: Path | str = ".",
    *,
    token_budget: int = 1200,
    include_tests: bool = False,
    use_cache: bool = True,
    max_file_bytes: int = 512_000,
) -> RepoMapReport:
    """Scan `repo_root` and build a ranked structural map fitting inside `token_budget`."""
    root = Path(repo_root).resolve()
    budget = max(200, int(token_budget))
    cache_db = root / ".vieforge" / "repo_map_cache.db"
    conn = _open_cache(cache_db) if use_cache else None
    cache_hits = 0
    summaries: list[RepoFileSummary] = []

    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
            for fname in sorted(filenames):
                fpath = Path(dirpath) / fname
                lang = _SUPPORTED_LANGS.get(fpath.suffix.lower())
                if not lang:
                    continue
                rel = str(fpath.relative_to(root)).replace("\\", "/")
                if not include_tests and (rel.startswith("tests/") or fname.startswith("test_")):
                    continue
                try:
                    st = fpath.stat()
                except OSError:
                    continue
                if st.st_size > max_file_bytes:
                    continue

                row = None
                if conn is not None:
                    row = conn.execute(
                        "SELECT mtime_ns, size_bytes, language, line_count, symbols_json, imports_json "
                        "FROM file_summaries WHERE rel_path = ?",
                        (rel,),
                    ).fetchone()

                if row is not None and int(row[0]) == st.st_mtime_ns and int(row[1]) == st.st_size:
                    cache_hits += 1
                    summaries.append(
                        RepoFileSummary(
                            rel_path=rel,
                            language=str(row[2]),
                            line_count=int(row[3]),
                            symbols=list(json.loads(row[4])),
                            imports=list(json.loads(row[5])),
                        )
                    )
                    continue

                try:
                    text = fpath.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                line_count = len(text.splitlines())
                if lang == "python":
                    syms, imps = _extract_python_summary(text)
                else:
                    syms, imps = _extract_generic_summary(lang, text)

                summaries.append(
                    RepoFileSummary(
                        rel_path=rel,
                        language=lang,
                        line_count=line_count,
                        symbols=syms,
                        imports=imps,
                    )
                )
                if conn is not None:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO file_summaries
                        (rel_path, mtime_ns, size_bytes, language, line_count, symbols_json, imports_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rel,
                            st.st_mtime_ns,
                            st.st_size,
                            lang,
                            line_count,
                            json.dumps(syms, ensure_ascii=False),
                            json.dumps(imps, ensure_ascii=False),
                        ),
                    )
        if conn is not None:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()

    # Compute inbound references across Python modules and relative JS/TS imports
    mod_to_rel: dict[str, str] = {}
    for s in summaries:
        if s.language == "python":
            mod_to_rel[_module_key_from_relpath(s.rel_path)] = s.rel_path
        stem = Path(s.rel_path).stem
        mod_to_rel.setdefault(stem, s.rel_path)

    inbound_counts: dict[str, int] = {s.rel_path: 0 for s in summaries}
    for s in summaries:
        for imp in s.imports:
            target = mod_to_rel.get(imp) or mod_to_rel.get(imp.split(".")[-1])
            if target and target != s.rel_path:
                inbound_counts[target] = inbound_counts.get(target, 0) + 1

    for s in summaries:
        s.inbound_refs = inbound_counts.get(s.rel_path, 0)
        code_bonus = 2.0 if s.language in ("python", "typescript", "javascript", "go", "rust") else 0.5
        s.score = round((s.inbound_refs * 4.0) + min(len(s.symbols), 12) * 0.6 + code_bonus, 2)

    summaries.sort(key=lambda s: (-s.score, s.rel_path))

    # Pack ranked entries tightly inside `token_budget`
    header = f"# Repo Map: `{root.name}` ({len(summaries)} files scanned, budget={budget} tokens)\n"
    blocks: list[str] = [header]
    used_tokens = _estimate_tokens(header)
    included: list[RepoFileSummary] = []

    for s in summaries:
        ref_tag = f", refs={s.inbound_refs}" if s.inbound_refs > 0 else ""
        max_syms = 8 if s.inbound_refs >= 2 else 5
        sym_list = s.symbols[:max_syms]
        extra_syms = len(s.symbols) - len(sym_list)
        sym_str = ", ".join(sym_list) + (f" (+{extra_syms})" if extra_syms > 0 else "")
        line = f"- `{s.rel_path}` ({s.line_count}L{ref_tag})" + (f": {sym_str}" if sym_str else "")
        tok = _estimate_tokens(line + "\n")
        if used_tokens + tok > budget:
            # Try ultra-compact fallback without symbol list before stopping
            compact = f"- `{s.rel_path}` ({s.line_count}L{ref_tag})"
            ctok = _estimate_tokens(compact + "\n")
            if used_tokens + ctok <= budget:
                blocks.append(compact)
                used_tokens += ctok
                included.append(s)
            continue
        blocks.append(line)
        used_tokens += tok
        included.append(s)

    omitted = len(summaries) - len(included)
    if omitted > 0:
        footer = f"\n*(... {omitted} lower-priority files omitted to stay within {budget}-token budget)*"
        blocks.append(footer)

    md = "\n".join(blocks)
    return RepoMapReport(
        repo_root=str(root),
        total_files=len(summaries),
        included_files=len(included),
        token_budget=budget,
        estimated_tokens=_estimate_tokens(md),
        cache_hits=cache_hits,
        files=included,
        markdown=md,
    )
