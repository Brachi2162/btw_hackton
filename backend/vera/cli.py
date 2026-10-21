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


def cmd_vlm(args: argparse.Namespace) -> None:
    import json
    from concurrent.futures import ThreadPoolExecutor

    import anthropic
    from PIL import Image

    from vera.config import get_settings
    from vera.pipeline.vlm import (
        CacheMiss,
        VlmCache,
        VlmConfig,
        VlmInvalid,
        make_reader,
        render_prompt,
        sender_names,
        to_candidates,
    )

    s = get_settings()
    cfg = VlmConfig.load(s.config_dir)
    mode = args.mode or s.vlm_mode
    # Escalation (spec 8.2.3): a stronger, different model re-reads only the
    # pages that still have an orange/grey field; it counts as its own source.
    model = cfg.escalation_model if args.escalate else cfg.model
    reader_name = "vlm_escalated" if args.escalate else "vlm_full"
    reader = make_reader(
        mode,
        cfg,
        VlmCache(s.data_dir / "cache" / "vlm"),
        s.anthropic_api_key,
        model=model,
    )
    prompt = render_prompt(
        s.prompts_dir, cfg.prompt, cfg.commitment_aliases, sender_names(s.config_dir)
    )

    pages_dir = Path(args.pages)
    manifest = json.loads((pages_dir / "manifest.json").read_text(encoding="utf-8"))
    pages = [p for p in manifest["pages"] if not args.doc or p["doc"] in args.doc]
    if args.escalate:
        subs = json.loads((pages_dir / "submissions.json").read_text(encoding="utf-8"))
        todo = {
            pid
            for sub in subs
            if any(f["status"] in ("review", "missing") for f in sub["fields"].values())
            for pid in sub["page_ids"]
        }
        pages = [p for p in pages if f"{p['doc']}/p{p['page']:02d}" in todo]
    if args.limit:
        pages = pages[: args.limit]

    def run(p: dict) -> tuple[str, dict]:
        page_id = f"{p['doc']}/p{p['page']:02d}"
        try:
            with Image.open(pages_dir / p["image"]) as img:
                r = reader.read_page(img, p["sha256"], prompt)
        except (VlmInvalid, CacheMiss, anthropic.APIError) as e:
            # One bad page (or a network drop) must not sink the batch.
            return page_id, {"error": type(e).__name__, "detail": str(e)[:200]}
        return page_id, {
            "model": r.model,
            "cached": r.cached,
            "usage": r.usage,
            "page_kind": r.reading.page_kind,
            "upright": r.reading.upright,
            "sender_guess": r.reading.sender_guess,
            "page_marker": r.reading.page_marker,
            "candidates": [
                c.model_dump(mode="json")
                for c in to_candidates(r, page_id, reader=reader_name)
            ],
        }

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=cfg.concurrency) as ex:
        out = dict(ex.map(run, pages))

    default = "vlm_escalated.json" if args.escalate else "vlm.json"
    out_path = Path(args.out) if args.out else pages_dir / default
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = [v for v in out.values() if "error" not in v]
    fresh = [v for v in ok if not v["cached"]]
    tokens_in = sum(v["usage"].get("input_tokens", 0) for v in fresh)
    tokens_out = sum(v["usage"].get("output_tokens", 0) for v in fresh)
    print(
        f"{len(ok)}/{len(out)} pages read ({mode}, {model}), "
        f"{len(fresh)} live calls, {tokens_in} in / {tokens_out} out tokens, "
        f"{time.time() - t0:.0f}s -> {out_path}"
    )
    for page_id, v in out.items():
        if "error" in v:
            print(f"  {page_id}: {v['error']} {v['detail']}")


def _ref_date(text: str | None):
    from datetime import date

    return date.fromisoformat(text) if text else date.today()


