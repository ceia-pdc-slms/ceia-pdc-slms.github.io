#!/usr/bin/env python3
"""Monta o hub da organização em _site/.

1. Lista os repositórios da org (públicos; privados só com publish = true no hub.toml).
2. Clona cada um e encontra todos os .html.
3. Repo com GitHub Pages próprio: o hub só aponta para o site dele.
   Repo sem Pages: o hub copia o repo para _site/<repo>/ (os links relativos continuam valendo)
   e gera _site/<repo>/index.html se o repo não tiver um.
4. Copia posts/ e assets/ deste repo e gera a página inicial e o catalogo.json.

Só usa a biblioteca padrão (Python 3.13+). Token: GH_TOKEN, GITHUB_TOKEN ou `gh auth token`.
Uso: python build.py [--offline]   (--offline reaproveita os clones em .cache/ sem buscar nada)
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_site"
CACHE = ROOT / ".cache" / "repos"
TEMPLATES = ROOT / "templates"
HUB = "hub"  # nome usado para o conteúdo deste próprio repo (posts/)

MESES = "jan fev mar abr mai jun jul ago set out nov dez".split()


# ---------------------------------------------------------------- dados

@dataclass
class Repo:
    name: str
    title: str
    description: str
    github: str
    mode: str  # "pages" (tem site próprio) | "mirror" (copiado para o hub) | "hub"
    href: str  # para onde o card do repo leva, relativo à raiz do site
    private: bool = False
    updated: str = ""
    count: int = 0


@dataclass
class Page:
    repo: str
    path: str
    title: str
    description: str
    kind: str
    href: str
    source: str
    date: str = ""
    authors: list[str] = field(default_factory=list)
    lang: str = ""


# ---------------------------------------------------------------- utilidades

def log(*a):
    print(*a, file=sys.stderr)


def token() -> str:
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def api(url: str, tok: str):
    req = urllib.request.Request(url if url.startswith("http") else f"https://api.github.com/{url}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "pdc-hub-build")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def git(*args, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def matches(path: str, patterns: list[str]) -> bool:
    p = PurePosixPath(path)
    return any(p.full_match(pat) for pat in patterns)


def data_br(iso: str) -> str:
    if not iso:
        return ""
    d = datetime.fromisoformat(iso[:10])
    return f"{d.day} {MESES[d.month - 1]} {d.year}"


def pretty(name: str) -> str:
    return re.sub(r"[-_]+", " ", name).strip().capitalize()


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


# ---------------------------------------------------------------- leitura de HTML

class Meta(HTMLParser):
    """Lê <title>, <meta>, lang e o primeiro parágrafo com texto suficiente."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.lang = ""
        self.first_p = ""
        self._in = None  # "title" | "p"
        self._buf: list[str] = []
        self._skip = 0  # dentro de <script>/<style>

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang") or ""
        elif tag in ("script", "style", "template"):
            self._skip += 1
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and a.get("content") is not None:
                self.meta.setdefault(key, a["content"].strip())
        elif tag == "title" and not self.title:
            self._in, self._buf = "title", []
        elif tag == "p" and not self.first_p and not self._skip:
            self._in, self._buf = "p", []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "template"):
            self._skip = max(0, self._skip - 1)
        elif tag == self._in:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if tag == "title":
                self.title = text
            elif len(text) >= 60:
                self.first_p = text
            self._in = None

    def handle_data(self, data):
        if self._in and not (self._skip and self._in == "p"):
            self._buf.append(data)


