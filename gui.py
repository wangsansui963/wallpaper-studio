#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wallpaper Studio - optional Tkinter GUI (personal, local use only).

Launch:  python gui.py
Requires only Python's standard library.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import wallpaper_studio as ws


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.spec = ws.load_spec()
        self.q: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self.worker: threading.Thread | None = None

        root.title("Wallpaper Studio  ·  个人本地使用")
        root.geometry("760x560")
        root.minsize(680, 500)

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="批量下载壁纸（仅个人本地使用，勿分发）",
                  font=("Microsoft YaHei", 12, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))

        self.vars = {
            "source": tk.StringVar(value="all"),
            "pages": tk.StringVar(value="1"),
            "max_items": tk.StringVar(value="50"),
            "workers": tk.StringVar(value="4"),
            "delay": tk.StringVar(value="0.3"),
            "out": tk.StringVar(value=str(Path.cwd() / "wallpapers")),
            "mode": tk.StringVar(value="raw"),
        }
        sources = [s["value"] for s in self.spec["sources"]]

        rows = [
            ("壁纸来源", ttk.Combobox(frm, textvariable=self.vars["source"],
                                     values=sources, state="readonly", width=24)),
            ("抓取页数", ttk.Entry(frm, textvariable=self.vars["pages"], width=12)),
            ("最多张数", ttk.Entry(frm, textvariable=self.vars["max_items"], width=12)),
            ("并发数", ttk.Entry(frm, textvariable=self.vars["workers"], width=12)),
            ("请求间隔(秒)", ttk.Entry(frm, textvariable=self.vars["delay"], width=12)),
            ("图片模式", ttk.Combobox(frm, textvariable=self.vars["mode"],
                                     values=["raw", "display", "thumbnail"],
                                     state="readonly", width=24)),
        ]
        for i, (label, widget) in enumerate(rows, start=1):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", padx=2, pady=4)
            widget.grid(row=i, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(frm, text="保存目录").grid(row=7, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.vars["out"], width=48).grid(
            row=7, column=1, columnspan=2, sticky="we", padx=4)
        ttk.Button(frm, text="浏览…", command=self.pick_dir).grid(
            row=7, column=3, sticky="w")

        btns = ttk.Frame(frm)
        btns.grid(row=8, column=0, columnspan=4, sticky="w", pady=(12, 6))
        self.start_btn = ttk.Button(btns, text="开始下载", command=self.start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn = ttk.Button(btns, text="停止", command=self.cancel,
                                   state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)
        ttk.Button(btns, text="仅导出清单", command=lambda: self.start(True)).pack(
            side=tk.LEFT, padx=(8, 0))

        self.progress = ttk.Progressbar(frm, mode="determinate")
        self.progress.grid(row=9, column=0, columnspan=4, sticky="we", pady=4)
        self.status = ttk.Label(frm, text="就绪")
        self.status.grid(row=10, column=0, columnspan=4, sticky="w")

        self.log = tk.Text(frm, height=16, wrap=tk.WORD, font=("Consolas", 9))
        scroll = ttk.Scrollbar(frm, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=11, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        scroll.grid(row=11, column=3, sticky="ns", pady=(8, 0))
        frm.rowconfigure(11, weight=1)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, foreground="#666",
                  text="提示：'all' 代表不传 source 参数；工具只访问无需鉴权的公开只读接口。").grid(
            row=12, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.root.after(120, self.poll)

    def pick_dir(self) -> None:
        d = filedialog.askdirectory()
        if d:
            self.vars["out"].set(d)

    def emit(self, text: str) -> None:
        self.q.put(("log", text))

    def poll(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log.insert(tk.END, payload + "\n")
                    self.log.see(tk.END)
                elif kind == "progress":
                    done, total = payload
                    self.progress["maximum"] = max(total, 1)
                    self.progress["value"] = done
                    self.status.config(text=f"{done} / {total}")
                elif kind == "done":
                    self.start_btn.config(state=tk.NORMAL)
                    self.stop_btn.config(state=tk.DISABLED)
                    self.status.config(text=payload)
        except queue.Empty:
            pass
        self.root.after(120, self.poll)

    def cancel(self) -> None:
        self.stop.set()
        self.emit("已请求停止，等待在途任务结束…")

    def start(self, manifest_only: bool = False) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.stop.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        try:
            pages = max(1, int(self.vars["pages"].get()))
            max_items = max(1, int(self.vars["max_items"].get()))
            workers = max(1, min(8, int(self.vars["workers"].get())))
            delay = max(0.1, float(self.vars["delay"].get()))
        except ValueError:
            messagebox.showerror("参数错误", "页数/张数/并发/间隔必须是数字")
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
            return

        cfg = {
            "source_name": self.vars["source"].get(),
            "pages": pages, "max_items": max_items, "workers": workers,
            "delay": delay, "out": Path(self.vars["out"].get()),
            "mode": self.vars["mode"].get(), "manifest_only": manifest_only,
        }
        self.worker = threading.Thread(target=self.run, args=(cfg,), daemon=True)
        self.worker.start()

    def run(self, cfg: dict) -> None:
        try:
            source = None
            for s in self.spec["sources"]:
                if s["value"] == cfg["source_name"]:
                    source = s["query_value"]
                    break

            def on_page(page, meta, n):
                self.emit(f"第 {page} 页：{n} 条，共 {meta.get('totalPages')} 页")

            items = []
            for w in ws.iter_gallery(self.spec, source=source,
                                     pages=cfg["pages"],
                                     max_items=cfg["max_items"],
                                     delay=cfg["delay"],
                                     on_page=on_page):
                if self.stop.is_set():
                    break
                items.append(w)
            out_dir = cfg["out"]
            ws.write_manifest(out_dir, items, "json")
            self.emit(f"清单 {len(items)} 条已保存")
            if cfg["manifest_only"]:
                self.q.put(("done", f"仅导出清单完成：{len(items)} 条"))
                return

            limiter = ws.RateLimiter(cfg["delay"])
            webp = cfg["mode"] != "raw"
            self.q.put(("progress", (0, len(items))))
            counts = {"ok": 0, "skipped": 0, "failed": 0}

            def work(w):
                if self.stop.is_set():
                    return w, ("failed", "", "stopped")
                url = w.raw_url if cfg["mode"] == "raw" else ws.convert_url(
                    w.raw_url, 1920, webp)
                return w, ws.download_one(w, out_dir, url=url,
                                          limiter=limiter, webp=webp)

            with ThreadPoolExecutor(max_workers=cfg["workers"]) as pool:
                futures = [pool.submit(work, w) for w in items]
                for i, fut in enumerate(as_completed(futures), start=1):
                    _, (status, name, info) = fut.result()
                    counts[status] = counts.get(status, 0) + 1
                    self.emit(f"[{i}/{len(items)}] {status:<7} {name or '-'} {info}")
                    self.q.put(("progress", (i, len(items))))
            self.q.put(("done", f"完成：{counts.get('ok', 0)} 下载 / "
                                f"{counts.get('skipped', 0)} 跳过 / "
                                f"{counts.get('failed', 0)} 失败"))
        except Exception as exc:  # noqa: BLE001
            self.emit(f"ERROR: {type(exc).__name__}: {exc}")
            self.q.put(("done", "出错，详见日志"))


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
