"""Command-line entry point for documentation translation prioritization."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from potodo.arguments_handling import Filters
from potodo.merge import sync_po_and_pot
from potodo.po_file import PoDirectories, PoDirectory

from .priority import PROJECTS, PriorityReport, resolve_translation_paths


def parse_args() -> tuple[argparse.Namespace, PriorityReport]:
    """Parse standalone report options and resolve project layout conventions."""
    parser = argparse.ArgumentParser(
        prog="potodo-docs-priority",
        description="Prioritize documentation translation resources.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        metavar="path",
        help="translation root or PO directory (default: current directory)",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        nargs="+",
        default=[],
        metavar="path",
        help="gitignore-style patterns to exclude from the report",
    )
    parser.add_argument("-a", "--above", default=0, type=int, metavar="X")
    parser.add_argument("-b", "--below", default=100, type=int, metavar="X")
    parser.add_argument("-f", "--only-fuzzy", action="store_true")
    parser.add_argument("--exclude-fuzzy", action="store_true")
    parser.add_argument(
        "-s", "--show-finished", action="store_true", help="include finished resources"
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="do not read or write Potodo's cache"
    )
    parser.add_argument(
        "--pot",
        type=Path,
        help="source template directory to calculate current completion against",
    )
    parser.add_argument("-j", "--json", action="store_true", dest="json_format")
    parser.add_argument(
        "-l",
        "--matching-files",
        action="store_true",
        help="print only matching PO paths in priority order",
    )
    report = PriorityReport()
    report.add_arguments(parser)
    args = parser.parse_args()
    if args.below < args.above:
        parser.error("--below must be greater than or equal to --above")
    if args.only_fuzzy and args.exclude_fuzzy:
        parser.error("--only-fuzzy and --exclude-fuzzy cannot be used together")
    project = PROJECTS[args.project]
    if project.stats_site and args.plausible_stats is None:
        parser.error(f"--plausible-stats is required for --project {project.name}")
    raw_paths = args.paths or [Path.cwd()]
    args.paths = resolve_translation_paths(project, raw_paths, args.language)
    missing = [path for path in args.paths if not any(path.rglob("*.po"))]
    if missing:
        detail = ", ".join(map(str, missing))
        if project.name == "packaging":
            parser.error(
                f"no PO files found in {detail}; packaging translations require "
                "the translation/source branch"
            )
        parser.error(f"no PO files found in {detail}")
    args.filters = Filters(
        above=args.above,
        below=args.below,
        exclude_fuzzy=args.exclude_fuzzy,
        exclude_reserved=False,
        only_reserved=False,
        only_fuzzy=args.only_fuzzy,
    )
    return args, report


def scan_paths(paths: list[Path], *, use_cache: bool) -> PoDirectories:
    """Scan paths without depending on an unreleased Potodo helper API."""
    directories = PoDirectories()
    for path in paths:
        directory = PoDirectory(path, use_cache=use_cache)
        directory.scan()
        directories.append(directory)
    return directories


def main() -> None:
    """Generate a documentation translation priority report."""
    args, report = parse_args()
    if args.pot:
        with TemporaryDirectory() as merge_path:
            sync_po_and_pot(args.paths, args.pot.resolve(), Path(merge_path))
            directories = scan_paths([Path(merge_path)], use_cache=False)
            directories.filter(args.filters, args.exclude)
            report.render(directories, args)
    else:
        directories = scan_paths(args.paths, use_cache=not args.no_cache)
        directories.filter(args.filters, args.exclude)
        report.render(directories, args)
