#!/usr/bin/env python3
"""vault-keeper：Obsidian / Markdown 知识库的管家。

脚本只做确定性的事：收编现有库、推断目录角色、校验 frontmatter 和双链、生成目录、
git 提交拉取推送、会话启动健康提示。判断（写什么、放哪、怎么链）留给 agent 和人。

永远不删文件，永远不 force push，永远不读 secrets 目录。
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

VERSION = "0.1.0"
CONFIG_NAME = ".vault-keeper.json"
ROLES = ("raw", "wiki", "private", "templates", "archive", "attachments", "excluded")

ROLE_HINTS = {
    "raw": {"raw", "sources", "source", "clippings", "clipping", "inbox", "captures", "capture", "literature",
            "references", "reference", "readwise", "imports", "import", "原始", "素材", "收集", "剪藏", "来源"},
    "templates": {"templates", "template", "_templates", "模板"},
    "archive": {"archive", "archives", "archived", "归档", "old"},
    "attachments": {"attachments", "attachment", "assets", "files", "images", "img", "media", "附件", "图片"},
    "private": {"life", "personal", "private", "journal", "journals", "diary", "daily", "dailies", "日记", "个人", "私密"},
}
SECRET_DIR_RE = re.compile(r"^\.?(secrets?|credentials?|keys?|vault-secrets?|\.env.*|\.ssh|\.age|密钥|密码)$", re.I)
SECRET_FILE_RE = re.compile(r"(^\.env($|\.)|\.pem$|\.key$|keys\.txt$|id_rsa|id_ed25519)", re.I)
ALWAYS_EXCLUDED = {".obsidian", ".git", ".trash", ".vault-keeper", "node_modules", "__pycache__", ".DS_Store", ".smart-env", ".space"}
ROOT_ALLOW = {"INDEX", "SCHEMA", "CLAUDE", "AGENTS", "README", "CATALOG", "HOME", "Home", "index", "readme", "MOC"}
MANAGED_BEGIN = "<!-- vault-keeper:begin"
MANAGED_END = "<!-- vault-keeper:end -->"
DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
TEMPLATE_RESIDUE_RE = re.compile(r"\{\{\s*(date|title|time|DATE|TITLE)\s*(:[^}]*)?\}\}")
WIKILINK_RE = re.compile(r"(!?)\[\[([^\]\[|#^]+)(?:[#^][^\]\[|]*)?(?:\|[^\]\[]*)?\]\]")
MDLINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+\.md)(?:#[^)]*)?\)")
ONE_LINER_KEYS = ("description", "summary", "note", "一句话", "摘要", "desc")


# --------------------------------------------------------------------------- 基础

def home() -> Path:
    return Path(os.environ.get("VAULT_KEEPER_HOME") or Path.home())


def expand(p: str) -> Path:
    if p == "~":
        return home()
    if p.startswith("~/"):
        return home() / p[2:]
    return Path(os.path.expandvars(p))


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return dt.date.today().isoformat()


def warn(msg: str) -> None:
    print(f"[vk] {msg}", file=sys.stderr)


def global_cfg_path() -> Path:
    return home() / ".agents" / "vault-keeper.json"


def load_global() -> dict:
    p = global_cfg_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            warn(f"{p} 解析失败，忽略：{e}")
    return {}


def save_global(d: dict) -> None:
    p = global_cfg_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def registered_vaults() -> List[Path]:
    out = []
    for v in load_global().get("vaults", []):
        p = expand(v)
        if p.exists():
            out.append(p)
    return out


def state_dir(vault: Path) -> Path:
    slug = hashlib.sha1(str(vault.resolve()).encode()).hexdigest()[:10]
    d = home() / ".agents" / ".vault-keeper" / f"{vault.name}-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def journal(vault: Path, event: str, **fields) -> None:
    rec = {"ts": now_iso(), "event": event}
    rec.update(fields)
    with (state_dir(vault) / "journal.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def find_vault(arg: Optional[str]) -> Path:
    if arg:
        p = expand(arg).resolve()
        if not p.is_dir():
            sys.exit(f"[vk] 不是目录：{p}")
        return p
    cur = Path.cwd().resolve()
    for c in [cur, *cur.parents]:
        if (c / CONFIG_NAME).exists() or (c / ".obsidian").is_dir():
            return c
    regs = registered_vaults()
    if len(regs) == 1:
        return regs[0]
    if regs:
        sys.exit("[vk] 注册了多个库，请用 --vault 指定：\n  " + "\n  ".join(str(r) for r in regs))
    sys.exit("[vk] 找不到知识库：请在库目录里运行，或加 --vault <目录>，或先 `vk init --vault <目录> --apply`。")


# --------------------------------------------------------------------------- 最小 YAML（frontmatter 子集）

KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_.\-一-鿿]+):(\s*(.*))?$")


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        inner = v[1:-1]
        return inner.replace("''", "'") if v[0] == "'" else inner.replace('\\"', '"')
    return v


def _scalar(v: str):
    s = _unquote(v)
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_unquote(x) for x in re.split(r",\s*", inner)] if inner else []
    return s


def _min_indent(lines: List[str]) -> int:
    inds = [len(l) - len(l.lstrip(" ")) for l in lines if l.strip()]
    return min(inds) if inds else 0


def parse_yaml_block(lines: List[str], base_indent: int = 0) -> dict:
    out: dict = {}
    i, n = 0, len(lines)

    def ind(s: str) -> int:
        return len(s) - len(s.lstrip(" "))

    while i < n:
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ind(line) < base_indent:
            break
        m = KEY_RE.match(line)
        if not m or ind(line) != base_indent:
            i += 1
            continue
        key = m.group(2)
        val = (m.group(4) or "").strip()
        i += 1
        children: List[str] = []
        while i < n and (not lines[i].strip() or ind(lines[i]) > base_indent):
            children.append(lines[i])
            i += 1
        while children and not children[-1].strip():
            children.pop()
        if val in (">", ">-", ">+", "|", "|-", "|+"):
            body = [c[min(ind(c), _min_indent(children)):] if c.strip() else "" for c in children]
            out[key] = ("\n".join(body)).strip() if val.startswith("|") else " ".join(b.strip() for b in body if b.strip())
        elif val == "" and children:
            if children[0].lstrip().startswith("- "):
                out[key] = [_scalar(c.lstrip()[2:]) for c in children if c.lstrip().startswith("- ")]
            else:
                out[key] = parse_yaml_block(children, _min_indent(children))
        elif val and val[0] in "'\"" and not (len(val) >= 2 and val[-1] == val[0]):
            q = val[0]
            buf = [val[1:]]
            for c in children:
                t = c.strip()
                if t.endswith(q):
                    buf.append(t[:-1])
                    break
                buf.append(t)
            out[key] = " ".join(buf).strip()
        elif val:
            if children:
                val = " ".join([val] + [c.strip() for c in children if c.strip()])
            out[key] = _scalar(val)
        else:
            out[key] = ""
    return out


def split_frontmatter(text: str) -> Tuple[List[str], Optional[int], Optional[int]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return lines, None, None
    for j in range(1, min(len(lines), 200)):
        if lines[j].strip() in ("---", "..."):
            return lines, 0, j
    return lines, None, None


# --------------------------------------------------------------------------- 配置与库模型

def load_vault_cfg(vault: Path) -> dict:
    p = vault / CONFIG_NAME
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            warn(f"{p} 解析失败，忽略：{e}")
    return {}


def save_vault_cfg(vault: Path, cfg: dict) -> None:
    (vault / CONFIG_NAME).write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def role_of_dir(name: str, cfg: dict) -> Optional[str]:
    """根据配置或名字判断顶层目录的角色；None 表示未知。"""
    for role in ROLES:
        if name in cfg.get("roles", {}).get(role, []):
            return role
    if name in ALWAYS_EXCLUDED or SECRET_DIR_RE.match(name):
        return "excluded"
    low = name.lower()
    for role, hints in ROLE_HINTS.items():
        if low in hints:
            return role
    return None


def infer_roles(vault: Path, cfg: dict) -> Tuple[Dict[str, List[str]], Dict[str, dict]]:
    """扫顶层目录，返回 roles 映射与每个目录的统计。"""
    roles: Dict[str, List[str]] = {r: [] for r in ROLES}
    stats: Dict[str, dict] = {}
    for d in sorted(vault.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        role = role_of_dir(name, cfg)
        md = other = 0
        if role != "excluded":
            for root, dns, fns in os.walk(d):
                dns[:] = [x for x in dns if x not in ALWAYS_EXCLUDED and not SECRET_DIR_RE.match(x)]
                for f in fns:
                    if f.startswith("."):
                        continue
                    if f.endswith(".md"):
                        md += 1
                    else:
                        other += 1
        if role is None:
            if md == 0 and other == 0:
                continue
            role = "attachments" if md == 0 or (other > 0 and md / (md + other) < 0.3) else "wiki"
        roles[role].append(name)
        stats[name] = {"role": role, "md": md, "other": other}
    return roles, stats


def required_fields_for(notes: List["Note"], threshold: float = 0.9, min_n: int = 5) -> Dict[str, List[str]]:
    """每个 wiki/raw 顶层目录里，≥threshold 的笔记都有的字段 → 必填。"""
    by_dir: Dict[str, List[Note]] = {}
    for n in notes:
        if n.role in ("wiki", "raw") and n.top:
            by_dir.setdefault(n.top, []).append(n)
    req: Dict[str, List[str]] = {}
    for d, ns in by_dir.items():
        if len(ns) < min_n:
            continue
        cnt: Dict[str, int] = {}
        for n in ns:
            for k in n.fm.keys():
                cnt[k] = cnt.get(k, 0) + 1
        fields = sorted(k for k, c in cnt.items() if c / len(ns) >= threshold and k not in ("originSessionId",))
        if fields:
            req[d] = fields
    return req


class Note:
    __slots__ = ("path", "rel", "top", "role", "fm", "text", "title", "one", "links", "mdlinks", "mtime", "has_fm", "residue")

    def __init__(self, vault: Path, path: Path, role: str):
        self.path = path
        self.rel = path.relative_to(vault).as_posix()
        parts = self.rel.split("/")
        self.top = parts[0] if len(parts) > 1 else ""
        self.role = role
        try:
            self.text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            self.text = ""
        lines, s, e = split_frontmatter(self.text)
        self.has_fm = s is not None
        self.fm = parse_yaml_block(lines[s + 1:e]) if s is not None else {}
        body_lines = lines[e + 1:] if s is not None else lines
        self.title = self._title(body_lines)
        self.one = self._one_liner(body_lines)
        prose = _strip_code("\n".join(body_lines))
        self.links = [(m.group(1) == "!", m.group(2).strip().rstrip("\\").strip())
                      for m in WIKILINK_RE.finditer(prose) if m.group(2).strip().rstrip("\\").strip()]
        self.mdlinks = [urllib.parse.unquote(m.group(1)) for m in MDLINK_RE.finditer(prose)
                        if "://" not in m.group(1) and not m.group(1).startswith("mailto:")]
        self.mtime = path.stat().st_mtime
        self.residue = role in ("wiki", "raw") and bool(TEMPLATE_RESIDUE_RE.search(prose))

    @property
    def stem(self) -> str:
        return self.path.stem

    def _title(self, body: List[str]) -> str:
        t = self.fm.get("title")
        if isinstance(t, str) and t.strip():
            return t.strip()
        for l in body[:40]:
            if l.startswith("# "):
                return l[2:].strip()
        return self.stem

    def _one_liner(self, body: List[str], limit: int = 72) -> str:
        for k in ONE_LINER_KEYS:
            v = self.fm.get(k)
            if isinstance(v, str) and v.strip():
                return _clip(_strip_md(v), limit)
        in_code = False
        for l in body[:80]:
            s = l.strip()
            if s.startswith("```"):
                in_code = not in_code
                continue
            if in_code or not s or s.startswith(("#", "|", "---", "<!--", "![[", "![", "> [!")):
                continue
            if s.startswith(">"):
                s = s.lstrip("> ").strip()
            if len(_strip_md(s)) < 8:
                continue
            return _clip(_strip_md(s), limit)
        return ""

    def status(self) -> str:
        v = self.fm.get("status")
        return str(v) if isinstance(v, (str, int)) and str(v).strip() else ""

    def date(self) -> str:
        v = self.fm.get("date")
        if isinstance(v, str) and v.strip():
            return v.strip()[:10]
        m = DATE_PREFIX_RE.match(self.stem)
        return m.group(0) if m else ""

    def tags(self) -> List[str]:
        v = self.fm.get("tags")
        if isinstance(v, list):
            return [str(x) for x in v]
        if isinstance(v, str) and v.strip():
            return [x.strip() for x in re.split(r"[,\s]+", v) if x.strip()]
        return []


FENCE_RE = re.compile(r"^(```|~~~).*?^\1\s*$", re.S | re.M)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
COMMON_STEMS = {"readme", "index", "home", "moc", "untitled", "notes", "todo"}


def _strip_code(s: str) -> str:
    """去掉围栏代码块和行内代码：里面的 [[ ]] 是 bash 语法、{{ }} 是示例，不是链接和残留。"""
    s = FENCE_RE.sub("", s)
    return INLINE_CODE_RE.sub("", s)


def _strip_md(s: str) -> str:
    s = re.sub(r"!?\[\[([^\]|]*)(\|([^\]]*))?\]\]", lambda m: m.group(3) or m.group(1), s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"[*_`~]+", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def iter_vault(vault: Path, cfg: dict) -> Iterable[Tuple[Path, str]]:
    """产出 (md 路径, 角色)。excluded 目录完全不进入。"""
    roles_cfg = cfg.get("roles") or {}
    for d in sorted(vault.iterdir()):
        if d.name.startswith(".") and d.name not in roles_cfg.get("wiki", []):
            continue
        if d.is_file():
            if d.suffix == ".md" and not d.is_symlink():
                yield d, "root"
            continue
        role = role_of_dir(d.name, cfg) or "wiki"
        if role in ("excluded", "attachments"):
            continue
        for root, dns, fns in os.walk(d):
            dns[:] = sorted(x for x in dns if x not in ALWAYS_EXCLUDED and not SECRET_DIR_RE.match(x) and not x.startswith("."))
            for f in sorted(fns):
                if f.endswith(".md") and not SECRET_FILE_RE.search(f):
                    yield Path(root) / f, role


def load_notes(vault: Path, cfg: dict) -> List[Note]:
    return [Note(vault, p, r) for p, r in iter_vault(vault, cfg)]


# --------------------------------------------------------------------------- 链接解析（Obsidian 语义）

class Resolver:
    """Obsidian 风格解析：[[name]] 按文件名（大小写不敏感），[[a/b/name]] 按库内路径。附件也算目标。"""

    def __init__(self, vault: Path, cfg: dict):
        self.by_stem: Dict[str, List[str]] = {}
        self.by_path: Dict[str, str] = {}
        for root, dns, fns in os.walk(vault):
            dns[:] = [x for x in dns if x not in ALWAYS_EXCLUDED and not SECRET_DIR_RE.match(x)]
            for f in fns:
                if f.startswith(".") or SECRET_FILE_RE.search(f):
                    continue
                rel = Path(root, f).relative_to(vault).as_posix()
                key_path = rel[:-3] if rel.endswith(".md") else rel
                self.by_path[key_path.lower()] = rel
                stem = Path(f).stem if f.endswith(".md") else f
                self.by_stem.setdefault(stem.lower(), []).append(rel)

    def resolve(self, target: str, from_rel: str) -> Tuple[Optional[str], int]:
        """返回 (命中路径, 候选数)。候选数 ≥2 表示同名歧义。"""
        t = target.strip()
        if t.startswith(("/", "~")):
            # 库外绝对路径：存在就算解析成功（外部文件，不计入链），不存在才是断链
            p = Path(os.path.expanduser(t))
            return (f"<external>{t}", 1) if p.exists() else (None, 0)
        t = t.strip("/")
        if not t:
            return from_rel, 1
        if t.endswith(".md"):
            t = t[:-3]
        low = t.lower()
        if "/" in low:
            hit = self.by_path.get(low)
            if hit:
                return hit, 1
            # 相对路径（相对于当前文件所在目录）
            base = Path(from_rel).parent
            cand = (base / t).as_posix().lower()
            cand = os.path.normpath(cand).replace("\\", "/")
            hit = self.by_path.get(cand)
            if hit:
                return hit, 1
            low = low.rsplit("/", 1)[-1]
        hits = self.by_stem.get(low) or []
        if not hits and "." not in low:
            hits = self.by_stem.get(low + ".md") or []
        if hits:
            return (sorted(hits, key=len)[0]), len(hits)
        return None, 0

    def suggest(self, target: str, n: int = 3) -> List[str]:
        stem = target.rsplit("/", 1)[-1].lower()
        if stem.endswith(".md"):
            stem = stem[:-3]
        cands = difflib.get_close_matches(stem, list(self.by_stem.keys()), n=n, cutoff=0.72)
        return [self.by_stem[c][0] for c in cands]


# --------------------------------------------------------------------------- git

def git(vault: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True, check=check)


def is_git(vault: Path) -> bool:
    return (vault / ".git").exists() and git(vault, "rev-parse", "--is-inside-work-tree").returncode == 0


def git_state(vault: Path) -> dict:
    st = {"git": False}
    if not is_git(vault):
        return st
    st["git"] = True
    porcelain = [l for l in git(vault, "status", "--porcelain", "-z", "--untracked-files=all").stdout.split("\0") if l]
    # -z 模式下重命名条目会多带一个旧路径字段；这里只取状态行
    entries = [l for l in porcelain if len(l) > 3 and l[2] == " "]
    st["dirty"] = len(entries)
    st["dirty_files"] = [l[3:] for l in entries]
    st["branch"] = git(vault, "branch", "--show-current").stdout.strip()
    st["remote"] = bool(git(vault, "remote").stdout.strip())
    last = git(vault, "log", "-1", "--format=%ct").stdout.strip()
    st["last_commit_days"] = int((dt.datetime.now().timestamp() - int(last)) / 86400) if last else None
    st["ahead"] = st["behind"] = None
    if st["remote"]:
        r = git(vault, "rev-list", "--left-right", "--count", "@{u}...HEAD")
        if r.returncode == 0:
            b, a = r.stdout.split()
            st["behind"], st["ahead"] = int(b), int(a)
    return st


def raw_modified_files(vault: Path, raw_dirs: List[str]) -> List[str]:
    if not raw_dirs or not is_git(vault):
        return []
    r = git(vault, "log", "--diff-filter=M", "--name-only", "--format=", "--", *raw_dirs)
    return sorted({l for l in r.stdout.splitlines() if l.strip()})


# --------------------------------------------------------------------------- lint

LINT_LEVEL = {
    "F1": "warn", "F2": "warn", "F3": "warn",
    "L1": "warn", "L2": "info", "L3": "info",
    "R1": "warn", "S1": "warn", "D1": "info", "P1": "info",
    "X1": "warn", "G1": "warn", "G2": "warn", "G3": "info", "G4": "warn",
    "C1": "info", "H1": "warn",
}

EXPLAIN = {
    "F1": "wiki 笔记没有 frontmatter。agent 和 Obsidian Bases 都靠它分类检索。用 templates/ 里的模板补上。",
    "F2": "缺少该目录 ≥90% 笔记都有的字段（init 时推断，见 .vault-keeper.json 的 required）。补字段，或在配置里删掉这个要求。",
    "F3": "正文里还留着模板占位符 {{date}} {{title}}，说明是复制模板后没填。",
    "L1": "双链指向的文件不存在。可能是改名、移动、错别字。脚本给出最接近的候选，由 agent 判断是改链接还是补笔记。",
    "L2": "[[同名]] 链接有多个候选文件，Obsidian 会静默选一个。改成带路径的 [[目录/文件名]]。",
    "L3": "孤儿：没有任何笔记链到它。不一定是错，但沉淀的目的就是被找到；考虑在相关笔记里加链接。",
    "R1": "raw/ 层的文件在 git 历史里被修改过。原始素材只追加不编辑，分析写到 wiki 层。",
    "S1": "笔记声明 supersedes 某文件，但那个文件的 status 不是 superseded。推翻旧结论要两边都标。",
    "D1": "不同目录有同名文件，是 L2 歧义的根源。改名或用带路径的链接。",
    "P1": "库根目录有不在白名单里的笔记（白名单：INDEX/SCHEMA/CLAUDE/AGENTS/README/CATALOG）。移进对应角色的目录。",
    "X1": "库里有看起来像密钥的目录或文件但没进 .gitignore。任何同步方案都会把它带出去。",
    "G1": "有未提交的改动。agent 写了没人提交，就等于没同步。vk sync --apply。",
    "G2": "有本地 commit 没推到远端。另一台机器看不到。vk sync --apply（默认会推）。",
    "G3": "没有 git 远端。只有一台机器时可以忽略；要同步就 gh repo create <name> --private --source . --push。",
    "G4": "远端有本地没有的 commit。先 vk sync --apply（会 pull --rebase）。",
    "C1": "CATALOG.md 不存在或比最新笔记旧。agent 靠它找可链接的笔记。vk index。",
    "H1": "库根目录没有 CLAUDE.md / AGENTS.md，agent 进来不知道规则。vk init --apply 会生成。",
}


def lint(vault: Path, cfg: dict, notes: List[Note]) -> dict:
    roles = cfg.get("roles") or {}
    required = cfg.get("required") or {}
    catalog_name = cfg.get("catalog", "CATALOG.md")
    rules_file = cfg.get("rules_file")
    issues: List[dict] = []

    def add(code: str, path: str, msg: str, **extra):
        issues.append({"code": code, "level": LINT_LEVEL[code], "path": path, "msg": msg, **extra})

    res = Resolver(vault, cfg)
    inbound: Dict[str, int] = {}
    stems: Dict[str, List[str]] = {}
    for n in notes:
        stems.setdefault(n.stem.lower(), []).append(n.rel)
        if n.role == "wiki" and not n.has_fm:
            add("F1", n.rel, "没有 frontmatter")
        if n.role in ("wiki", "raw") and n.top in required and n.has_fm:
            missing = [k for k in required[n.top] if k not in n.fm]
            if missing:
                add("F2", n.rel, f"缺字段 {', '.join(missing)}", fields=missing)
        if n.rel == catalog_name or n.role == "templates":
            continue  # 目录是生成的；模板里的 [[wikilink]] 是占位，不查链接
        if n.residue:
            add("F3", n.rel, "模板占位符未填")
        seen_targets: set = set()
        for is_embed, target in n.links:
            if target in seen_targets:
                continue
            seen_targets.add(target)
            hit, cnt = res.resolve(target, n.rel)
            if hit is None:
                add("L1", n.rel, f"断链 [[{target}]]", target=target, suggest=res.suggest(target))
            else:
                if hit.endswith(".md") and not hit.startswith("<external>"):
                    inbound[hit] = inbound.get(hit, 0) + 1
                if cnt >= 2 and "/" not in target:
                    add("L2", n.rel, f"[[{target}]] 有 {cnt} 个同名候选", target=target)
        for target in n.mdlinks:
            if target in seen_targets:
                continue
            seen_targets.add(target)
            hit, _ = res.resolve(target, n.rel)
            if hit is None:
                add("L1", n.rel, f"断链 ({target})", target=target, suggest=res.suggest(target))
            elif not hit.startswith("<external>"):
                inbound[hit] = inbound.get(hit, 0) + 1
        sup = n.fm.get("supersedes")
        for s in (sup if isinstance(sup, list) else [sup] if sup else []):
            hit, _ = res.resolve(str(s), n.rel)
            if hit:
                other = next((m for m in notes if m.rel == hit), None)
                if other is not None and str(other.fm.get("status", "")).lower() not in ("superseded", "archived", "deprecated"):
                    add("S1", n.rel, f"supersedes [[{s}]]，但对方 status={other.status() or '空'}", target=hit)
    for n in notes:
        if n.role == "wiki" and n.rel != catalog_name and inbound.get(n.rel, 0) == 0:
            add("L3", n.rel, "孤儿笔记（无入链）")
        if n.role == "root" and n.stem not in ROOT_ALLOW and n.rel != catalog_name and n.rel != rules_file and n.stem not in cfg.get("root_allow", []):
            add("P1", n.rel, "根目录游离笔记")
    for stem, rels in stems.items():
        if len(rels) >= 2 and stem not in COMMON_STEMS:
            add("D1", rels[0], f"同名文件 ×{len(rels)}", others=rels)
    # 密钥目录：被 git 跟踪的最危险（.gitignore 对已跟踪文件无效）
    gi = (vault / ".gitignore").read_text(encoding="utf-8", errors="ignore") if (vault / ".gitignore").exists() else ""
    ok_dirs = set(cfg.get("secret_dirs_ok") or [])
    for d in vault.iterdir():
        if SECRET_DIR_RE.match(d.name) and d.name != ".git" and d.name not in ok_dirs:
            tracked = is_git(vault) and bool(git(vault, "ls-files", "--", d.name).stdout.strip())
            ignored = any(l.strip().rstrip("/") == d.name for l in gi.splitlines())
            if tracked:
                add("X1", d.name + "/", "疑似密钥目录已被 git 跟踪，会随同步外泄。确认无明文可在配置 secret_dirs_ok 里放行")
            elif not ignored:
                add("X1", d.name + "/", "疑似密钥目录未加入 .gitignore")
    for raw_f in raw_modified_files(vault, roles.get("raw", [])):
        if (vault / raw_f).exists():
            add("R1", raw_f, "raw 层文件被修改过")
    # git
    gs = git_state(vault)
    if gs["git"]:
        if gs["dirty"]:
            add("G1", "", f"{gs['dirty']} 个文件未提交", count=gs["dirty"])
        if gs.get("ahead"):
            add("G2", "", f"{gs['ahead']} 个 commit 未推送", count=gs["ahead"])
        if gs.get("behind"):
            add("G4", "", f"远端领先 {gs['behind']} 个 commit", count=gs["behind"])
        if not gs["remote"]:
            add("G3", "", "没有远端")
    # catalog / rules
    cat = vault / catalog_name
    newest = max((n.mtime for n in notes if n.rel != catalog_name), default=0)
    if not cat.exists():
        add("C1", catalog_name, "目录未生成")
    elif cat.stat().st_mtime < newest - 1:
        add("C1", catalog_name, "目录过期")
    if not (vault / "CLAUDE.md").exists() and not (vault / "AGENTS.md").exists():
        add("H1", "", "缺 CLAUDE.md / AGENTS.md")
    counts: Dict[str, int] = {}
    for i in issues:
        counts[i["code"]] = counts.get(i["code"], 0) + 1
    return {"ts": now_iso(), "vault": str(vault), "notes": len(notes), "counts": counts, "issues": issues, "git": gs}


def render_lint(r: dict, full: bool = False, max_per_code: int = 8) -> str:
    out = [f"# vault-keeper lint · {Path(r['vault']).name} · {r['notes']} 篇 · {r['ts'][:16]}"]
    counts = r["counts"]
    if not counts:
        out.append("一切正常。")
        return "\n".join(out)
    warns = {c: n for c, n in counts.items() if LINT_LEVEL[c] == "warn"}
    infos = {c: n for c, n in counts.items() if LINT_LEVEL[c] == "info"}
    out.append("要处理：" + ("，".join(f"{c}×{n}" for c, n in sorted(warns.items())) or "无"))
    out.append("仅提示：" + ("，".join(f"{c}×{n}" for c, n in sorted(infos.items())) or "无"))
    by_code: Dict[str, List[dict]] = {}
    for i in r["issues"]:
        by_code.setdefault(i["code"], []).append(i)
    for code in sorted(by_code, key=lambda c: (LINT_LEVEL[c] != "warn", c)):
        items = by_code[code]
        out.append(f"\n## {code} ×{len(items)}  {EXPLAIN[code].split('。')[0]}")
        shown = items if full else items[:max_per_code]
        for i in shown:
            line = f"- {i['path']}  {i['msg']}" if i["path"] else f"- {i['msg']}"
            if i.get("suggest"):
                line += "  → 候选: " + " | ".join(i["suggest"])
            if i.get("others"):
                line += "  ↔ " + " | ".join(i["others"][1:])
            out.append(line)
        if len(items) > len(shown):
            out.append(f"- …还有 {len(items) - len(shown)} 条（--full 看全部）")
    return "\n".join(out)


# --------------------------------------------------------------------------- catalog

def build_catalog(vault: Path, cfg: dict, notes: List[Note]) -> str:
    catalog_name = cfg.get("catalog", "CATALOG.md")
    rows = [n for n in notes if n.role in ("wiki", "raw", "root") and n.rel != catalog_name]
    groups: Dict[str, List[Note]] = {}
    for n in rows:
        groups.setdefault(n.top or "（根目录）", []).append(n)
    total = len(rows)
    out = [
        "---",
        "type: catalog",
        f"generated: {now_iso()}",
        f"count: {total}",
        'note: "由 vault-keeper 生成，手改会被覆盖。写新笔记前先在这里找同主题的笔记：能更新就不新建，新建就链接相关笔记。"',
        "---",
        "",
        f"# 目录 / Catalog · {total} 篇 · {today()}",
        "",
        "private 目录不收录；状态 superseded 的标 ~~删除线~~。格式：`[[路径|标题]] · 日期 · 一句话`。",
    ]
    order = sorted(groups, key=lambda g: (g == "（根目录）", -len(groups[g])))
    for g in order:
        ns = groups[g]
        out.append(f"\n## {g} · {len(ns)}")
        sub: Dict[str, List[Note]] = {}
        for n in ns:
            parts = n.rel.split("/")
            sub.setdefault(parts[1] if len(parts) > 2 else "", []).append(n)
        for s in sorted(sub, key=lambda k: (k != "", k)):
            if s:
                out.append(f"\n### {g}/{s} · {len(sub[s])}")
            for n in sorted(sub[s], key=lambda x: (x.date() or "0000", x.title), reverse=True):
                label = n.title.replace("|", "｜").replace("]]", "")
                link = f"[[{n.rel[:-3]}|{label}]]"
                if n.status().lower() in ("superseded", "archived", "deprecated"):
                    link = f"~~{link}~~"
                bits = [link]
                if n.date():
                    bits.append(n.date())
                if n.status() and n.status().lower() not in ("active",):
                    bits.append(n.status())
                if n.one and n.one.lower() != n.title.lower():
                    bits.append(n.one)
                out.append("- " + " · ".join(bits))
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- 管理块（CLAUDE.md / AGENTS.md）

def managed_block(vault: Path, cfg: dict) -> str:
    roles = cfg.get("roles") or {}
    required = cfg.get("required") or {}
    rules = cfg.get("rules_file")
    me = Path(__file__).resolve()
    vk = f"python3 {me}"
    lines = [
        f"{MANAGED_BEGIN} 由 `vk init --apply` 生成并在重跑时更新；此块之外的内容不会被动。 -->",
        "# 知识库规则 / Vault rules",
        "",
        "这是一个 Obsidian 知识库，由 vault-keeper 维护。在这里写东西之前先读完这一段。",
    ]
    if rules:
        lines.append(f"完整规则在 [[{Path(rules).stem}]]，和本块冲突时以它为准。")
    lines += ["", "## 目录角色", "", "| 目录 | 角色 | 规则 |", "|---|---|---|"]
    role_rule = {
        "raw": "原始素材：只追加、不编辑；每个文件标来源",
        "wiki": "沉淀层：agent 写、人审；frontmatter 必填见下",
        "private": "私人：不主动引用，不进 CATALOG",
        "templates": "模板：新笔记从这里复制，填掉 {{date}} {{title}}",
        "archive": "归档：默认不读；要「删」的笔记移到这里",
        "attachments": "附件",
        "excluded": "不读、不写、不在任何输出里提到内容",
    }
    for role in ("raw", "wiki", "private", "templates", "archive", "excluded"):
        ds = roles.get(role) or []
        if ds:
            lines.append(f"| {', '.join(d + '/' for d in ds)} | {role} | {role_rule[role]} |")
    if required:
        lines += ["", "frontmatter 必填：" + "；".join(f"`{d}/` → {', '.join(f)}" for d, f in sorted(required.items()))]
    cat = cfg.get("catalog", "CATALOG.md")
    lines += [
        "",
        "## 写笔记之前",
        "",
        f"1. 先读 `{cat}`（生成的目录，每篇一行）找同主题笔记：能更新旧笔记就不新建。",
        "2. 新建用 `vk new <目录> \"<标题>\"`，它会套模板、起文件名；或手动复制 templates/ 里的模板。",
        "3. 至少链接 2 篇相关笔记（`[[路径|标题]]`，同名文件多时必须带路径）。",
        "4. 推翻旧结论：旧笔记 `status: superseded`，新笔记写 `supersedes:` 和「为什么变了」。",
        "",
        "## 写完之后",
        "",
        f"```\n{vk} lint        # 断链、缺字段、模板残留\n{vk} index       # 重建 {cat}\n{vk} sync --apply  # git 提交并推送（Claude Code 装了钩子会自动做）\n```",
        "",
        "## 永远不做",
        "",
        "- 不删文件：移到 archive/。不改 raw/ 里的内容。",
        f"- 不手改 `{cat}`，它是生成的。",
        "- 不读、不引用 excluded 目录里的任何内容，不读 `.env` / 密钥文件。",
        MANAGED_END,
    ]
    return "\n".join(lines) + "\n"


def upsert_managed(path: Path, block: str) -> str:
    if not path.exists():
        path.write_text(block, encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8")
    if MANAGED_BEGIN in text and MANAGED_END in text:
        s = text.index(MANAGED_BEGIN)
        e = text.index(MANAGED_END) + len(MANAGED_END)
        new = text[:s] + block.rstrip("\n") + text[e:]
        if new == text:
            return "unchanged"
        path.write_text(new, encoding="utf-8")
        return "updated"
    path.write_text(block + "\n" + text, encoding="utf-8")
    return "prepended"


# --------------------------------------------------------------------------- 命令

def cmd_init(args) -> int:
    vault = find_vault(args.vault)
    cfg = load_vault_cfg(vault)
    roles, stats = infer_roles(vault, cfg)
    new_cfg = {"version": 1, "roles": {r: ds for r, ds in roles.items() if ds}}
    for k in ("catalog", "root_allow", "sync"):
        if k in cfg:
            new_cfg[k] = cfg[k]
    new_cfg.setdefault("catalog", "CATALOG.md")
    new_cfg.setdefault("sync", {"push_every_min": 30, "auto_pull": True})
    # 规则文件
    rules = cfg.get("rules_file")
    if not rules:
        for cand in ("SCHEMA.md", "RULES.md", "schema.md", "README.md"):
            if (vault / cand).exists():
                rules = cand
                break
    if rules:
        new_cfg["rules_file"] = rules
    notes = load_notes(vault, new_cfg)
    new_cfg["required"] = cfg.get("required") or required_fields_for(notes)
    gs = git_state(vault)
    secret_dirs = [d.name for d in vault.iterdir() if SECRET_DIR_RE.match(d.name) and d.name != ".git"]
    tracked_secret = [d for d in secret_dirs if gs["git"] and git(vault, "ls-files", "--", d).stdout.strip()]
    secret_dirs = [d for d in secret_dirs if d not in tracked_secret]
    for k in ("secret_dirs_ok",):
        if k in cfg:
            new_cfg[k] = cfg[k]

    quiet = getattr(args, "quiet", False)
    say = (lambda *a, **k: None) if quiet else print
    say(f"# vault-keeper init · {vault}")
    say(f"笔记 {len(notes)} 篇；git：{'有' if gs['git'] else '无'}" + (f"，远端 {'有' if gs.get('remote') else '无'}" if gs["git"] else ""))
    say("\n## 目录角色（推断，可改 .vault-keeper.json）")
    say("| 目录 | 角色 | md | 其他 |\n|---|---|---|---|")
    for name, s in sorted(stats.items(), key=lambda kv: (ROLES.index(kv[1]["role"]), kv[0])):
        say(f"| {name}/ | {s['role']} | {s['md']} | {s['other']} |")
    if new_cfg["required"]:
        say("\n## 必填字段（该目录 ≥90% 笔记都有）")
        for d, f in sorted(new_cfg["required"].items()):
            say(f"- {d}/: {', '.join(f)}")
    say(f"\n规则文件：{rules or '无（将只用生成的块）'}")
    if secret_dirs:
        say(f"疑似密钥目录：{', '.join(secret_dirs)} → 标为 excluded，并加入 .gitignore")
    if tracked_secret:  # 这条无论 quiet 都要说
        print(f"注意：{', '.join(d + '/' for d in tracked_secret)} 看起来像密钥目录但已被 git 跟踪，.gitignore 管不了它。"
              "确认里面没有明文就在 .vault-keeper.json 加 \"secret_dirs_ok\": [\"…\"]；有明文就 git rm --cached 后再 ignore。")
    say("\n## 将写入")
    say(f"- {CONFIG_NAME}（配置）")
    say(f"- CLAUDE.md、AGENTS.md（管理块，已有文件则只插入/更新块）")
    say(f"- {new_cfg['catalog']}（生成的目录）")
    say("- .gitignore 补：.obsidian/workspace*  .trash/  .DS_Store" + ("  " + "  ".join(d + "/" for d in secret_dirs) if secret_dirs else ""))
    if not gs["git"]:
        say("- git：未初始化。" + ("加 --git 会 `git init`。" if not args.git else "将 `git init`。"))
    if not args.apply:
        print("\n（dry-run。加 --apply 执行。不会提交 git、不会装钩子。）")
        return 0

    save_vault_cfg(vault, new_cfg)
    block = managed_block(vault, new_cfg)
    r1 = upsert_managed(vault / "CLAUDE.md", block)
    ag = vault / "AGENTS.md"
    if ag.is_symlink() or not ag.exists():
        if ag.is_symlink():
            ag.unlink()
        try:
            ag.symlink_to("CLAUDE.md")
            r2 = "symlink→CLAUDE.md"
        except OSError:
            shutil.copy2(vault / "CLAUDE.md", ag)
            r2 = "copied"
    else:
        r2 = upsert_managed(ag, block)
    gi = vault / ".gitignore"
    existing = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    want = [".obsidian/workspace*", ".trash/", ".DS_Store"] + [d + "/" for d in secret_dirs]
    added = [w for w in want if w not in existing and w.rstrip("/") not in existing]
    if added or not gi.exists():
        gi.write_text("\n".join(existing + added).strip("\n") + "\n", encoding="utf-8")
    if args.git and not gs["git"]:
        git(vault, "init", "-q")
    # 没有模板目录时放一套默认模板（insight / decision / raw / project）
    if not new_cfg["roles"].get("templates"):
        tdir = vault / "templates"
        src = Path(__file__).resolve().parent.parent / "assets" / "templates"
        if not tdir.exists() and src.is_dir():
            shutil.copytree(src, tdir)
            new_cfg["roles"]["templates"] = ["templates"]
            save_vault_cfg(vault, new_cfg)
            print(f"已放入默认模板 templates/（{', '.join(p.stem for p in sorted(tdir.glob('*.md')))}）")
    (vault / new_cfg["catalog"]).write_text(build_catalog(vault, new_cfg, load_notes(vault, new_cfg)), encoding="utf-8")
    g = load_global()
    vs = g.setdefault("vaults", [])
    sv = str(vault)
    hp = str(home())
    if sv.startswith(hp + "/"):
        sv = "~/" + sv[len(hp) + 1:]
    if sv not in vs:
        vs.append(sv)
    save_global(g)
    journal(vault, "init", roles=new_cfg["roles"], required=new_cfg["required"])
    print(f"{'' if quiet else chr(10)}已写入：{CONFIG_NAME}；CLAUDE.md（{r1}）；AGENTS.md（{r2}）；{new_cfg['catalog']}；.gitignore（+{len(added)}）" + ("" if quiet else f"；注册到 {global_cfg_path()}"))
    say("下一步：vk lint 看问题；vk hooks --install 让 Claude Code 自动提交；有第二台机器就 vk sync --apply。")
    return 0


def cmd_lint(args) -> int:
    vault = find_vault(args.vault)
    cfg = load_vault_cfg(vault)
    if not cfg:
        warn("没有 .vault-keeper.json，用名字推断角色（建议先 vk init --apply）。")
        roles, _ = infer_roles(vault, {})
        cfg = {"roles": {r: d for r, d in roles.items() if d}}
    notes = load_notes(vault, cfg)
    r = lint(vault, cfg, notes)
    (state_dir(vault) / "last-lint.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(render_lint(r, full=args.full))
    return 1 if any(i["level"] == "warn" for i in r["issues"]) and args.strict else 0


def cmd_index(args) -> int:
    vault = find_vault(args.vault)
    cfg = load_vault_cfg(vault) or {"roles": {r: d for r, d in infer_roles(vault, {})[0].items() if d}}
    notes = load_notes(vault, cfg)
    text = build_catalog(vault, cfg, notes)
    out = Path(args.out).expanduser() if args.out else vault / cfg.get("catalog", "CATALOG.md")
    if args.dry_run:
        print(text)
        return 0
    out.write_text(text, encoding="utf-8")
    print(f"已生成 {out}（{sum(1 for l in text.splitlines() if l.startswith('- '))} 条）")
    return 0


def bigrams(s: str) -> set:
    s = re.sub(r"\s+", " ", s.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def cmd_find(args) -> int:
    vault = find_vault(args.vault)
    cfg = load_vault_cfg(vault) or {}
    notes = [n for n in load_notes(vault, cfg) if n.role in ("wiki", "raw", "root")]
    q = " ".join(args.query)
    qb = bigrams(q)
    qwords = {w for w in re.split(r"[\s,，、/]+", q.lower()) if w}
    rows = []
    for n in notes:
        hay = f"{n.title} {n.one} {' '.join(n.tags())} {n.rel}".lower()
        hb = bigrams(hay)
        score = len(qb & hb) / len(qb) if qb else 0
        for w in qwords:
            if w in n.title.lower():
                score += 0.8
            elif w in hay:
                score += 0.4
        if score > 0.15:
            rows.append((score, n))
    rows.sort(key=lambda x: -x[0])
    if not rows:
        print("没有匹配的笔记。可以新建：vk new <目录> \"<标题>\"")
        return 1
    print(f"「{q}」最相关：")
    for score, n in rows[: args.n]:
        st = f" [{n.status()}]" if n.status() and n.status() != "active" else ""
        print(f"  {score:.2f}  [[{n.rel[:-3]}|{n.title}]]{st}\n        {n.one or '—'}")
    return 0


def cmd_new(args) -> int:
    vault = find_vault(args.vault)
    cfg = load_vault_cfg(vault)
    roles = cfg.get("roles") or {}
    target_dir = vault / args.dir
    if not target_dir.is_dir():
        sys.exit(f"[vk] 目录不存在：{args.dir}。先建目录，或选 CATALOG 里已有的目录。")
    top = args.dir.split("/")[0]
    if top in roles.get("raw", []) and not args.raw:
        sys.exit(f"[vk] {top}/ 是 raw 层。原始素材请加 --raw 确认；分析和结论写到 wiki 目录。")
    # 模板
    tdirs = [vault / t for t in roles.get("templates", ["templates"])]
    tmpl: Optional[Path] = None
    if args.template:
        for td in tdirs:
            for cand in (td / args.template, td / f"{args.template}.md"):
                if cand.exists():
                    tmpl = cand
        if not tmpl:
            sys.exit(f"[vk] 找不到模板 {args.template}")
    else:
        singular = top[:-1] if top.endswith("s") else top
        for td in tdirs:
            for cand in (td / f"{top}.md", td / f"{singular}.md"):
                if cand.exists():
                    tmpl = cand
                    break
    existing = [p.stem for p in target_dir.glob("*.md")]
    date_style = bool(existing) and sum(1 for s in existing if DATE_PREFIX_RE.match(s)) / len(existing) >= 0.5
    slug = re.sub(r"[\\/:*?\"<>|#^\[\]]+", "-", args.title).strip(" -")
    fname = f"{today()}-{slug}.md" if (date_style or args.dated) else f"{slug}.md"
    path = target_dir / fname
    if path.exists():
        sys.exit(f"[vk] 已存在：{path.relative_to(vault)}")
    if tmpl:
        body = tmpl.read_text(encoding="utf-8")
        body = re.sub(r"\{\{\s*date(:[^}]*)?\s*\}\}", today(), body)
        body = re.sub(r"\{\{\s*time(:[^}]*)?\s*\}\}", dt.datetime.now().strftime("%H:%M"), body)
        body = re.sub(r"\{\{\s*title\s*\}\}", args.title, body)
    else:
        body = f"---\ntype: {top[:-1] if top.endswith('s') else top}\ndate: {today()}\ntags: []\nstatus: active\n---\n\n# {args.title}\n\n"
    path.write_text(body, encoding="utf-8")
    req = (cfg.get("required") or {}).get(top)
    print(f"已创建 {path.relative_to(vault)}" + (f"（模板 {tmpl.relative_to(vault)}）" if tmpl else "（无模板，用了最小 frontmatter）"))
    if req:
        print(f"必填字段：{', '.join(req)}")
    print("写完后：vk lint && vk index")
    return 0


def _lock(vault: Path) -> Optional[Path]:
    lk = state_dir(vault) / "sync.lock"
    try:
        if lk.exists() and dt.datetime.now().timestamp() - lk.stat().st_mtime > 300:
            lk.unlink()
        fd = os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return lk
    except FileExistsError:
        return None


def _summarize_changes(files: List[str]) -> str:
    by_top: Dict[str, int] = {}
    for f in files:
        top = f.split("/")[0] if "/" in f else "(root)"
        by_top[top] = by_top.get(top, 0) + 1
    parts = [f"{k} {v}" for k, v in sorted(by_top.items(), key=lambda kv: -kv[1])[:5]]
    return "，".join(parts)


def do_sync(vault: Path, cfg: dict, apply: bool, push: Optional[bool], msg: Optional[str], auto: bool, quiet: bool) -> int:
    """返回 0 正常，2 冲突/错误（auto 模式下仍返回 0，并把错误写进状态文件）。"""
    out: List[str] = []
    sd = state_dir(vault)
    status_file = sd / "last-sync.json"
    result = {"ts": now_iso(), "ok": True, "error": "", "committed": 0, "pushed": False, "pulled": False}

    def say(s: str):
        if not quiet:
            print(s)

    if not is_git(vault):
        say("这个库不是 git 仓库。vk init --apply --git 可初始化；没有 git 就没有同步与历史。")
        return 0
    gs = git_state(vault)
    if not gs["dirty"] and not gs.get("ahead") and not gs.get("behind"):
        say("已同步，无改动。")
        if auto:
            status_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return 0
    if not apply:
        say(f"# vk sync（dry-run）· {vault.name}")
        if gs["dirty"]:
            say(f"将提交 {gs['dirty']} 个文件：{_summarize_changes(gs['dirty_files'])}")
        if gs.get("ahead"):
            say(f"将推送 {gs['ahead']} 个本地 commit")
        if gs.get("behind"):
            say(f"远端领先 {gs['behind']} 个 commit，将 pull --rebase")
        if not gs["remote"]:
            say("没有远端：只会本地提交。加远端：gh repo create <name> --private --source . --push")
        say("加 --apply 执行。")
        return 0
    lk = _lock(vault)
    if lk is None:
        say("另一个 vk sync 正在跑，跳过。")
        return 0
    try:
        if gs["dirty"]:
            git(vault, "add", "-A")
            m = msg or f"vault-keeper: {today()} {gs['dirty']} 个文件（{_summarize_changes(gs['dirty_files'])}）"
            r = git(vault, "commit", "-q", "-m", m)
            if r.returncode != 0:
                result.update(ok=False, error="commit 失败: " + (r.stderr.strip() or r.stdout.strip())[:300])
                say("ERROR " + result["error"])
                return 2
            result["committed"] = gs["dirty"]
            say(f"已提交 {gs['dirty']} 个文件：{m}")
        if gs["remote"]:
            do_pull = (cfg.get("sync") or {}).get("auto_pull", True)
            if do_pull:
                r = git(vault, "pull", "--rebase", "-q")
                if r.returncode != 0:
                    conflicted = [f for f in git(vault, "diff", "--name-only", "-z", "--diff-filter=U").stdout.split("\0") if f]
                    git(vault, "rebase", "--abort")
                    result.update(ok=False, error="CONFLICT pull --rebase 失败，已回退到本地状态。冲突文件: " + ", ".join(conflicted[:10]))
                    say(result["error"])
                    say("处理：手动 `git pull --rebase`，解决冲突后 `git rebase --continue`，再 vk sync --apply。本地内容没有丢。")
                    return 2
                result["pulled"] = True
            want_push = push if push is not None else True
            if auto and push is None:
                every = int((cfg.get("sync") or {}).get("push_every_min", 30))
                last = 0.0
                if status_file.exists():
                    try:
                        prev = json.loads(status_file.read_text())
                        if prev.get("pushed"):
                            last = dt.datetime.fromisoformat(prev["ts"]).timestamp()
                    except Exception:  # noqa: BLE001
                        pass
                ahead = git_state(vault).get("ahead") or 0
                want_push = ahead > 0 and (dt.datetime.now().timestamp() - last > every * 60 or ahead >= 10)
            if want_push:
                r = git(vault, "push", "-q")
                if r.returncode != 0:
                    result.update(ok=False, error="push 失败: " + (r.stderr.strip() or r.stdout.strip())[:300])
                    say("ERROR " + result["error"])
                    return 2
                result["pushed"] = True
                say("已推送。")
            elif auto:
                say("已本地提交；推送按周期批量。")
        return 0
    finally:
        try:
            lk.unlink()
        except Exception:  # noqa: BLE001
            pass
        status_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        journal(vault, "sync", **{k: v for k, v in result.items() if k != "ts"})


def cmd_sync(args) -> int:
    vaults = registered_vaults() if args.all else [find_vault(args.vault)]
    if args.all and not vaults:
        return 0
    rc = 0
    for v in vaults:
        cfg = load_vault_cfg(v)
        r = do_sync(v, cfg, apply=args.apply or args.auto, push=(True if args.push else False if args.no_push else None),
                    msg=args.message, auto=args.auto, quiet=args.quiet)
        rc = max(rc, r)
    return 0 if args.auto else rc


def health_line(vault: Path) -> Optional[str]:
    cfg = load_vault_cfg(vault)
    bits = []
    gs = git_state(vault)
    if gs["git"]:
        if gs["dirty"]:
            d = gs.get("last_commit_days")
            bits.append(f"{gs['dirty']} 个文件未提交" + (f"（上次提交 {d} 天前）" if d is not None and d >= 2 else ""))
        if gs.get("ahead"):
            bits.append(f"{gs['ahead']} 个 commit 未推送")
        if gs.get("behind"):
            bits.append(f"远端领先 {gs['behind']}")
    sd = state_dir(vault)
    ls = sd / "last-sync.json"
    if ls.exists():
        try:
            prev = json.loads(ls.read_text())
            if not prev.get("ok"):
                bits.append("上次自动同步失败：" + prev.get("error", "").split("。")[0][:70])
        except Exception:  # noqa: BLE001
            pass
    ll = sd / "last-lint.json"
    if ll.exists():
        try:
            prev = json.loads(ll.read_text())
            age = (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(prev["ts"])).days
            warns = sum(n for c, n in prev["counts"].items() if LINT_LEVEL.get(c) == "warn" and not c.startswith("G"))
            if age > 7:
                bits.append(f"{age} 天没 lint")
            elif warns:
                bits.append(f"lint 待处理 {warns} 条")
        except Exception:  # noqa: BLE001
            pass
    else:
        bits.append("从未 lint")
    cat = vault / (cfg.get("catalog", "CATALOG.md"))
    if not cat.exists():
        bits.append("无目录")
    if not (vault / "CLAUDE.md").exists() and not (vault / "AGENTS.md").exists():
        bits.append("缺 CLAUDE.md")
    if not bits:
        return None
    return f"vault-keeper[{vault.name}]：{'，'.join(bits)}。运行 /vault-keeper 整理。"


def cmd_health(args) -> int:
    vaults = [find_vault(args.vault)] if args.vault else registered_vaults()
    lines = [l for l in (health_line(v) for v in vaults) if l]
    if not lines:
        return 0
    msg = "\n".join(lines)
    if args.json:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}}, ensure_ascii=False))
    else:
        print(msg)
    return 0


def cmd_hooks(args) -> int:
    me = Path(__file__).resolve()
    py = "python3"
    hooks = {
        "SessionStart": [{"matcher": "startup", "hooks": [
            {"type": "command", "command": f"{py} {me} health", "timeout": 10}]}],
        "Stop": [{"hooks": [
            {"type": "command", "command": f"{py} {me} sync --auto --all --quiet", "timeout": 60}]}],
    }
    if not args.install:
        print("把下面这段合并进 ~/.claude/settings.json 的 \"hooks\"（或加 --install 让我合并，会先备份）：")
        print(json.dumps({"hooks": hooks}, ensure_ascii=False, indent=2))
        print("\nStop 钩子只提交已注册的库（~/.agents/vault-keeper.json），推送按 push_every_min 批量，出错不打断你，下次启动时提示。")
        print("Codex 没有钩子：用 vk schedule --install 装一个每 10 分钟的 launchd 任务兜底。")
        return 0
    cfg_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (home() / ".claude"))
    sp = cfg_dir / "settings.json"
    data = {}
    if sp.exists():
        bak = sp.with_name(f"settings.json.bak-vk-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(sp, bak)
        print(f"已备份 {bak}")
        data = json.loads(sp.read_text(encoding="utf-8"))
    h = data.setdefault("hooks", {})
    for ev, entries in hooks.items():
        lst = h.setdefault(ev, [])
        for e in entries:
            if not any(json.dumps(x, sort_keys=True) == json.dumps(e, sort_keys=True) for x in lst):
                lst.append(e)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {sp}。新会话生效。")
    return 0


def cmd_schedule(args) -> int:
    me = Path(__file__).resolve()
    minutes = args.every
    if sys.platform == "darwin":
        label = "com.vault-keeper.sync"
        plist_path = home() / "Library" / "LaunchAgents" / f"{label}.plist"
        py = shutil.which("python3") or "/usr/bin/python3"
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    <string>{py}</string><string>{me}</string><string>sync</string><string>--auto</string><string>--all</string><string>--quiet</string>
  </array>
  <key>StartInterval</key><integer>{minutes * 60}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{home()}/.agents/.vault-keeper/launchd.log</string>
  <key>StandardErrorPath</key><string>{home()}/.agents/.vault-keeper/launchd.log</string>
</dict></plist>
"""
        if not args.install:
            print(f"将写入 {plist_path}（每 {minutes} 分钟 vk sync --auto --all）：\n{plist}\n加 --install 执行。卸载：launchctl unload {plist_path} && rm {plist_path}")
            return 0
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        (home() / ".agents" / ".vault-keeper").mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist, encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
        r = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
        print(f"已写入并加载 {plist_path}" + (f"（launchctl: {r.stderr.strip()}）" if r.stderr.strip() else ""))
        return 0
    line = f"*/{minutes} * * * * {shutil.which('python3') or 'python3'} {me} sync --auto --all --quiet"
    print("Linux：把这行加进 crontab -e：\n" + line)
    return 0


