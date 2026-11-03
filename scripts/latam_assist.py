from __future__ import annotations

import argparse
import concurrent.futures as cf
import difflib
import fnmatch
import json
import os
import random
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(MOD_ROOT, "tools"))
sys.path.insert(0, HERE)

import latam_text as lt
from ldtool.po import read_po, write_po

DEFAULT_PO_DIR = os.path.join(MOD_ROOT, "text", "es-419")
DEFAULT_REVIEW_DIR = os.path.join(MOD_ROOT, "text", "review")
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "translategemma:12b"
DEFAULT_NUM_CTX = 4096

PLACEHOLDER_FORMAT = "brace"
NEWLINE_MODE = False

_TEMPLATE = (
    "You are a professional {s} ({sc}) to {t} ({tc}) translator. Your goal is to accurately "
    "convey the meaning and nuances of the original {s} text while adhering to {t} grammar, "
    "vocabulary, and cultural sensitivities.\nProduce only the {t} translation, without any "
    "additional explanations or commentary.{extra} Please translate the following {s} text "
    "into {t}:\n\n\n{text}")
_GUIDE_PLACEHOLDERS = " Keep every placeholder such as {ph} exactly as written."
_GUIDE = (" Use neutral Latin American Spanish: tú for one person, ustedes for several people, "
          "no voseo and no Spain-only slang; change only what a Latin American reader would "
          "find foreign.")
PROMPTS = ("official", "guided")
DEFAULT_PROMPT = "guided"


def build_prompt(text: str, variant: str, fmt: str = PLACEHOLDER_FORMAT) -> str:
    extra = ""
    if variant != "official":
        if lt.FORMATS[fmt].pattern.search(text):
            extra += _GUIDE_PLACEHOLDERS.format(ph=lt.FORMATS[fmt].render(1))
        extra += _GUIDE
    return _TEMPLATE.format(s="Spanish", sc="es-ES", t="Latin American Spanish", tc="es-419",
                            extra=extra, text=text)


