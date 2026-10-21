"""Command line entry point: `python -m vera.cli <command> ...`."""

import argparse
import glob
import os
import sys
import time
from pathlib import Path


def _expand(patterns: list[str]) -> list[Path]:
    """Expand globs ourselves (Windows shells don't)."""
    paths: list[Path] = []
    for p in patterns:
        matches = sorted(glob.glob(p))
        if not matches:
            sys.exit(f"no files match: {p}")
        paths += [Path(m) for m in matches]
    return paths


def cmd_pages(args: argparse.Namespace) -> None:
    from vera.pipeline.pages import extract_pages

    t0 = time.time()
    records = extract_pages(
        _expand(args.files),
        Path(args.out),
        dpi=args.dpi,
        orient=not args.no_orient,
        workers=args.workers,
    )
    rotated = [r for r in records if r.rotation]
    print(f"{len(records)} pages -> {args.out} in {time.time() - t0:.0f}s")
    print(
        f"rotated {len(rotated)}: "
        + ", ".join(f"{r.doc} p{r.page} +{r.rotation}" for r in rotated)
    )
    weak = [r for r in records if not args.no_orient and r.orientation_confidence < 3]
    for r in weak:
        print(
            f"  low orientation confidence: {r.doc} p{r.page} "
            f"({r.orientation_confidence}), please check the image"
        )


def cmd_gt_template(args: argparse.Namespace) -> None:
    from vera.groundtruth import load_splits, write_template
    from vera.pipeline.ingest import pdf_page_count

    pages = [
        (p.stem, i)
        for p in _expand(args.files)
        for i in range(1, pdf_page_count(p) + 1)
    ]
    splits = load_splits(Path(args.splits))
    # dev rows first: that's the set to fill (and tune on) first.
    pages.sort(key=lambda dp: (splits.get(dp[0]) != "dev", dp[0], dp[1]))
    write_template(
        pages,
        Path(args.out),
        splits,
        images_dir=Path(args.images) if args.images else None,
        force=args.force,
    )
    print(f"{len(pages)} empty rows -> {args.out}")


def main(argv: list[str] | None = None) -> None:
    # Parallelism comes from our process pool; keep Tesseract single-threaded.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")

    parser = argparse.ArgumentParser(prog="vera")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pages", help="PDF/images -> upright page PNGs + manifest")
    p.add_argument("files", nargs="+", help="files or globs")
    p.add_argument("--out", default="data/work/pages")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--no-orient", action="store_true", help="skip rotation fix")
    p.set_defaults(func=cmd_pages)

    g = sub.add_parser("gt-template", help="empty ground-truth workbook")
    g.add_argument("files", nargs="+", help="sample PDFs or globs")
    g.add_argument("--out", default="eval/ground_truth.xlsx")
    g.add_argument("--splits", default="eval/splits.yaml")
    g.add_argument(
        "--images",
        default="data/work/pages",
        help="page images dir, for the per-row link",
    )
    g.add_argument("--force", action="store_true", help="overwrite (loses edits!)")
    g.set_defaults(func=cmd_gt_template)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