def detect_obsidian_vaults() -> List[Path]:
    """读 Obsidian 自己的登记表，拿到本机所有打开过的库。"""
    cands = [
        home() / "Library" / "Application Support" / "obsidian" / "obsidian.json",   # macOS
        home() / ".config" / "obsidian" / "obsidian.json",                            # Linux
        Path(os.environ.get("APPDATA", "")) / "obsidian" / "obsidian.json" if os.environ.get("APPDATA") else None,
    ]
    out: List[Path] = []
    for c in cands:
        if c and c.exists():
            try:
                data = json.loads(c.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            for v in (data.get("vaults") or {}).values():
                p = Path(v.get("path", ""))
                if p.is_dir() and (p / ".obsidian").exists() and p not in out:
                    out.append(p)
    return out


def ensure_skill_projection() -> str:
    """装在 ~/.agents/skills 里时，给 Claude Code 补一个软链接（Codex 直接读库，不需要）。"""
    me = Path(__file__).resolve().parent.parent
    claude = home() / ".claude"
    if not claude.exists():
        return "Claude Code 未检测到"
    link = claude / "skills" / me.name
    if link.exists() or link.is_symlink():
        return "Claude Code 已可见"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(me)
        return f"已链接到 {link}"
    except OSError as e:
        return f"软链接失败（{e}）；用 npx skills add 安装可绕过"


def _ask(prompt: str, default_no: bool = True, yes: bool = False) -> bool:
    if yes:
        return not default_no
    if not sys.stdin.isatty():
        return not default_no
    try:
        a = input(prompt + (" [y/N] " if default_no else " [Y/n] ")).strip().lower()
    except EOFError:
        return not default_no
    if not a:
        return not default_no
    return a in ("y", "yes", "是", "好")


def cmd_setup(args) -> int:
    """一键：找库 → init → git → 同步 → 钩子 → 定时 → 体检。幂等，可重跑。"""
    print(f"# vault-keeper setup v{VERSION}")
    vaults: List[Path] = [expand(v).resolve() for v in (args.vault or [])]
    if not vaults:
        vaults = detect_obsidian_vaults()
        if vaults:
            print(f"从 Obsidian 登记表找到 {len(vaults)} 个库：" + "，".join(str(v) for v in vaults))
    if not vaults:
        vaults = registered_vaults()
    if not vaults:
        print("没找到 Obsidian 库。指定路径：vk setup --vault <目录>（可多个）。纯 Markdown 目录也可以。")
        return 1
    print(ensure_skill_projection())
    gh_ok = shutil.which("gh") is not None and subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0
    summary = []
    for v in vaults:
        print(f"\n## {v}")
        cfg = load_vault_cfg(v)
        fresh = not cfg
        # 1. init（新文件、幂等）
        ns = argparse.Namespace(vault=str(v), apply=True, git=True, quiet=True)
        buf_rc = cmd_init(ns)
        if buf_rc != 0:
            summary.append((v, "init 失败"))
            continue
        cfg = load_vault_cfg(v)
        # 2. git：首个快照 + 远端
        gs = git_state(v)
        if gs["git"] and not git(v, "rev-parse", "HEAD").returncode == 0:
            git(v, "add", "-A")
            git(v, "commit", "-q", "-m", f"vault-keeper: {today()} 初始快照")
            print("已打初始快照（本地 git）。")
        gs = git_state(v)
        if gs["git"] and not gs["remote"]:
            name = f"vault-{v.name.lower()}" if v.name.lower() != "vault" else "vault"
            if gh_ok and (args.create_remote or _ask(f"没有远端。用 gh 建私有仓库 {name} 并推送？", yes=args.yes)):
                r = subprocess.run(["gh", "repo", "create", name, "--private", "--source", str(v), "--push"],
                                   capture_output=True, text=True)
                print(("已创建并推送：" + (r.stdout.strip() or name)) if r.returncode == 0 else ("建仓失败：" + r.stderr.strip()[:200]))
            else:
                print(f"没有远端：另一台电脑要同步时运行  gh repo create {name} --private --source {v} --push"
                      + ("" if gh_ok else "  （先 gh auth login）"))
        # 3. 同步一次
        if gs["git"]:
            do_sync(v, cfg, apply=True, push=None, msg=None, auto=False, quiet=False)
        # 4. 体检
        notes = load_notes(v, cfg)
        r = lint(v, cfg, notes)
        (state_dir(v) / "last-lint.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        warns = {c: n for c, n in r["counts"].items() if LINT_LEVEL[c] == "warn" and not c.startswith("G")}
        line = f"{len(notes)} 篇；要处理：" + ("，".join(f"{c}×{n}" for c, n in sorted(warns.items())) or "无")
        print("体检：" + line)
        summary.append((v, line + ("（新收编）" if fresh else "")))
    # 5. 钩子与定时
    print()
    if not args.no_hooks and (home() / ".claude").exists():
        cmd_hooks(argparse.Namespace(install=True))
    elif not args.no_hooks:
        print("Claude Code 未检测到，跳过钩子。")
    if not args.no_schedule and sys.platform == "darwin":
        cmd_schedule(argparse.Namespace(install=True, every=args.every))
    elif not args.no_schedule:
        cmd_schedule(argparse.Namespace(install=False, every=args.every))
    print("\n# 完成")
    for v, line in summary:
        print(f"- {v.name}: {line}")
    print("\n下一步：在库目录里打开 Claude Code 或 Codex，说一句「整理一下 vault」，agent 会把要你决定的事一次问完。")
    print("之后：每次会话结束自动提交，每 10 分钟后台推送，启动时一行提示。想撤掉：vk uninstall。")
    return 0


def cmd_uninstall(args) -> int:
    """撤掉钩子和定时任务；库里的文件和 git 不动。"""
    me = str(Path(__file__).resolve())
    sp = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (home() / ".claude")) / "settings.json"
    if sp.exists():
        data = json.loads(sp.read_text(encoding="utf-8"))
        h = data.get("hooks") or {}
        removed = 0
        for ev in list(h.keys()):
            kept = []
            for entry in h[ev]:
                hooks = [x for x in entry.get("hooks", []) if me not in x.get("command", "")]
                removed += len(entry.get("hooks", [])) - len(hooks)
                if hooks:
                    entry["hooks"] = hooks
                    kept.append(entry)
            if kept:
                h[ev] = kept
            else:
                h.pop(ev, None)
        sp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Claude Code 钩子：移除 {removed} 条")
    if sys.platform == "darwin":
        plist = home() / "Library" / "LaunchAgents" / "com.vault-keeper.sync.plist"
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            plist.unlink()
            print("launchd 定时任务：已卸载")
    print("库里的 CLAUDE.md / CATALOG.md / .vault-keeper.json 和 git 历史保留；不想要可以手动删。")
    return 0


def cmd_explain(args) -> int:
    for c in args.codes:
        c = c.upper()
        print(f"{c}: {EXPLAIN.get(c, '未知代码')}")
    return 0


def cmd_stats(args) -> int:
    vault = find_vault(args.vault)
    cfg = load_vault_cfg(vault)
    notes = load_notes(vault, cfg)
    by_role: Dict[str, int] = {}
    by_top: Dict[str, int] = {}
    links = 0
    with_fm = 0
    for n in notes:
        by_role[n.role] = by_role.get(n.role, 0) + 1
        by_top[n.top or "(root)"] = by_top.get(n.top or "(root)", 0) + 1
        links += len(n.links)
        with_fm += n.has_fm
    gs = git_state(vault)
    print(f"{vault}：{len(notes)} 篇，{with_fm} 篇有 frontmatter，{links} 个双链")
    print("按角色：" + "，".join(f"{r} {c}" for r, c in sorted(by_role.items(), key=lambda kv: -kv[1])))
    print("按目录：" + "，".join(f"{t} {c}" for t, c in sorted(by_top.items(), key=lambda kv: -kv[1])[:12]))
    if gs["git"]:
        print(f"git：未提交 {gs['dirty']}，未推送 {gs.get('ahead') if gs.get('ahead') is not None else '?'}，上次提交 {gs.get('last_commit_days')} 天前，远端 {'有' if gs['remote'] else '无'}")
    return 0


# --------------------------------------------------------------------------- main

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="vk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"vault-keeper {VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def V(p):
        p.add_argument("--vault", help="库目录（默认：当前目录向上找 .obsidian / .vault-keeper.json，或唯一已注册的库）")
        return p

    p = V(sub.add_parser("init", help="收编现有库：推断目录角色、写规则块、生成目录（默认 dry-run）"))
    p.add_argument("--apply", action="store_true")
    p.add_argument("--git", action="store_true", help="没有 git 时 git init")
    p.set_defaults(fn=cmd_init)

    p = V(sub.add_parser("lint", help="校验 frontmatter、双链、raw 不可变、git 状态等（只读）"))
    p.add_argument("--json", action="store_true")
    p.add_argument("--full", action="store_true")
    p.add_argument("--strict", action="store_true", help="有 warn 级问题时退出码 1")
    p.set_defaults(fn=cmd_lint)

    p = V(sub.add_parser("index", help="生成 CATALOG.md"))
    p.add_argument("--out")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_index)

    p = V(sub.add_parser("find", help="用自然语言找笔记"))
    p.add_argument("query", nargs="+")
    p.add_argument("-n", type=int, default=8)
    p.set_defaults(fn=cmd_find)

    p = V(sub.add_parser("new", help="按目录模板新建笔记"))
    p.add_argument("dir")
    p.add_argument("title")
    p.add_argument("--template")
    p.add_argument("--dated", action="store_true", help="强制日期前缀文件名")
    p.add_argument("--raw", action="store_true", help="确认在 raw 层新建")
    p.set_defaults(fn=cmd_new)

    p = V(sub.add_parser("sync", help="git add/commit/pull --rebase/push（默认 dry-run，永不 force）"))
    p.add_argument("--apply", action="store_true")
    p.add_argument("--auto", action="store_true", help="钩子模式：总是提交，按周期推送，永远退出 0")
    p.add_argument("--all", action="store_true", help="所有已注册的库")
    p.add_argument("--push", action="store_true")
    p.add_argument("--no-push", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("-m", "--message")
    p.set_defaults(fn=cmd_sync)

    p = V(sub.add_parser("health", help="SessionStart 用：有事才输出一行"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_health)

    p = sub.add_parser("hooks", help="Claude Code 钩子（SessionStart health + Stop sync --auto）")
    p.add_argument("--install", action="store_true")
    p.set_defaults(fn=cmd_hooks)

    p = sub.add_parser("schedule", help="launchd / cron 定时 sync --auto --all（给 Codex 等无钩子的 agent 兜底）")
    p.add_argument("--install", action="store_true")
    p.add_argument("--every", type=int, default=10, help="分钟")
    p.set_defaults(fn=cmd_schedule)

    p = V(sub.add_parser("stats", help="规模与 git 状态一览"))
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("setup", help="一键：找到本机 Obsidian 库 → init → git → 同步 → 钩子 → 定时 → 体检（幂等）")
    p.add_argument("--vault", action="append", help="指定库目录，可多次；不给则读 Obsidian 登记表")
    p.add_argument("--yes", "-y", action="store_true", help="不提问，取默认（不建远端）")
    p.add_argument("--create-remote", action="store_true", help="没有远端时用 gh 建私有仓库并推送")
    p.add_argument("--no-hooks", action="store_true")
    p.add_argument("--no-schedule", action="store_true")
    p.add_argument("--every", type=int, default=10)
    p.set_defaults(fn=cmd_setup)

    p = sub.add_parser("uninstall", help="撤掉钩子和定时任务，库里文件不动")
    p.set_defaults(fn=cmd_uninstall)

    p = sub.add_parser("explain", help="解释 lint 代码")
    p.add_argument("codes", nargs="+")
    p.set_defaults(fn=cmd_explain)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
