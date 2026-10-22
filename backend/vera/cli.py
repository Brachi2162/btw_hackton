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

    from vera.config import get_settings
    from vera.pipeline.decide import merge_reads
    from vera.pipeline.process import crop_region, page_id_of, read_crop, read_vlm
    from vera.pipeline.vlm import VlmCache, VlmConfig, make_reader, render_prompt
    from vera.pipeline.vlm import sender_names

    s = get_settings()
    cfg = VlmConfig.load(s.config_dir)
    mode = args.mode or s.vlm_mode
    # Escalation (spec 8.2.3): a stronger, different model re-reads only the
    # pages that still have an orange/grey field; it counts as its own source.
    # Crops (spec 8.2.2): a third model reads a full-resolution crop of the
    # patient area, for names/IDs that two sources haven't confirmed.
    # Crop check (D-012): another provider reads the same tight crop.
    if args.crop_check:
        if not cfg.crop_check_model:
            raise SystemExit("crop_check_model is empty in config/vlm.yaml")
        model, reader_name = cfg.crop_check_model, "vlm_crop_check"
        default = "vlm_crop_check.json"
    elif args.crops:
        model, reader_name, default = cfg.crop_model, "vlm_crop", "vlm_crops.json"
    elif args.escalate:
        model, reader_name = cfg.escalation_model, "vlm_escalated"
        default = "vlm_escalated.json"
    else:
        model, reader_name, default = cfg.model, "vlm_full", "vlm.json"
    reader = make_reader(
        mode,
        cfg,
        VlmCache(s.data_dir / "cache" / "vlm"),
        s.anthropic_api_key,
        model=model,
        gemini_api_key=s.gemini_api_key,
    )
    prompt = render_prompt(
        s.prompts_dir, cfg.prompt, cfg.commitment_aliases, sender_names(s.config_dir)
    )

    pages_dir = Path(args.pages)
    manifest = json.loads((pages_dir / "manifest.json").read_text(encoding="utf-8"))
    pages = [p for p in manifest["pages"] if not args.doc or p["doc"] in args.doc]
    crops = args.crops or args.crop_check
    if args.escalate or crops:
        from vera.models import Submission
        from vera.pipeline.process import needs_crop, needs_escalation

        need = needs_crop if crops else needs_escalation
        subs = json.loads((pages_dir / "submissions.json").read_text(encoding="utf-8"))
        todo = {
            pid
            for sub in subs
            if need(Submission.model_validate(sub))
            for pid in sub["page_ids"]
        }
        pages = [p for p in pages if page_id_of(p["doc"], p["page"]) in todo]
    if crops:
        crop_prompt = (s.prompts_dir / f"{cfg.crop_prompt}.md").read_text(
            encoding="utf-8"
        )

        def _load(name):
            path = pages_dir / name
            return (
                json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            )

        full, esc = _load("vlm.json"), _load("vlm_escalated.json")
        done = _load("vlm_crops.json")
    if args.limit:
        pages = pages[: args.limit]

    def run(p: dict) -> tuple[str, dict]:
        page_id = page_id_of(p["doc"], p["page"])
        try:
            if crops:
                region = crop_region(merge_reads(full.get(page_id), esc.get(page_id)))
                box = None
                if args.crop_check:
                    # The same box the crop reader settled on, or skip.
                    first = done.get(page_id) or {}
                    if "box" not in first:
                        return page_id, {"error": "NoCrop", "detail": "run --crops"}
                    box = tuple(first["box"])
                return page_id, read_crop(
                    reader,
                    pages_dir / p["image"],
                    p["sha256"],
                    crop_prompt,
                    cfg.crop_prompt,
                    cfg.crop_max_side,
                    page_id,
                    region,
                    zoom=cfg.crop_zoom,
                    box=box,
                    reader_name=reader_name,
                )
            return page_id, read_vlm(
                reader,
                pages_dir / p["image"],
                p["sha256"],
                prompt,
                page_id,
                reader_name,
            )
        except anthropic.APIError as e:
            # One bad page (or a network drop) must not sink the batch.
            return page_id, {"error": type(e).__name__, "detail": str(e)[:200]}

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=cfg.concurrency) as ex:
        out = dict(ex.map(run, pages))

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