def num_predict_for(text: str) -> int:
    return 48 + 2 * (len(text) // 3 + 1)


@dataclass(frozen=True)
class Item:
    file: str
    ctxt: str
    msgid: str
    english: str

    @property
    def hash(self) -> str:
        return lt.msgid_hash(self.msgid)

    @property
    def key(self) -> str:
        return f"{self.ctxt}|{self.hash}"


@dataclass
class Filters:
    files: list[str] = field(default_factory=list)
    records: list[int] = field(default_factory=list)
    ctxts: list[str] = field(default_factory=list)
    spain_only: bool = False
    limit: int | None = None


def load_items(po_dir: str, f: Filters) -> list[Item]:
    if not os.path.isdir(po_dir):
        raise SystemExit(f"error: po directory not found: {po_dir}")
    names = sorted(n for n in os.listdir(po_dir) if n.endswith(".po"))
    if f.files:
        names = [n for n in names if any(fnmatch.fnmatch(n, pat) for pat in f.files)]
    if f.records:
        wanted = {f"table_{r}.po" for r in f.records}
        names = [n for n in names if n in wanted]
    items: list[Item] = []
    for name in names:
        for e in read_po(os.path.join(po_dir, name)).entries:
            if e.obsolete or e.msgstr or e.fuzzy or not e.msgctxt:
                continue
            if f.ctxts and e.msgctxt not in f.ctxts:
                continue
            if not lt.is_translatable(e.msgid):
                continue
            if f.spain_only and not lt.is_spain_only(e.msgid):
                continue
            en = next((c[3:].strip() for c in e.extracted_comments if c.startswith("en:")), "")
            items.append(Item(name, e.msgctxt, e.msgid, en))
            if f.limit and len(items) >= f.limit:
                return items
    return items


class StaleEntry(RuntimeError):
    pass


def write_msgstr(po_dir: str, item: Item, msgstr: str) -> None:
    path = os.path.join(po_dir, item.file)
    po = read_po(path)
    entry = next((e for e in po.entries if e.msgctxt == item.ctxt and not e.obsolete), None)
    if entry is None:
        raise StaleEntry(f"{item.ctxt} no longer in {item.file}")
    if entry.msgid != item.msgid:
        raise StaleEntry(f"{item.ctxt}: msgid changed since the review")
    if entry.msgstr == msgstr:
        return
    if entry.msgstr:
        raise StaleEntry(f"{item.ctxt}: already has a msgstr in {item.file}")
    entry.msgstr = msgstr
    write_po(path, po)


class JsonlStore:
    def __init__(self, path: str):
        self.path = path
        self.data: dict[str, dict] = {}
        self.lock = threading.Lock()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.read().split("\n")
            for i, line in enumerate(lines):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    if i < len(lines) - 2:
                        print(f"warning: {path}:{i + 1}: unreadable line skipped", file=sys.stderr)
                    continue
                self.data[rec["key"]] = rec

    def get(self, key: str) -> dict | None:
        return self.data.get(key)

    def put(self, rec: dict) -> None:
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self.lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            self.data[rec["key"]] = rec


class OllamaError(RuntimeError):
    pass


class Ollama:
    def __init__(self, host: str, model: str, num_ctx: int = DEFAULT_NUM_CTX,
                 timeout: float = 600.0):
        import requests
        self._requests = requests
        self.host = host.rstrip("/")
        if not re.match(r"^https?://", self.host):
            self.host = "http://" + self.host
        self.model = model
        self.num_ctx = num_ctx
        self.timeout = timeout
        self._local = threading.local()

    def _session(self):
        s = getattr(self._local, "session", None)
        if s is None:
            s = self._local.session = self._requests.Session()
        return s

    def _post(self, path: str, payload: dict, timeout: float | None = None) -> dict:
        try:
            r = self._session().post(self.host + path, json=payload,
                                     timeout=timeout or self.timeout)
        except self._requests.ConnectionError:
            raise OllamaError(f"Ollama is not running at {self.host} "
                              "(start the Ollama app or `ollama serve`)") from None
        except self._requests.Timeout:
            raise OllamaError(f"Ollama request timed out after {timeout or self.timeout:.0f} s") \
                from None
        if r.status_code == 404 and "not found" in r.text:
            raise OllamaError(f"model {self.model!r} not found; run: ollama pull {self.model}")
        if r.status_code != 200:
            raise OllamaError(f"Ollama {path} returned {r.status_code}: {r.text[:300]}")
        return r.json()

    def check(self) -> None:
        try:
            r = self._session().get(self.host + "/api/tags", timeout=10)
        except self._requests.ConnectionError:
            raise OllamaError(f"Ollama is not running at {self.host} "
                              "(start the Ollama app or `ollama serve`)") from None
        names = {m.get("name") for m in r.json().get("models", [])}
        names |= {n[:-len(":latest")] for n in names if n and n.endswith(":latest")}
        if self.model not in names:
            raise OllamaError(f"model {self.model!r} not found on {self.host}; "
                              f"run: ollama pull {self.model}")

    def warm_up(self) -> float:
        t = time.time()
        self._post("/api/generate", {"model": self.model, "prompt": "", "keep_alive": -1,
                                     "options": {"num_ctx": self.num_ctx}})
        return time.time() - t

    def unload(self) -> None:
        self._post("/api/generate", {"model": self.model, "keep_alive": 0})

    def chat(self, prompt: str, *, temperature: float, num_predict: int,
             seed: int | None = None) -> tuple[str, dict]:
        options = {"temperature": temperature, "num_ctx": self.num_ctx,
                   "num_predict": num_predict}
        if seed is not None:
            options["seed"] = seed
        j = self._post("/api/chat", {"model": self.model, "stream": False, "think": False,
                                     "keep_alive": -1, "options": options,
                                     "messages": [{"role": "user", "content": prompt}]})
        return j.get("message", {}).get("content", ""), j


@dataclass
class GenConfig:
    prompt: str = DEFAULT_PROMPT
    temperature: float = 0.2
    retries: int = 1


@dataclass
class Context:
    po_dir: str
    suggestions: JsonlStore
    decisions: JsonlStore
    client: Ollama | None
    gen: GenConfig


def _attempt(ctx: Context, item: Item, temperature: float, seed: int | None,
             grammar: str) -> dict:
    p = lt.protect(item.msgid, PLACEHOLDER_FORMAT, NEWLINE_MODE, grammar)
    raw, info = ctx.client.chat(build_prompt(p.text, ctx.gen.prompt), temperature=temperature,
                                num_predict=num_predict_for(p.text), seed=seed)
    res = lt.restore(p, raw)
    errors = list(res.errors)
    if info.get("done_reason") == "length" and not any("alternatives" in n for n in res.notes):
        errors.append("model output truncated (num_predict)")
    proposal = res.text
    if proposal is not None and not errors:
        errors = lt.tag_problems(item.msgid, proposal)
    return {"raw": raw, "proposal": proposal, "errors": errors, "notes": res.notes,
            "model_text": p.text, "eval_s": round(info.get("total_duration", 0) / 1e9, 2)}


def generate(ctx: Context, item: Item, *, temperature: float | None = None,
             seed: int | None = None) -> dict:
    t0 = time.time()
    temp = ctx.gen.temperature if temperature is None else temperature
    att = _attempt(ctx, item, temp, seed, "expand")
    tries = 1
    while att["errors"] and tries <= ctx.gen.retries:
        retry = _attempt(ctx, item, temp + 0.2 * tries, random.randrange(1 << 30), "placeholder")
        tries += 1
        if not retry["errors"] or retry["proposal"] is not None or att["proposal"] is None:
            att = retry
    proposal = att["proposal"]
    if att["errors"]:
        status = "invalid"
    elif proposal == item.msgid:
        status = "same"
    else:
        status = "ok"
    rec = {"key": item.key, "ctxt": item.ctxt, "file": item.file, "hash": item.hash,
           "model": ctx.client.model, "prompt": ctx.gen.prompt, "status": status,
           "proposal": proposal, "raw": att["raw"], "errors": att["errors"],
           "warnings": lt.review_warnings(item.msgid, proposal) if proposal else [],
           "notes": att["notes"], "tries": tries, "seconds": round(time.time() - t0, 2),
           "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    ctx.suggestions.put(rec)
    return rec


REQUEST_ERRORS = (OllamaError, ValueError, KeyError, OSError)


def is_final(decision: dict | None) -> bool:
    return bool(decision) and decision["action"] in ("accept", "edit", "keep", "same")


def _fmt_eta(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


SUGGEST_WORKERS = 4
REVIEW_WORKERS = 2


def cmd_suggest(a: argparse.Namespace) -> int:
    a.workers = max(1, a.workers or SUGGEST_WORKERS)
    ctx = make_context(a, need_model=True)
    items = load_items(a.po_dir, filters_from(a))
    todo = [it for it in items
            if (a.force or ctx.suggestions.get(it.key) is None)
            and not is_final(ctx.decisions.get(it.key))]
    print(f"suggest: {len(items)} entries match, {len(items) - len(todo)} already cached or "
          f"decided, {len(todo)} to do; model {a.model}, {a.workers} worker(s)", flush=True)
    if not todo:
        return finish(a, ctx)
    print(f"warm-up: model loaded in {ctx.client.warm_up():.1f} s", flush=True)
    counts = {"ok": 0, "same": 0, "invalid": 0, "error": 0}
    lock = threading.Lock()
    t0 = time.time()
    done = 0
    consecutive_errors = 0

    def work(it: Item) -> dict:
        return generate(ctx, it)

    pool = cf.ThreadPoolExecutor(max_workers=a.workers)
    pending: dict[cf.Future, Item] = {}
    queue = iter(todo)
    try:
        def refill() -> None:
            while len(pending) < a.workers * 2:
                it = next(queue, None)
                if it is None:
                    return
                pending[pool.submit(work, it)] = it

        refill()
        while pending:
            finished, _ = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for fut in finished:
                it = pending.pop(fut)
                try:
                    rec = fut.result()
                    status, secs = rec["status"], rec["seconds"]
                    consecutive_errors = 0
                except REQUEST_ERRORS as exc:
                    status, secs = "error", 0.0
                    consecutive_errors += 1
                    print(f"  error {it.ctxt}: {exc}", file=sys.stderr, flush=True)
                with lock:
                    done += 1
                    counts[status] += 1
                    rate = done / max(time.time() - t0, 1e-6)
                    eta = (len(todo) - done) / rate
                    print(f"[{done:>6}/{len(todo)}] {status:<7} {it.ctxt:<14} {secs:5.1f}s | "
                          f"{rate * 60:5.1f}/min ETA {_fmt_eta(eta)} | ok {counts['ok']} "
                          f"same {counts['same']} invalid {counts['invalid']} "
                          f"error {counts['error']}", flush=True)
                if consecutive_errors >= 5:
                    raise OllamaError("5 requests failed in a row; stopping")
            refill()
    except KeyboardInterrupt:
        print("\ninterrupted; finished proposals are cached, re-run to resume", flush=True)
        pool.shutdown(wait=False, cancel_futures=True)
        return 130
    except OllamaError as exc:
        pool.shutdown(wait=False, cancel_futures=True)
        print(f"error: {exc}", file=sys.stderr)
        return 2
    pool.shutdown()
    elapsed = time.time() - t0
    print(f"suggest: {done} entries in {_fmt_eta(elapsed)} ({done / max(elapsed, 1e-6) * 60:.1f}"
          f"/min, {elapsed / max(done, 1):.2f} s/entry): {counts}", flush=True)
    return finish(a, ctx)


def word_diff(a: str, b: str) -> list[tuple[str, str]]:
    tok = re.compile(r"<[^<>]*>|\s+|[^\s<]+")
    ta, tb = tok.findall(a), tok.findall(b)
    out: list[tuple[str, str]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op == "equal":
            out += [(" ", t) for t in ta[i1:i2]]
        else:
            out += [("-", t) for t in ta[i1:i2]]
            out += [("+", t) for t in tb[j1:j2]]
    return out


class Prefetcher:
    def __init__(self, ctx: Context, items: list[Item], ahead: int, workers: int):
        self.ctx, self.items, self.ahead = ctx, items, ahead
        self.pool = cf.ThreadPoolExecutor(max_workers=max(1, workers)) if ctx.client else None
        self.futures: dict[int, cf.Future] = {}

    def ensure(self, index: int) -> None:
        if self.pool is None:
            return
        for i in range(index, min(index + self.ahead + 1, len(self.items))):
            it = self.items[i]
            if i not in self.futures and self.ctx.suggestions.get(it.key) is None \
                    and not is_final(self.ctx.decisions.get(it.key)):
                self.futures[i] = self.pool.submit(generate, self.ctx, it)

    def get(self, index: int) -> dict | None:
        rec = self.ctx.suggestions.get(self.items[index].key)
        if rec is not None:
            return rec
        fut = self.futures.get(index)
        if fut is None:
            return None
        return fut.result()

    def close(self) -> None:
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)


@dataclass
class ReviewIO:
    read_key: Callable[[], str]
    edit: Callable[[str], str | None]
    confirm: Callable[[str], bool]
    wait: Callable[[str, Callable[[], dict]], dict]


class Reviewer:
    KEYS = "[a]ccept  [k]eep original  [e]dit  [r]egenerate  [s]kip  [q]uit"

    def __init__(self, ctx: Context, items: list[Item], io: ReviewIO, console, *,
                 show_all: bool = False, cached_only: bool = False, ahead: int = 4,
                 workers: int = 1):
        self.ctx, self.io, self.console = ctx, io, console
        self.show_all = show_all
        decided = [it for it in items if not is_final(ctx.decisions.get(it.key))]
        if cached_only:
            decided = [it for it in decided if ctx.suggestions.get(it.key)]
        skipped = {it.key for it in decided if (ctx.decisions.get(it.key) or {}).get("action") == "skip"}
        self.items = [it for it in decided if it.key not in skipped] + \
                     [it for it in decided if it.key in skipped]
        self.prefetch = Prefetcher(ctx, self.items, ahead, workers)
        self.counts = {"accept": 0, "edit": 0, "keep": 0, "same": 0, "skip": 0, "forced": 0}

    def decide(self, item: Item, action: str, msgstr: str = "", forced: bool = False) -> None:
        self.ctx.decisions.put({"key": item.key, "ctxt": item.ctxt, "file": item.file,
                                "hash": item.hash, "msgid": item.msgid, "action": action,
                                "msgstr": msgstr, "forced": forced,
                                "time": time.strftime("%Y-%m-%dT%H:%M:%S")})
        if action in ("accept", "edit") and msgstr:
            write_msgstr(self.ctx.po_dir, item, msgstr)
        self.counts[action] += 1
        self.counts["forced"] += forced

    def run(self) -> dict:
        try:
            for i, item in enumerate(self.items):
                self.prefetch.ensure(i)
                rec = self.prefetch.get(i) if self.ctx.suggestions.get(item.key) else \
                    self.io.wait(f"waiting for the model ({item.ctxt})...",
                                 lambda: self.prefetch.get(i) or self._generate(item))
                if rec["status"] == "same" and not self.show_all:
                    self.decide(item, "same")
                    continue
                if self._review_one(i, item, rec) == "quit":
                    break
        finally:
            self.prefetch.close()
        return self.counts

    def _generate(self, item: Item, **kw) -> dict:
        if self.ctx.client is None:
            raise OllamaError("no model available (--cached-only)")
        return generate(self.ctx, item, **kw)

    def _review_one(self, i: int, item: Item, rec: dict) -> str:
        candidate = rec.get("proposal") or item.msgid
        source = "model"
        errors = list(rec.get("errors") or [])
        while True:
            self.render(i, item, rec, candidate, errors, source)
            key = self.io.read_key().lower()
            if key == "q":
                return "quit"
            if key == "s":
                self.decide(item, "skip")
                return "next"
            if key == "k":
                self.decide(item, "keep")
                return "next"
            if key == "a":
                if not rec.get("proposal") and source == "model":
                    self.console.print("No usable proposal: [e]dit, [r]egenerate or "
                                       "[k]eep the original.", style="red", markup=False)
                    continue
                if candidate == item.msgid:
                    self.decide(item, "keep")
                    return "next"
                problems = lt.tag_problems(item.msgid, candidate)
                if problems and not self.io.confirm(
                        "This text FAILS tag validation:\n  " + "\n  ".join(problems) +
                        "\nType 'yes' to save it anyway"):
                    continue
                self.decide(item, "accept" if source == "model" else "edit", candidate,
                            forced=bool(problems))
                return "next"
            if key == "e":
                edited = self.io.edit(candidate)
                if edited is None:
                    continue
                edited = edited.strip()
                if edited == item.msgid:
                    self.decide(item, "keep")
                    return "next"
                problems = lt.tag_problems(item.msgid, edited)
                if problems and not self.io.confirm(
                        "The edit FAILS tag validation:\n  " + "\n  ".join(problems) +
                        "\nType 'yes' to save it anyway (anything else: back to the menu)"):
                    candidate, source, errors = edited, "edit", problems
                    continue
                self.decide(item, "edit", edited, forced=bool(problems))
                return "next"
            if key == "r":
                try:
                    rec = self.io.wait("regenerating...", lambda: self._generate(
                        item, temperature=self.ctx.gen.temperature + 0.4,
                        seed=random.randrange(1 << 30)))
                except OllamaError as exc:
                    self.console.print(str(exc), style="red", markup=False)
                    continue
                candidate, source = rec.get("proposal") or item.msgid, "model"
                errors = list(rec.get("errors") or [])
                continue

    def render(self, i: int, item: Item, rec: dict, candidate: str, errors: list[str],
               source: str) -> None:
        from rich.panel import Panel
        from rich.text import Text

        c = self.console
        c.print()
        c.rule(Text(f"[{i + 1}/{len(self.items)}] {item.file}  {item.ctxt}", style="bold"))
        valid = not (lt.tag_problems(item.msgid, candidate) if candidate != item.msgid else [])
        if source == "model" and not rec.get("proposal"):
            valid = False
        c.print(Panel(tagged(item.english or "(no English)"), title="EN", title_align="left",
                      border_style="dim"))
        c.print(Panel(tagged(item.msgid), title="ES-ES (original)", title_align="left",
                      border_style="blue"))
        label = "proposal" if source == "model" else "your edit"
        c.print(Panel(tagged(candidate), title=f"es-419 {label}", title_align="left",
                      border_style="green" if valid else "red"))
        diff = Text()
        for op, t in word_diff(item.msgid, candidate):
            style = {"-": "red strike", "+": "bold green"}.get(op) or \
                ("dim" if t.startswith("<") else "")
            diff.append(t, style=style)
        c.print(Panel(diff, title="diff", title_align="left", border_style="dim"))
        status = Text()
        problems = errors if not rec.get("proposal") and source == "model" else \
            lt.tag_problems(item.msgid, candidate)
        if candidate == item.msgid and source == "model" and rec.get("proposal"):
            status.append("no change proposed\n", style="cyan")
        elif problems:
            status.append("INVALID: " + "; ".join(problems) + "\n", style="bold red")
        else:
            status.append("tags OK\n", style="green")
        for w in lt.review_warnings(item.msgid, candidate):
            status.append("warning: " + w + "\n", style="yellow")
        if source == "model":
            for n in rec.get("notes") or []:
                status.append("check: " + n + "\n", style="yellow")
        c.print(status, end="")
        c.print(self.KEYS, style="dim", markup=False)


def tagged(text: str):
    from rich.text import Text
    out = Text()
    pos = 0
    for m in lt.TAG_RE.finditer(text):
        out.append(text[pos:m.start()])
        out.append(m.group(0), style="dim cyan" if m.group(0) != "<br>" else "bold magenta")
        pos = m.end()
    out.append(text[pos:])
    return out


def terminal_io(console) -> ReviewIO:
    def read_key() -> str:
        try:
            import msvcrt
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                msvcrt.getwch()
                return ""
            if ch == "\x03":
                raise KeyboardInterrupt
            return ch
        except ImportError:
            return (input("> ").strip() or " ")[0]

    def edit(text: str) -> str | None:
        from prompt_toolkit import prompt
        try:
            return prompt("msgstr> ", default=text)
        except (KeyboardInterrupt, EOFError):
            return None

    def confirm(question: str) -> bool:
        console.print(question, style="bold red", markup=False)
        try:
            return input("> ").strip().lower() == "yes"
        except (KeyboardInterrupt, EOFError):
            return False

    def wait(message: str, fn: Callable[[], dict]) -> dict:
        with console.status(message):
            return fn()

    return ReviewIO(read_key, edit, confirm, wait)


def reconcile(ctx: Context, items_by_key: dict[str, Item] | None = None) -> tuple[int, list[str]]:
    applied, problems = 0, []
    by_file: dict[str, list[dict]] = {}
    for d in ctx.decisions.data.values():
        if d["action"] in ("accept", "edit") and d.get("msgstr"):
            by_file.setdefault(d["file"], []).append(d)
    for name, decs in sorted(by_file.items()):
        path = os.path.join(ctx.po_dir, name)
        if not os.path.exists(path):
            problems.append(f"{name}: file not found")
            continue
        po = read_po(path)
        index = {e.msgctxt: e for e in po.entries if not e.obsolete}
        changed = False
        for d in decs:
            e = index.get(d["ctxt"])
            if e is None or lt.msgid_hash(e.msgid) != d["hash"]:
                problems.append(f"{d['ctxt']}: msgid changed or entry gone; decision not applied")
            elif not e.msgstr:
                e.msgstr = d["msgstr"]
                changed = True
                applied += 1
            elif e.msgstr != d["msgstr"]:
                problems.append(f"{d['ctxt']}: .po already has a different msgstr")
        if changed:
            write_po(path, po)
    return applied, problems


def cmd_review(a: argparse.Namespace) -> int:
    from rich.console import Console
    console = Console(highlight=False)
    ctx = make_context(a, need_model=not a.cached_only)
    applied, problems = reconcile(ctx)
    if applied:
        console.print(f"re-applied {applied} saved decisions to the .po files")
    for p in problems[:20]:
        console.print(f"warning: {p}", style="yellow", markup=False)
    items = load_items(a.po_dir, filters_from(a))
    if ctx.client is not None:
        with console.status("loading the model..."):
            ctx.client.warm_up()
    reviewer = Reviewer(ctx, items, terminal_io(console), console, show_all=a.all,
                        cached_only=a.cached_only, ahead=a.prefetch,
                        workers=a.workers or REVIEW_WORKERS)
    console.print(f"{len(reviewer.items)} entries to review "
                  f"({len(items) - len(reviewer.items)} already decided)")
    try:
        counts = reviewer.run()
    except KeyboardInterrupt:
        counts = reviewer.counts
        console.print("\ninterrupted")
    except (OllamaError, StaleEntry) as exc:
        console.print(f"error: {exc}", style="red", markup=False)
        return 2
    console.print(f"review: {counts}; decisions saved in {ctx.decisions.path}", markup=False)
    return finish(a, ctx)


def cmd_stats(a: argparse.Namespace) -> int:
    ctx = make_context(a, need_model=False)
    items = load_items(a.po_dir, filters_from(a))
    sug = {"ok": 0, "same": 0, "invalid": 0, "none": 0}
    dec: dict[str, int] = {}
    for it in items:
        rec = ctx.suggestions.get(it.key)
        sug[rec["status"] if rec else "none"] += 1
        d = ctx.decisions.get(it.key)
        if d:
            dec[d["action"]] = dec.get(d["action"], 0) + 1
    print(f"{len(items)} untranslated entries match the filters")
    print(f"proposals: {sug}")
    print(f"decisions (on still-untranslated entries): {dec}")
    total = {}
    for d in ctx.decisions.data.values():
        total[d["action"]] = total.get(d["action"], 0) + 1
    print(f"all decisions: {total}")
    return 0


def cmd_unload(a: argparse.Namespace) -> int:
    client = Ollama(a.host, a.model, a.num_ctx)
    client.unload()
    print(f"unloaded {a.model}")
    return 0


def cmd_apply(a: argparse.Namespace) -> int:
    ctx = make_context(a, need_model=False)
    applied, problems = reconcile(ctx)
    for p in problems:
        print(f"warning: {p}")
    print(f"apply: {applied} msgstr written to {a.po_dir}")
    return 1 if problems else 0


def make_context(a: argparse.Namespace, need_model: bool) -> Context:
    client = None
    if need_model:
        client = Ollama(a.host, a.model, a.num_ctx)
        client.check()
    gen = GenConfig(prompt=getattr(a, "prompt", DEFAULT_PROMPT),
                    temperature=getattr(a, "temperature", 0.2),
                    retries=getattr(a, "retries", 1))
    return Context(a.po_dir, JsonlStore(os.path.join(a.review_dir, "suggestions.jsonl")),
                   JsonlStore(os.path.join(a.review_dir, "decisions.jsonl")), client, gen)


def finish(a: argparse.Namespace, ctx: Context) -> int:
    if getattr(a, "unload", False):
        Ollama(a.host, a.model, a.num_ctx).unload()
        print(f"unloaded {a.model}")
    return 0


def filters_from(a: argparse.Namespace) -> Filters:
    return Filters(files=a.files or [], records=a.record or [], ctxts=a.ctxt or [],
                   spain_only=a.spain_only, limit=a.limit)


def make_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default=DEFAULT_HOST, help=f"Ollama URL (default {DEFAULT_HOST})")
    common.add_argument("--model", default=DEFAULT_MODEL, help=f"default {DEFAULT_MODEL}")
    common.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX,
                        help="model context size; keep it constant to avoid reloads")
    common.add_argument("--po-dir", default=DEFAULT_PO_DIR, help="directory with the .po files")
    common.add_argument("--review-dir", default=DEFAULT_REVIEW_DIR,
                        help="where suggestions.jsonl / decisions.jsonl live")

    sel = argparse.ArgumentParser(add_help=False)
    sel.add_argument("--files", action="append", help="file-name glob, e.g. 'table_20*.po' (repeatable)")
    sel.add_argument("--record", action="append", type=int, help="record number (repeatable)")
    sel.add_argument("--ctxt", action="append", help="one msgctxt (repeatable)")
    sel.add_argument("--spain-only", action="store_true",
                     help="only entries whose msgid contains a Spain-only word or form")
    sel.add_argument("--limit", type=int, help="at most N entries")

    gen = argparse.ArgumentParser(add_help=False)
    gen.add_argument("--prompt", choices=PROMPTS, default=DEFAULT_PROMPT)
    gen.add_argument("--temperature", type=float, default=0.2)
    gen.add_argument("--retries", type=int, default=1, help="extra attempts after an invalid proposal")
    gen.add_argument("--workers", type=int,
                     help=f"parallel model requests (default {SUGGEST_WORKERS} for suggest, "
                          f"{REVIEW_WORKERS} for the review prefetch); the server runs them in "
                          "parallel only with OLLAMA_NUM_PARALLEL >= N")
    gen.add_argument("--unload", action="store_true", help="unload the model when done")

    p = argparse.ArgumentParser(prog="latam_assist",
                                description="Model-assisted es-ES -> es-419 review of the .po files.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("suggest", parents=[common, sel, gen], help="batch proposals into the cache")
    s.add_argument("--force", action="store_true", help="regenerate cached proposals too")
    s.set_defaults(func=cmd_suggest)
    r = sub.add_parser("review", parents=[common, sel, gen], help="interactive review")
    r.add_argument("--all", action="store_true", help="also show entries the model left unchanged")
    r.add_argument("--cached-only", action="store_true",
                   help="only entries with a cached proposal; never call the model")
    r.add_argument("--prefetch", type=int, default=4, help="proposals generated ahead (default 4)")
    r.set_defaults(func=cmd_review)
    st = sub.add_parser("stats", parents=[common, sel], help="progress of proposals and decisions")
    st.set_defaults(func=cmd_stats)
    ap = sub.add_parser("apply", parents=[common],
                        help="write accepted decisions into the .po files (e.g. after re-extract)")
    ap.set_defaults(func=cmd_apply)
    u = sub.add_parser("unload", parents=[common], help="unload the model from the GPU")
    u.set_defaults(func=cmd_unload)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    a = make_parser().parse_args(argv)
    try:
        return a.func(a)
    except OllamaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