def cmd_decide(args: argparse.Namespace) -> None:
    import json
    from collections import Counter

    from vera.config import get_settings
    from vera.pipeline.decide import FIELDS, DecisionConfig, decide_page, merge_reads

    s = get_settings()
    cfg = DecisionConfig.load(s.config_dir)
    pages_dir = Path(args.pages)
    vlm = json.loads((pages_dir / "vlm.json").read_text(encoding="utf-8"))
    barcodes = json.loads((pages_dir / "barcodes.json").read_text(encoding="utf-8"))
    esc_path = pages_dir / "vlm_escalated.json"
    escalated = (
        json.loads(esc_path.read_text(encoding="utf-8"))
        if esc_path.is_file() and not args.no_escalated
        else {}
    )
    vlm = {pid: merge_reads(v, escalated.get(pid)) for pid, v in vlm.items()}
    ref = _ref_date(args.ref_date)

    subs = [
        decide_page(
            page_id,
            args.batch,
            v,
            barcodes.get(page_id, {}).get("barcodes", []),
            cfg,
            ref,
        )
        for page_id, v in vlm.items()
    ]
    out_path = Path(args.out) if args.out else pages_dir / "submissions.json"
    out_path.write_text(
        json.dumps(
            [x.model_dump(mode="json") for x in subs], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    print(f"{len(subs)} submissions (ref date {ref}) -> {out_path}")
    print(f"{'field':20s} verified likely review missing")
    for f in FIELDS:
        c = Counter(x.fields[f].status for x in subs)
        print(
            f"{f:20s} {c['verified']:8d} {c['likely']:6d} {c['review']:6d} "
            f"{c['missing']:7d}"
        )
    rows = Counter(x.row_status for x in subs)
    print("rows: " + ", ".join(f"{k} {v}" for k, v in sorted(rows.items())))


def cmd_eval(args: argparse.Namespace) -> None:
    import json
    from datetime import datetime

    from vera.config import get_settings
    from vera.evaluate import dump, evaluate, to_markdown
    from vera.groundtruth import load_ground_truth
    from vera.models import Submission
    from vera.pipeline.decide import DecisionConfig

    s = get_settings()
    cfg = DecisionConfig.load(s.config_dir)
    subs_list = json.loads(Path(args.submissions).read_text(encoding="utf-8"))
    subs = {d["id"]: Submission.model_validate(d) for d in subs_list}
    rep = evaluate(
        subs,
        load_ground_truth(Path(args.gt)),
        cfg.senders,
        _ref_date(args.ref_date),
        split=args.split,
    )
    out_dir = Path(args.reports) / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    md = to_markdown(rep)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    (out_dir / "report.json").write_text(dump(rep), encoding="utf-8")
    print(md.split("## לפי סטטוס")[0])
    print(f"-> {out_dir}")


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

    v = sub.add_parser("vlm", help="vision-model read of extracted pages (cached)")
    v.add_argument("--pages", default="data/work/pages", help="output of `pages`")
    v.add_argument("--doc", nargs="*", help="only these docs, e.g. part2")
    v.add_argument("--limit", type=int, default=0, help="first N pages only")
    v.add_argument("--mode", choices=["live", "replay"], default=None)
    v.add_argument("--out", default=None, help="default: <pages>/vlm.json")
    v.add_argument(
        "--escalate",
        action="store_true",
        help="re-read pages with orange/grey fields with the escalation model",
    )
    v.set_defaults(func=cmd_vlm)

    d = sub.add_parser("decide", help="barcodes + VLM -> statuses per field")
    d.add_argument("--pages", default="data/work/pages", help="output of `pages`")
    d.add_argument("--batch", default="samples")
    d.add_argument("--ref-date", default=None, help="batch reference date, ISO")
    d.add_argument("--out", default=None, help="default: <pages>/submissions.json")
    d.add_argument(
        "--no-escalated", action="store_true", help="ignore vlm_escalated.json"
    )
    d.set_defaults(func=cmd_decide)

    e = sub.add_parser("eval", help="accuracy report against the ground truth")
    e.add_argument("--submissions", default="data/work/pages/submissions.json")
    e.add_argument("--gt", default="eval/ground_truth.xlsx")
    e.add_argument("--split", default="dev", help="dev | test | empty for all")
    e.add_argument("--ref-date", default=None)
    e.add_argument("--reports", default="eval/reports")
    e.set_defaults(func=cmd_eval)

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
