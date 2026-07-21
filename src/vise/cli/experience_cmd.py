"""vise experience <subcmd> — experience memory from the CLI.

Wraps the same engine functions used by the MCP experience_query and
experience_stats tools without spawning the MCP server.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _project_dir(args: argparse.Namespace) -> str:
    return getattr(args, "project_dir", None) or str(Path.cwd())


def _cmd_query(args: argparse.Namespace) -> int:
    from vise.engines.experience_memory import (
        get_experience_store,
        get_project_experience_store,
        merge_stores,
        compute_relevance,
    )

    project_dir = _project_dir(args)
    scope = getattr(args, "scope", "project")
    top_n = getattr(args, "top_n", 5)
    min_score = getattr(args, "min_score", 0.5)

    if scope == "global":
        merged = list(get_experience_store().entries)
    elif scope == "both":
        merged = merge_stores(get_experience_store(), get_project_experience_store(project_dir))
    else:
        merged = list(get_project_experience_store(project_dir).entries)

    scored = [
        (entry, compute_relevance(entry, args.file_path))
        for entry in merged
    ]
    scored = [(e, s) for e, s in scored if s > min_score]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:top_n]

    result = {
        "file_path": args.file_path,
        "matches": len(top),
        "total_memories": len(merged),
        "results": [
            {
                "score": round(score, 3),
                "type": entry.type,
                "file_pattern": entry.file_pattern,
                "domain": entry.domain,
                "description": entry.description,
                "severity": entry.severity,
                "confidence": round(entry.confidence, 3),
                "occurrences": entry.occurrences,
                "resolution": entry.resolution or None,
                "scope": entry.scope,
                "last_seen": entry.last_seen,
            }
            for entry, score in top
        ],
        "project_dir": project_dir,
    }

    if getattr(args, "json", False):
        print(json.dumps(result))
        return 0

    print(f"Experience query: {args.file_path}")
    print(f"  {len(merged)} memories, {len(top)} matches")
    for entry, score in top:
        print(f"  [{score:.2f}] {entry.type}  {entry.file_pattern}")
        print(f"         {entry.description[:100]}")
    return 0


def _cmd_gc(args: argparse.Namespace) -> int:
    from vise.engines.experience_memory import (
        GLOBAL_MEMORY_FILE,
        PROJECT_MEMORIES_DIR,
    )
    from vise.engines.experience_gc import gc, protected_ids_for

    project_dir = _project_dir(args)
    apply_flag: bool = getattr(args, "apply", False)
    stats_only: bool = getattr(args, "stats", False)
    as_json: bool = getattr(args, "json", False)

    # Collect protected ids from the current project's asset_journal
    try:
        prot = protected_ids_for(project_dir)
    except Exception:
        prot = set()

    # Determine which stores to process
    project_name = __import__("pathlib").Path(project_dir).name
    project_store_path = PROJECT_MEMORIES_DIR / project_name / "experience_memory.json"

    stores_to_gc = []
    if GLOBAL_MEMORY_FILE.exists():
        stores_to_gc.append(("global", GLOBAL_MEMORY_FILE))
    if project_store_path.exists():
        stores_to_gc.append(("project", project_store_path))

    if not stores_to_gc:
        msg = "No experience stores found."
        if as_json:
            import json as _json
            print(_json.dumps({"message": msg, "reports": []}))
        else:
            print(msg)
        return 0

    reports = []
    for label, store_path in stores_to_gc:
        if stats_only:
            # Just load and report counts, no GC
            try:
                import json as _json
                raw = store_path.read_bytes()
                data = _json.loads(raw)
                entries = data.get("entries", [])
                rep = {
                    "store": label,
                    "store_path": str(store_path),
                    "entries": len(entries),
                    "bytes": len(raw),
                }
                reports.append(rep)
            except Exception as exc:
                reports.append({"store": label, "error": str(exc)})
            continue

        rep = gc(store_path, apply=apply_flag, protected_ids=prot)
        rep["store"] = label
        reports.append(rep)

    if as_json:
        import json as _json
        print(_json.dumps(reports, indent=2))
        return 0

    if stats_only:
        for rep in reports:
            if "error" in rep:
                print(f"  [{rep['store']}] ERROR: {rep['error']}")
            else:
                print(
                    f"  [{rep['store']}] {rep['entries']} entries"
                    f" ({rep['bytes']:,} bytes)  {rep['store_path']}"
                )
        return 0

    # GC report table
    for rep in reports:
        if rep.get("error"):
            print(f"  [{rep['store']}] ERROR: {rep['error']}")
            continue
        mode = "dry-run" if rep["dry_run"] else "applied"
        print(
            f"  [{rep['store']}] {mode}: "
            f"before={rep['before']} "
            f"consolidated={rep['consolidated']} "
            f"dropped={rep['dropped']} "
            f"protected_kept={rep['protected_kept']} "
            f"after={rep['after']} "
            f"bytes {rep['bytes_before']:,} -> {rep['bytes_after']:,}"
        )
        if rep["store_path"]:
            print(f"    store: {rep['store_path']}")

    if not apply_flag:
        print("(dry-run) Pass --apply to execute GC.")

    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    from vise.engines.experience_memory import (
        get_experience_store,
        get_project_experience_store,
        GLOBAL_MEMORY_FILE,
        PROJECT_MEMORIES_DIR,
    )

    project_dir = _project_dir(args)
    global_store = get_experience_store()
    project_store = get_project_experience_store(project_dir)

    global_stats = global_store.stats()
    project_stats = project_store.stats()

    result = {
        "global": global_stats,
        "project": project_stats,
        "combined_total": global_stats["total"] + project_stats["total"],
        "storage": {
            "global_file": str(GLOBAL_MEMORY_FILE),
            "project_file": str(
                PROJECT_MEMORIES_DIR / Path(project_dir).name / "experience_memory.json"
            ),
        },
        "project_dir": project_dir,
    }

    if getattr(args, "json", False):
        print(json.dumps(result))
        return 0

    print(f"Experience stats  project={project_dir}")
    print(f"  global total:  {global_stats['total']}")
    print(f"  project total: {project_stats['total']}")
    print(f"  combined:      {result['combined_total']}")
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("experience", help="Query and inspect experience memory")
    exp_sub = p.add_subparsers(dest="experience_command", metavar="SUBCMD")
    p.set_defaults(func=_make_dispatch(p))

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--project-dir",
        default=None,
        dest="project_dir",
        metavar="DIR",
        help="project directory (default: cwd)",
    )

    # query
    q = exp_sub.add_parser("query", parents=[common], help="query experience memory for a file path")
    q.add_argument("file_path", help="file path to query about")
    q.add_argument("--json", action="store_true", help="Emit JSON")
    q.add_argument("--top-n", type=int, default=5, dest="top_n", metavar="N",
                   help="maximum results to return (default: 5)")
    q.add_argument("--min-score", type=float, default=0.5, dest="min_score", metavar="F",
                   help="minimum relevance score (default: 0.5)")
    q.add_argument(
        "--scope",
        default="project",
        choices=["project", "global", "both"],
        help="memory scope to query (default: project)",
    )
    q.set_defaults(func=_cmd_query)

    # stats
    s = exp_sub.add_parser("stats", parents=[common], help="show experience memory statistics")
    s.add_argument("--json", action="store_true", help="Emit JSON")
    s.set_defaults(func=_cmd_stats)

    # gc
    gc_p = exp_sub.add_parser(
        "gc",
        parents=[common],
        help="garbage-collect and consolidate experience memory (dry-run by default)",
    )
    gc_group = gc_p.add_mutually_exclusive_group()
    gc_group.add_argument("--apply", action="store_true", help="Rewrite stores (atomic). Creates .bak backup.")
    gc_group.add_argument("--stats", action="store_true", dest="stats", help="Print store sizes only, no GC.")
    gc_p.add_argument("--json", action="store_true", help="Emit JSON report")
    gc_p.set_defaults(func=_cmd_gc)


def _make_dispatch(parent: argparse.ArgumentParser):
    def _dispatch(args: argparse.Namespace) -> int:
        if not getattr(args, "experience_command", None):
            parent.print_help()
            return 0
        return args.func(args)
    return _dispatch