def cmd_ocr(args: argparse.Namespace) -> None:
    """Google Vision OCR of the pages (D-009) -> <pages>/ocr.json (cached)."""
    import json
    from concurrent.futures import ThreadPoolExecutor

    from PIL import Image

    from vera.config import get_settings
    from vera.pipeline.gvision import VisionOcr
    from vera.pipeline.vlm import CacheMiss, VlmInvalid

    s = get_settings()
    mode = args.mode or s.vlm_mode
    if mode == "live" and not s.google_vision_api_key:
        raise SystemExit("GOOGLE_VISION_API_KEY is empty in .env")
    ocr = VisionOcr(
        s.data_dir / "cache" / "gvision", s.google_vision_api_key, mode=mode
    )
    pages_dir = Path(args.pages)
    manifest = json.loads((pages_dir / "manifest.json").read_text(encoding="utf-8"))
    pages = [p for p in manifest["pages"] if not args.doc or p["doc"] in args.doc]

    def run(p):
        page_id = f"{p['doc']}/p{p['page']:02d}"
        try:
            with Image.open(pages_dir / p["image"]) as img:
                return page_id, {"lines": ocr.read(img, p["sha256"])}
        except (VlmInvalid, CacheMiss) as e:
            return page_id, {"error": type(e).__name__, "detail": str(e)[:200]}

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        out = dict(ex.map(run, pages))
    out_path = Path(args.out) if args.out else pages_dir / "ocr.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    ok = [v for v in out.values() if "error" not in v]
    words = sum(len(ln["words"]) for v in ok for ln in v["lines"])
    print(
        f"{len(ok)}/{len(out)} pages OCR'd ({mode}), {words} words, "
        f"{time.time() - t0:.0f}s -> {out_path}"
    )
    for page_id, v in out.items():
        if "error" in v:
            print(f"  {page_id}: {v['error']} {v['detail']}")


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
    crop_path = pages_dir / "vlm_crops.json"
    crops = (
        json.loads(crop_path.read_text(encoding="utf-8"))
        if crop_path.is_file() and not args.no_escalated
        else {}
    )
    check_path = pages_dir / "vlm_crop_check.json"
    checks = (
        json.loads(check_path.read_text(encoding="utf-8"))
        if check_path.is_file() and not args.no_escalated
        else {}
    )
    ocr_path = pages_dir / "ocr.json"
    ocr = (
        json.loads(ocr_path.read_text(encoding="utf-8"))
        if ocr_path.is_file() and not args.no_ocr
        else {}
    )
    vlm = {
        pid: merge_reads(v, escalated.get(pid), crops.get(pid), checks.get(pid))
        for pid, v in vlm.items()
    }
    ref = _ref_date(args.ref_date)

    subs = [
        decide_page(
            page_id,
            args.batch,
            v,
            barcodes.get(page_id, {}).get("barcodes", []),
            cfg,
            ref,
            ocr=ocr.get(page_id, {}).get("lines"),
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


def _batch_service():
    from vera.batch import BatchService
    from vera.config import get_settings
    from vera.services import build_services

    return BatchService(build_services(get_settings()))


def cmd_redecide(args: argparse.Namespace) -> None:
    n = _batch_service().redecide(args.batch)
    print(f"{n} forms decided again under the current rules (no model calls)")


def cmd_seed_demo(args: argparse.Namespace) -> None:
    from vera.demo import seed

    t = seed(_batch_service(), weeks=args.weeks)
    print(
        f"demo data: {t['batches']} batches, {t['forms']} forms, {t['tubes']} tubes "
        "(marked demo=true; remove with clear-demo)"
    )


def cmd_clear_demo(args: argparse.Namespace) -> None:
    from vera.demo import clear

    print(f"removed {clear(_batch_service())['batches_removed']} demo batches")


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
        "--crops",
        action="store_true",
        help="crop reads of the patient area for unconfirmed names/IDs",
    )
    v.add_argument(
        "--crop-check",
        action="store_true",
        help="the same crops through crop_check_model, another provider (D-012)",
    )
    v.add_argument(
        "--escalate",
        action="store_true",
        help="re-read pages with orange/grey fields with the escalation model",
    )
    v.set_defaults(func=cmd_vlm)

    o = sub.add_parser("ocr", help="Google Vision OCR of extracted pages (cached)")
    o.add_argument("--pages", default="data/work/pages", help="output of `pages`")
    o.add_argument("--doc", nargs="*", help="only these docs, e.g. part2")
    o.add_argument("--mode", choices=["live", "replay"], default=None)
    o.add_argument("--out", default=None, help="default: <pages>/ocr.json")
    o.set_defaults(func=cmd_ocr)

    d = sub.add_parser("decide", help="barcodes + VLM -> statuses per field")
    d.add_argument("--pages", default="data/work/pages", help="output of `pages`")
    d.add_argument("--batch", default="samples")
    d.add_argument("--ref-date", default=None, help="batch reference date, ISO")
    d.add_argument("--out", default=None, help="default: <pages>/submissions.json")
    d.add_argument(
        "--no-escalated", action="store_true", help="ignore vlm_escalated.json"
    )
    d.add_argument("--no-ocr", action="store_true", help="ignore ocr.json")
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

    rd = sub.add_parser(
        "redecide", help="decide stored reads again under the current rules"
    )
    rd.add_argument("--batch", default=None, help="one batch id (default: all)")
    rd.set_defaults(func=cmd_redecide)

    sd = sub.add_parser("seed-demo", help="made-up history for the demo (demo=true)")
    sd.add_argument("--weeks", type=int, default=4)
    sd.set_defaults(func=cmd_seed_demo)

    cd = sub.add_parser("clear-demo", help="remove everything seed-demo created")
    cd.set_defaults(func=cmd_clear_demo)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