def read_meta(file: Path) -> Meta:
    m = Meta()
    try:
        m.feed(file.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:  # HTML quebrado não pode derrubar o build
        log(f"  aviso: não consegui ler {file}: {e}")
    return m


def shorten(text: str, n: int = 220) -> str:
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------- git

def sync_repo(name: str, cfg: dict, tok: str, offline: bool) -> Path:
    dest = CACHE / name
    if offline:
        if not dest.exists():
            raise SystemExit(f"--offline, mas {dest} não existe")
        return dest
    url = f"https://github.com/{cfg['org']}/{name}.git"
    if tok:
        url = url.replace("https://", f"https://x-access-token:{tok}@")
    if (dest / ".git").exists():
        git("remote", "set-url", "origin", url, cwd=dest)
        git("fetch", "--quiet", "origin", cwd=dest)
        git("reset", "--quiet", "--hard", "origin/HEAD", cwd=dest)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # blob:none traz o histórico (para datas e autores) sem baixar versões antigas dos arquivos
        git("clone", "--quiet", "--filter=blob:none", url, str(dest))
    git("remote", "set-url", "origin", f"https://github.com/{cfg['org']}/{name}.git", cwd=dest)
    return dest


def file_history(repo_dir: Path, path: str) -> tuple[str, list[str]]:
    """(data do último commit, autores do primeiro ao mais recente) — vazio se não for repo git."""
    try:
        out = git("log", "--follow", "--format=%cI\t%an", "--", path, cwd=repo_dir).strip().splitlines()
    except (subprocess.CalledProcessError, OSError):
        return "", []
    if not out:
        return "", []
    authors: list[str] = []
    for line in reversed(out):
        a = line.split("\t", 1)[1]
        if a not in authors and not a.endswith("[bot]"):
            authors.append(a)
    return out[0].split("\t", 1)[0], authors


# ---------------------------------------------------------------- catálogo

def classify(path: str, title: str, desc: str, cfg: dict) -> str:
    for rule in cfg.get("kinds", []):
        if "match" in rule and matches(path, [rule["match"]]):
            return rule["kind"]
        if "text" in rule and re.search(rule["text"], f"{title} {desc}", re.I):
            return rule["kind"]
    return cfg.get("default_kind", "pagina")


def scan(repo: Repo, repo_dir: Path, base_href: str, source_base: str, cfg: dict, prefix: str = "") -> list[Page]:
    """prefix: caminho de repo_dir dentro do repo (ex.: "posts/" para o próprio hub)."""
    pages = []
    overrides = cfg.get("files", {})
    for f in sorted(repo_dir.rglob("*.htm*")):
        local = f.relative_to(repo_dir).as_posix()
        rel = prefix + local
        if rel.startswith(".git/") or matches(rel, cfg.get("exclude", [])):
            continue
        if rel == "index.html" and repo.mode != "hub":
            continue  # a página inicial do repo é o card do próprio repo
        m = read_meta(f)
        o = {**{k[4:]: v for k, v in m.meta.items() if k.startswith("hub:")}, **overrides.get(f"{repo.name}/{rel}", {})}
        if str(o.get("hidden", "")).lower() in ("true", "1", "sim", "yes"):
            continue
        title = o.get("title") or m.title or pretty(f.stem)
        desc = o.get("description") or m.meta.get("description") or m.meta.get("og:description") or m.first_p
        date, authors = file_history(repo_dir, local)
        if o.get("authors"):
            authors = o["authors"] if isinstance(o["authors"], list) else [a.strip() for a in o["authors"].split(",")]
        pages.append(Page(
            repo=repo.name, path=rel, title=title, description=shorten(desc),
            kind=o.get("kind") or classify(rel, title, desc, cfg),
            href=base_href + rel, source=f"{source_base}/{rel}",
            date=str(o.get("date") or date)[:10], authors=authors, lang=m.lang,
        ))
    return pages


def copy_tree(src: Path, dst: Path, cfg: dict):
    ignore_top = {".git", ".github"}

    def ignore(d, names):
        rel_dir = Path(d).relative_to(src).as_posix()
        out = set()
        for n in names:
            rel = n if rel_dir == "." else f"{rel_dir}/{n}"
            if n in ignore_top and rel_dir == "." or matches(rel, cfg.get("exclude", [])):
                out.add(n)
        return out

    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


# ---------------------------------------------------------------- render

ARROW = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>'
EXT = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>'


def page_card(p: Page, labels: dict, repos: dict[str, Repo], prefix: str = "") -> str:
    href = p.href if p.href.startswith("http") else prefix + p.href
    meta = " · ".join(x for x in [data_br(p.date), ", ".join(p.authors[:3])] if x)
    repo_title = repos[p.repo].title if p.repo in repos else p.repo
    haystack = " ".join([p.title, p.description, " ".join(p.authors), repo_title, p.path]).lower()
    return f"""<article class="card item" data-kind="{esc(p.kind)}" data-repo="{esc(p.repo)}" data-busca="{esc(haystack)}" data-revelar>
  <div class="item-top"><span class="item-tipo">{esc(labels.get(p.kind, [p.kind])[0])}</span><span class="chip">{esc(repo_title)}</span></div>
  <h3><a class="item-link" href="{esc(href)}">{esc(p.title)}</a></h3>
  <p>{esc(p.description)}</p>
  <div class="item-meta"><span>{esc(meta)}</span><span class="item-acoes"><a class="fonte" href="{esc(p.source)}" title="Ver o código no GitHub">código</a><span class="ir">Abrir {ARROW}</span></span></div>
</article>"""


def repo_card(r: Repo, prefix: str = "") -> str:
    href = r.href if r.href.startswith("http") else prefix + r.href
    chip = {"pages": '<span class="chip acc">site próprio</span>', "mirror": '<span class="chip">espelhado no hub</span>'}.get(r.mode, "")
    if r.private:
        chip += ' <span class="chip warn">privado</span>'
    plural = "artefato" if r.count == 1 else "artefatos"
    return f"""<article class="card repo" data-revelar>
  <div class="item-top"><span class="item-tipo">{r.count} {plural}</span><span>{chip}</span></div>
  <h3><a class="item-link" href="{esc(href)}">{esc(r.title)}</a></h3>
  <p>{esc(r.description)}</p>
  <div class="item-meta"><span>{esc(('atualizado em ' + data_br(r.updated)) if r.updated else '')}</span><span class="item-acoes"><a class="fonte" href="{esc(r.github)}">GitHub {EXT}</a><span class="ir">Abrir {ARROW}</span></span></div>
</article>"""


def filters(pages: list[Page], labels: dict) -> str:
    present = [k for k in labels if any(p.kind == k for p in pages)]
    present += sorted({p.kind for p in pages} - set(present))
    btns = ['<button data-kind="" aria-pressed="true">Todos</button>']
    btns += [f'<button data-kind="{esc(k)}" aria-pressed="false">{esc(labels.get(k, [k, k])[-1])}</button>' for k in present]
    return "".join(btns)


def render(template: str, **vals) -> str:
    out = (TEMPLATES / template).read_text(encoding="utf-8")
    for k, v in vals.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


# ---------------------------------------------------------------- principal

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="não busca nada, usa os clones em .cache/")
    args = ap.parse_args()

    cfg = tomllib.loads((ROOT / "hub.toml").read_text(encoding="utf-8"))
    repo_cfg = cfg.get("repos", {})
    labels = cfg.get("labels", {})
    tok = "" if args.offline else token()

    if args.offline:
        listing = json.loads((CACHE.parent / "repos.json").read_text())
    else:
        listing = api(f"orgs/{cfg['org']}/repos?per_page=100&type=all", tok)
        (CACHE.parent).mkdir(parents=True, exist_ok=True)
        (CACHE.parent / "repos.json").write_text(json.dumps(listing))

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()

    repos: dict[str, Repo] = {}
    pages: list[Page] = []

    for info in sorted(listing, key=lambda r: r["name"]):
        name = info["name"]
        rc = repo_cfg.get(name, {})
        if name in cfg.get("skip_repos", []) or info.get("archived") or rc.get("skip"):
            continue
        if info["private"] and not rc.get("publish"):
            log(f"– {name}: privado, fora do hub (publish = true no hub.toml para incluir)")
            continue

        mode = "pages" if info.get("has_pages") else "mirror"
        if mode == "pages":
            try:
                site = api(f"repos/{cfg['org']}/{name}/pages", tok)["html_url"]
            except Exception:
                site = f"{cfg['site_url']}/{name}/"
            site = site.rstrip("/") + "/"
            href = site
        else:
            href = f"{name}/"
        repo = Repo(
            name=name, title=rc.get("title") or pretty(name),
            description=rc.get("description") or info.get("description") or "",
            github=info["html_url"], mode=mode, href=href, private=info["private"],
            updated=(info.get("pushed_at") or "")[:10],
        )
        log(f"• {name} ({mode})")
        repo_dir = sync_repo(name, cfg, tok, args.offline)
        branch = info.get("default_branch", "main")
        found = scan(repo, repo_dir, href, f"{info['html_url']}/blob/{branch}", cfg)
        repo.count = len(found)
        if not found and mode == "mirror" and not (repo_dir / "index.html").exists():
            log("  sem páginas HTML, fica de fora")
            continue
        if mode == "mirror":
            copy_tree(repo_dir, OUT / name, cfg)
            if not (OUT / name / "index.html").exists():
                (OUT / name / "index.html").write_text(render(
                    "repo.html", TITLE=esc(repo.title), DESCRIPTION=esc(repo.description),
                    GITHUB=esc(repo.github), COUNT=len(found),
                    ITEMS="\n".join(page_card(p, labels, {name: repo}, prefix="../") for p in sorted(found, key=lambda p: p.date, reverse=True)),
                ), encoding="utf-8")
        repos[name] = repo
        pages += found

    # conteúdo nativo do hub: posts/ (e o que mais vier depois)
    hub_repo = Repo(name=HUB, title="Posts do hub", description="", github="", mode="hub", href="posts/")
    if (ROOT / "posts").exists():
        hub_pages = scan(hub_repo, ROOT / "posts", "", f"https://github.com/{cfg['org']}/{cfg['org']}.github.io/blob/main", cfg, prefix="posts/")
        if hub_pages:
            copy_tree(ROOT / "posts", OUT / "posts", {"exclude": ["**/*.md"]})
            repos[HUB] = hub_repo
            pages += hub_pages

    shutil.copytree(ROOT / "assets", OUT / "assets", dirs_exist_ok=True)
    pages.sort(key=lambda p: (p.date, p.title), reverse=True)
    visible_repos = [r for r in repos.values() if r.mode != "hub"]
    now = datetime.now(timezone.utc)

    (OUT / "index.html").write_text(render(
        "inicio.html",
        TITLE=esc(cfg.get("title", cfg["org"])), EYEBROW=esc(cfg.get("eyebrow", "")), LEDE=esc(cfg.get("lede", "")),
        N_PAGES=len(pages), N_REPOS=len(visible_repos), UPDATED=data_br(now.isoformat()),
        ORG_URL=f"https://github.com/{cfg['org']}",
        REPOS="\n".join(repo_card(r) for r in visible_repos),
        FILTERS=filters(pages, labels),
        REPO_OPTIONS="".join(f'<option value="{esc(r.name)}">{esc(r.title)}</option>' for r in repos.values()),
        ITEMS="\n".join(page_card(p, labels, repos) for p in pages),
    ), encoding="utf-8")

    (OUT / "catalogo.json").write_text(json.dumps({
        "gerado_em": now.isoformat(timespec="seconds"),
        "repos": [asdict(r) for r in visible_repos],
        "paginas": [asdict(p) for p in pages],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / ".nojekyll").touch()
    log(f"\n✓ {len(pages)} páginas de {len(visible_repos)} repositórios → {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
