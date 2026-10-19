"""Rendering benchmark toolkit; see tools/bench/README.md."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from . import report, runner


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.bench", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str, repeat: int = 1) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--repeat", type=int, default=repeat, help="Runs in fresh processes; the median of medians")
        sub.add_argument("--json", metavar="FILE", help="Append each result as one JSON line")
        return sub

    def workload(sub: argparse.ArgumentParser, catalog: str) -> None:
        sub.add_argument("--catalog", default=catalog, help="Built-in catalog name, catalog file, or all")

    def display(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--display",
            choices=("private", "mesa", "real"),
            default="private",
            help="private: Xvfb with the inherited driver; mesa: Xvfb forced to llvmpipe; real: your own display",
        )
        sub.add_argument("--real-swap", action="store_true", help="Keep the real buffer swap on a private display")

    prepare = command("prepare", "Catalog evaluation and drawing preparation, no GPU")
    workload(prepare, "standard")
    prepare.add_argument("--entities", type=int, default=500)
    prepare.add_argument("--frames", type=int, default=60)
    prepare.add_argument("--warmup", type=int, default=10)
    prepare.add_argument("--profile", type=int, default=0, metavar="N", help="Also print the top N functions")

    lanes = command("lanes", "Production viewers in one window, per-stage round times")
    workload(lanes, "standard")
    display(lanes)
    lanes.add_argument("--lanes", type=int, default=4)
    lanes.add_argument("--entities", type=int, default=250)
    lanes.add_argument("--frames", type=int, default=100)
    lanes.add_argument("--warmup", type=int, default=10)
    lanes.add_argument(
        "--decode", action="store_true", help="Read Scenes from a synthetic ADF v1 file as playback does"
    )
    lanes.add_argument("--interaction", choices=("none", "pan", "zoom"), default="none")
    lanes.add_argument("--fps", type=float, default=0.0, help="Pace rounds at this rate; 0 runs back to back")
    lanes.add_argument("--changing-scores", action="store_true", help="Change confidence text and bars every frame")
    lanes.add_argument("--gpu-sync", action="store_true", help="Wait for the GPU after each round (gpu_wait)")
    lanes.add_argument("--hide-feature", action="append", default=[], help="Overlay feature to hide; repeatable")

    sheets = command("sheets", "Render catalog sheets to PNG")
    workload(sheets, "all")
    display(sheets)
    sheets.add_argument("out", help="Folder for OUT/<catalog>/<sheet>.png")

    compare = command("compare", "Run a subcommand on REF and on the working tree, alternating", repeat=5)
    compare.add_argument("ref", help="Any git rev; its detached worktree is reused under the cache folder")
    compare.add_argument("--clean", action="store_true", help="Remove REF's worktree (after the run, if one is given)")
    return parser


def _child(command: str, options: dict[str, object]) -> None:
    result = importlib.import_module(f"tools.bench.{command}").run(options)
    report.emit(runner.RESULT_PREFIX + json.dumps(result))
    sys.stdout.flush()


def _measure(work: argparse.Namespace, ref: str | None, repeat: int, json_file: str | None) -> None:
    """Measure the working tree, or REF and the working tree alternately, once per catalog."""
    sides = [runner.head()] if ref is None else [runner.base(ref), runner.head()]
    catalogs = runner.builtin_catalogs() if work.catalog == "all" else [work.catalog]
    with runner.session(
        getattr(work, "display", "private"), work.command != "prepare", getattr(work, "real_swap", False)
    ) as env:
        for catalog in catalogs:
            runs: dict[str, list[report.Record]] = {side.label: [] for side in sides}
            for _ in range(repeat):
                for side in sides:
                    options = {**vars(work), "catalog": catalog}
                    if work.command == "sheets":
                        folder = Path(work.out) / (side.label if ref else "") / Path(catalog).stem
                        options["out"] = str(folder)
                    runs[side.label].append(runner.run_child(side, work.command, options, env))
            merged = {label: report.merge(records) for label, records in runs.items()}
            report.emit(f"\n{work.command} {catalog} (repeat {repeat})")
            for label, record in merged.items():
                report.emit(f"{label}: {report.describe(record)}")
                if json_file:
                    report.append_json(json_file, record)
            report.emit(report.table(merged))
            if ref and work.command == "sheets":
                from .sheets import pixel_differences

                folders = [Path(work.out) / label / Path(catalog).stem for label in merged]
                for sheet, difference in pixel_differences(*folders).items():
                    report.emit(f"  {sheet:24} {difference}")


def main(argv: list[str] | None = None) -> int:
    """Run the command line."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--child"]:
        _child(argv[1], json.loads(argv[2]))
        return 0
    inner: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, inner = argv[:split], argv[split + 1 :]
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command != "compare":
        _measure(args, None, args.repeat, args.json)
        return 0
    if inner:
        work = parser.parse_args(inner)
        if work.command == "compare":
            parser.error("compare cannot wrap compare")
        _measure(work, args.ref, args.repeat, args.json)
    elif not args.clean:
        parser.error("compare needs -- <prepare|lanes|sheets arguments>")
    if args.clean:
        runner.clean(args.ref)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
