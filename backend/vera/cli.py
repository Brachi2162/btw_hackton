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


def _page_barcodes(path: Path) -> list[dict]:
    from PIL import Image

    from vera.pipeline.barcodes import read_barcodes

    with Image.open(path) as img:
        return [b.to_dict() for b in read_barcodes(img.convert("RGB"))]


def cmd_barcodes(args: argparse.Namespace) -> None:
    import json
    from concurrent.futures import ProcessPoolExecutor

    pages_dir = Path(args.pages)
    manifest = json.loads((pages_dir / "manifest.json").read_text(encoding="utf-8"))
    pages = manifest["pages"]
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(_page_barcodes, [pages_dir / p["image"] for p in pages]))

    out = {
        f"{p['doc']}/p{p['page']:02d}": {"sha256": p["sha256"], "barcodes": bcs}
        for p, bcs in zip(pages, results)
    }
    out_path = Path(args.out) if args.out else pages_dir / "barcodes.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(b) for b in results)
    from_crops = sum(b["via"] == "crop" for bcs in results for b in bcs)
    print(
        f"{total} barcodes on {len(pages)} pages ({from_crops} only in 2x crops) "
        f"-> {out_path} in {time.time() - t0:.0f}s"
    )
    empty = [k for k, v in out.items() if not v["barcodes"]]
    if empty:
        print("no barcode: " + ", ".join(empty))


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

    b = sub.add_parser("barcodes", help="read barcodes on extracted pages")
    b.add_argument("--pages", default="data/work/pages", help="output of `pages`")
    b.add_argument("--out", default=None, help="default: <pages>/barcodes.json")
    b.add_argument("--workers", type=int, default=None)
    b.set_defaults(func=cmd_barcodes)

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
