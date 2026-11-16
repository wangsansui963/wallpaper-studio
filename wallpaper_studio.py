#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wallpaper Studio - personal, local-only batch wallpaper downloader.

Built from a statically decoded gallery protocol (see gallery_spec.json).
Python 3.9+ standard library only; no third-party dependencies.

COMPLIANCE / SCOPE
  * Personal, local, non-commercial use only.
  * Only unauthenticated read-only public endpoints are used. No login,
    token, signature, DRM or paywall bypass of any kind is implemented.
  * Wallpapers remain the property of their authors. Do not redistribute.
  * Polite defaults: low concurrency, a minimum interval between requests,
    resumable downloads and de-duplication to avoid needless traffic.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_SPEC = Path(__file__).with_name("gallery_spec.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}


@dataclass
class Wallpaper:
    id: str
    img_id: str
    source: str
    raw_url: str
    dimensions: str = ""
    like: int = 0
    page: int = 0


class RateLimiter:
    """Global minimum interval between outbound requests (thread-safe)."""

    def __init__(self, min_interval: float = 0.25) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self.min_interval
        delta = slot - time.monotonic()
        if delta > 0:
            time.sleep(delta)


def load_spec(path: str | Path = DEFAULT_SPEC) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def http_get(url: str, *, timeout: float = 15, retries: int = 2,
             headers: dict | None = None) -> tuple[bytes, dict, int]:
    hdr = {"User-Agent": UA, "Accept": "application/json, image/*"}
    if headers:
        hdr.update(headers)
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(), dict(resp.headers), resp.status
        except Exception as exc:  # noqa: BLE001 - network errors are expected
            last = exc
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries + 1} attempts: {last}")


def fetch_list_page(spec: dict, page: int, *, source: str | None = None,
                    order: str | None = None, timeout: float = 15) -> tuple[list, dict]:
    ep = spec["endpoints"]["list"]
    params = dict(ep.get("fixed_params") or {})
    params[spec["pagination"]["param"]] = page
    if source:
        params["source"] = source
    if order:
        params["order"] = order
    url = ep["url"] + "?" + urllib.parse.urlencode(params)
    body, _, _ = http_get(url, timeout=timeout)
    obj = json.loads(body.decode("utf-8", "replace"))
    if obj.get("code") not in (0, "0"):
        raise RuntimeError(f"API code={obj.get('code')} message={obj.get('message')}")
    data = obj.get("data") or {}
    return data.get("list") or [], data


def parse_item(item: dict, page: int = 0) -> Wallpaper:
    src = item.get("src") or {}
    raw = src.get("rawSrc") or item.get("rawUrl") or item.get("url") or ""
    return Wallpaper(
        id=str(item.get("_id") or item.get("id") or ""),
        img_id=str(item.get("imgId") or ""),
        source=str(item.get("source") or "unknown"),
        raw_url=str(raw),
        dimensions=str(item.get("dimensions") or ""),
        like=int(item.get("like") or 0),
        page=page,
    )


def wallpaper_from_dict(d: dict) -> Wallpaper:
    return Wallpaper(
        id=str(d.get("id") or ""),
        img_id=str(d.get("img_id") or ""),
        source=str(d.get("source") or "unknown"),
        raw_url=str(d.get("raw_url") or ""),
        dimensions=str(d.get("dimensions") or ""),
        like=int(d.get("like") or 0),
        page=int(d.get("page") or 0),
    )


def dedupe_key(w: Wallpaper) -> str:
    return w.id or w.img_id or hashlib.sha256(w.raw_url.encode()).hexdigest()[:16]


def convert_url(raw: str, width: int, webp: bool = True) -> str:
    """Reimplementation of the extension's convertURL() imageView2 rule."""
    fmt = "format/webp/" if webp else ""
    return f"{raw}?imageView2/2/w/{int(width)}/{fmt}interlace/1"


def daily_url(spec: dict, kind: str, when: datetime | None = None,
              md5: str = "") -> str:
    when = when or datetime.now()
    tpl = spec["daily"][kind]["template"]
    if kind == "infinity":
        return tpl.format(month=when.month, day=when.day)
    return tpl.format(md5=md5)


def guess_ext(url: str, webp: bool = False) -> str:
    if webp:
        return ".webp"
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in IMAGE_EXTS else ".jpg"


def safe_filename(w: Wallpaper, ext: str) -> str:
    base = f"{w.source}_{w.img_id or w.id}"
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_")[:90]
    return (clean or "wallpaper") + ext


def iter_gallery(spec: dict, *, source: str | None = None,
                 order: str | None = None, pages: int | None = None,
                 max_items: int | None = None, timeout: float = 15,
                 delay: float = 0.3, on_page=None):
    """Yield de-duplicated Wallpaper items across pages."""
    pg = spec["pagination"]
    page = pg["start"]
    seen: set[str] = set()
    yielded = 0
    page_count = 0
    while True:
        items, meta = fetch_list_page(spec, page, source=source,
                                      order=order, timeout=timeout)
        page_count += 1
        if on_page:
            on_page(page, meta, len(items))
        if not items:
            break
        for item in items:
            w = parse_item(item, page)
            if not w.raw_url.startswith("https://"):
                continue
            key = dedupe_key(w)
            if key in seen:
                continue
            seen.add(key)
            yield w
            yielded += 1
            if max_items and yielded >= max_items:
                return
        if pages and page_count >= pages:
            break
        nxt = meta.get("nextPage")
        total = meta.get("totalPages")
        if nxt is None:
            break
        if isinstance(nxt, int) and nxt <= page:
            break
        if isinstance(total, int) and page >= total - 1:
            break
        page = int(nxt) if isinstance(nxt, int) else page + 1
        time.sleep(max(0.0, delay))


def download_one(w: Wallpaper, out_dir: Path, *, url: str | None = None,
                 timeout: float = 20, retries: int = 2,
                 limiter: RateLimiter | None = None,
                 webp: bool = False) -> tuple[str, str, str]:
    """Download one image with resume support. Returns (status, filename, info)."""
    target = url or w.raw_url
    if not target.startswith("https://"):
        return "failed", "", "only https URLs are allowed"
    ext = guess_ext(target, webp=webp)
    name = safe_filename(w, ext)
    dest = out_dir / name
    if dest.exists() and dest.stat().st_size > 0:
        return "skipped", name, str(dest.stat().st_size)
    part = out_dir / (name + ".part")
    last = "unknown error"
    for attempt in range(retries + 1):
        resume = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": UA, "Accept": "image/*"}
        if resume:
            headers["Range"] = f"bytes={resume}-"
        if limiter:
            limiter.wait()
        try:
            req = urllib.request.Request(target, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ctype = resp.headers.get("Content-Type", "").split(";")[0].strip()
                if ctype and not ctype.startswith("image/"):
                    raise RuntimeError(f"unexpected content-type: {ctype}")
                mode = "ab" if resp.status == 206 else "wb"
                with open(part, mode) as fh:
                    shutil.copyfileobj(resp, fh)
            if part.stat().st_size == 0:
                raise RuntimeError("empty download")
            part.replace(dest)
            return "ok", name, str(dest.stat().st_size)
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"[:200]
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    return "failed", name, last


def write_manifest(out_dir: Path, items: list[Wallpaper], fmt: str = "json") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        path = out_dir / "manifest.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(asdict(items[0]).keys()) if items
                                    else list(Wallpaper.__dataclass_fields__))
            writer.writeheader()
            for w in items:
                writer.writerow(asdict(w))
    else:
        path = out_dir / "manifest.json"
        path.write_text(json.dumps({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(items),
            "items": [asdict(w) for w in items],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# CLI commands
# --------------------------------------------------------------------------

def cmd_sources(args, spec) -> None:
    print(f"{'VALUE':<24} NAME")
    for s in spec["sources"]:
        print(f"{s['value']:<24} {s['name']}")
    print("\nNote: 'all' means omit the source parameter (do not send source=all).")


def cmd_verify(args, spec) -> None:
    items, meta = fetch_list_page(spec, spec["pagination"]["start"],
                                  timeout=args.timeout)
    print("HTTP OK  code=0")
    print("count      :", meta.get("count"))
    print("totalPages :", meta.get("totalPages"))
    print("nextPage   :", meta.get("nextPage"))
    print("page size  :", len(items))
    if items:
        w = parse_item(items[0])
        print("sample     :", w.source, w.img_id, w.raw_url)


def cmd_list(args, spec) -> None:
    source = None
    for s in spec["sources"]:
        if s["value"].lower() == args.source.lower():
            source = s["query_value"]
            break
    else:
        raise SystemExit(f"unknown source: {args.source} (use 'sources')")

    def on_page(page, meta, n):
        print(f"  page {page}: {n} items (totalPages={meta.get('totalPages')})")

    items = list(iter_gallery(spec, source=source, order=args.order,
                              pages=args.pages, max_items=args.max_items,
                              timeout=args.timeout, delay=args.delay,
                              on_page=on_page))
    out_dir = Path(args.out).resolve()
    path = write_manifest(out_dir, items, args.format)
    print(f"saved {len(items)} items -> {path}")


def cmd_download(args, spec) -> None:
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.manifest:
        raw = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        rows = raw["items"] if isinstance(raw, dict) else raw
        items = [wallpaper_from_dict(d) for d in rows]
    else:
        source = None
        for s in spec["sources"]:
            if s["value"].lower() == args.source.lower():
                source = s["query_value"]
                break
        else:
            raise SystemExit(f"unknown source: {args.source}")
        items = list(iter_gallery(spec, source=source, order=args.order,
                                  pages=args.pages, max_items=args.max_items,
                                  timeout=args.timeout, delay=args.delay))
    if args.limit:
        items = items[:args.limit]
    write_manifest(out_dir, items, "json")
    print(f"queued {len(items)} wallpapers -> {out_dir}")
    if args.dry_run:
        print("--dry-run: manifest written, nothing downloaded.")
        return

    limiter = RateLimiter(args.delay)
    webp = args.mode != "raw" and args.webp
    counts = {"ok": 0, "skipped": 0, "failed": 0}
    lock = threading.Lock()
    log_path = out_dir / "download.log"
    jsonl_path = out_dir / "manifest.jsonl"

    def work(w: Wallpaper):
        url = w.raw_url if args.mode == "raw" else convert_url(
            w.raw_url, args.width, webp)
        return w, download_one(w, out_dir, url=url, timeout=args.timeout,
                               retries=args.retries, limiter=limiter, webp=webp)

    with open(log_path, "a", encoding="utf-8") as log, \
            open(jsonl_path, "a", encoding="utf-8") as jl:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(work, w) for w in items]
            for fut in as_completed(futures):
                w, (status, name, info) = fut.result()
                with lock:
                    counts[status] = counts.get(status, 0) + 1
                    done = sum(counts.values())
                    line = f"[{done}/{len(items)}] {status:<7} {name or '-'} {info}"
                    print(line)
                    log.write(line + "\n")
                    jl.write(json.dumps({"status": status, "file": name,
                                         "info": info, **asdict(w)},
                                        ensure_ascii=False) + "\n")
                if getattr(args, "progress", None):
                    args.progress(done, len(items), status, name)
    print(f"done: {counts.get('ok', 0)} downloaded, "
          f"{counts.get('skipped', 0)} skipped, {counts.get('failed', 0)} failed")
    print(f"log: {log_path}")


def cmd_daily(args, spec) -> None:
    when = None
    if args.date:
        when = datetime.strptime(args.date, "%Y-%m-%d")
    url = daily_url(spec, args.kind, when, args.md5)
    w = Wallpaper(id=f"daily-{args.kind}", img_id=when.strftime("%Y%m%d") if when
                  else datetime.now().strftime("%Y%m%d"),
                  source=f"daily-{args.kind}", raw_url=url)
    out_dir = Path(args.out).resolve()
    status, name, info = download_one(w, out_dir, timeout=args.timeout,
                                      retries=args.retries)
    print(f"{status}: {name} {info}\nurl: {url}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wallpaper-studio",
        description="Personal local batch wallpaper downloader (decoded gallery protocol)")
    p.add_argument("--spec", default=str(DEFAULT_SPEC), help="path to gallery_spec.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sources", help="list decoded gallery sources")

    v = sub.add_parser("verify", help="one read-only request to verify the API")
    v.add_argument("--timeout", type=float, default=15)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", default="./wallpapers", help="output directory")
    common.add_argument("--source", default="all", help="gallery source value")
    common.add_argument("--order", default=None, help="like | _id")
    common.add_argument("--pages", type=int, default=1, help="max pages to fetch")
    common.add_argument("--max-items", type=int, default=50, help="stop after N items")
    common.add_argument("--timeout", type=float, default=15)
    common.add_argument("--delay", type=float, default=0.3,
                        help="minimum seconds between requests")
    common.add_argument("--format", choices=["json", "csv"], default="json")

    l = sub.add_parser("list", parents=[common], help="fetch and save a gallery manifest")
    l.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)

    d = sub.add_parser("download", parents=[common],
                       help="fetch a manifest and batch download images")
    d.add_argument("--manifest", default=None, help="reuse a saved manifest.json")
    d.add_argument("--limit", type=int, default=None, help="cap number of downloads")
    d.add_argument("--workers", type=int, default=4, help="concurrent downloads")
    d.add_argument("--retries", type=int, default=2)
    d.add_argument("--mode", choices=["raw", "display", "thumbnail"], default="raw")
    d.add_argument("--width", type=int, default=1920,
                   help="imageView2 width for display/thumbnail mode")
    d.add_argument("--webp", action="store_true", help="request webp in transform mode")
    d.add_argument("--dry-run", action="store_true",
                   help="write manifest only, download nothing")

    day = sub.add_parser("daily", help="download the daily wallpaper")
    day.add_argument("--kind", choices=["infinity", "bing"], default="infinity")
    day.add_argument("--date", default=None, help="YYYY-MM-DD (infinity kind)")
    day.add_argument("--md5", default="", help="stored md5 for bing kind")
    day.add_argument("--out", default="./wallpapers")
    day.add_argument("--timeout", type=float, default=20)
    day.add_argument("--retries", type=int, default=2)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    spec = load_spec(args.spec)
    {
        "sources": cmd_sources,
        "verify": cmd_verify,
        "list": cmd_list,
        "download": cmd_download,
        "daily": cmd_daily,
    }[args.cmd](args, spec)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
