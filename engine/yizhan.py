#!/usr/bin/env python3
"""忆栈引擎：把聊天归档 ZIP 沉淀为结构化 Markdown 知识库。纯标准库。"""

import argparse
import datetime
import json
import os
import re
import sys
import zipfile
from pathlib import Path

IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
VID_EXT = {".mp4", ".mov", ".m4v"}
GENERIC_DIRS = {"images", "image", "img", "videos", "video", "files", "file", "attachments"}

# 消息头两行：「·昵称」（昵称可含 emoji/空格，防御性兼容无 ·）+「YYYY年M月D日 H:MM[:SS]」（不补零）
DATE_RE = re.compile(
    r"^(?P<y>\d{4})年(?P<mo>\d{1,2})月(?P<d>\d{1,2})日\s+"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?\s*$"
)
TYPE_RE = re.compile(r"^\[(?P<t>[^\[\]]{1,8})\]\s*(?P<rest>.*)$")
GROUP_RES = [
    re.compile(r"群聊(?:名称)?\s*[:：]\s*(?P<g>.+)"),
    re.compile(r"群名(?:称)?\s*[:：]\s*(?P<g>.+)"),
    re.compile(r"^「(?P<g>.+?)」"),
    re.compile(r"^(?P<g>.+?)(?:（\d+）)?的聊天记录\s*$"),
]
BAD_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]+')

DISPLAY = ["重要通知", "技能与工具分享", "实战案例与数据", "有价值链接", "问答精华", "成员动态"]
SECTION_HINT = {"问答精华": "（含 @ 的消息为问答候选，待 AI 确认）"}
# 抽取优先级：通知 > 链接 > 案例 > 工具 > 成员 > @（@ 最弱，作待定兜底）
PRIORITY = ["重要通知", "有价值链接", "实战案例与数据", "技能与工具分享", "成员动态", "问答精华"]
PATTERNS = {
    "重要通知": [r"通知", r"公告", r"请知悉", r"请大家"],
    "有价值链接": [r"https?://"],
    "实战案例与数据": [r"案例", r"数据", r"转化", r"成交", r"收入", r"涨粉", r"实测", r"跑出", r"gmv"],
    "技能与工具分享": [r"工具", r"插件", r"教程", r"安装", r"技巧", r"开源", r"skill", r"脚本", r"模板"],
    "成员动态": [r"欢迎", r"加入", r"新人", r"生日", r"恭喜"],
    "问答精华": [r"@"],
}
CN_NUM = "一二三四五六"

KIND_CN = {"image": "图片", "video": "视频", "file": "文件"}


def classify(name):
    ext = os.path.splitext(name)[1].lower()
    if ext == ".txt":
        return "txt"
    if ext in IMG_EXT:
        return "image"
    if ext in VID_EXT:
        return "video"
    return "file"


def is_junk(name):
    base = name.rsplit("/", 1)[-1]
    return name.endswith("/") or "__MACOSX/" in name or base.startswith(".")


def count_kinds(names):
    c = {"txt": 0, "images": 0, "videos": 0, "files": 0}
    for n in names:
        k = classify(n)
        c[{"image": "images", "video": "videos"}.get(k, k if k == "txt" else "files")] += 1
    return c


def human_size(n):
    f = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if f < 1024 or u == "GB":
            return f"{int(f)} {u}" if u == "B" else f"{f:.1f} {u}"
        f /= 1024


def yq(s):
    s = str(s)
    if s and not re.search(r"[:#\[\]{}&*!|>'\"%@`,”\s]", s):
        return s
    return json.dumps(s, ensure_ascii=False)


def header_at(lines, i):
    """第 i 行若是消息头（昵称行 + 次行日期），返回 (sender, date, time, body 起始行)，否则 None。"""
    if i + 1 >= len(lines):
        return None
    line = lines[i].strip()
    if not line:
        return None
    m = DATE_RE.match(lines[i + 1].strip())
    if not m:
        return None
    if line.startswith("·") and len(line) > 1:
        sender = line[1:].strip()
    elif not line.startswith("[") and not DATE_RE.match(line):
        sender = line
    else:
        return None
    if not sender:
        return None
    date = f"{m.group('y')}-{int(m.group('mo')):02d}-{int(m.group('d')):02d}"
    time = f"{int(m.group('h')):02d}:{m.group('mi')}:{m.group('s') or '00'}"
    return sender, date, time, i + 2


def parse_messages(text):
    lines = text.splitlines()
    msgs = []
    i = 0
    while i < len(lines):
        h = header_at(lines, i)
        if not h:
            i += 1
            continue
        sender, date, time, j = h
        body = []
        while j < len(lines) and not header_at(lines, j):
            body.append(lines[j])
            j += 1
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        mtype = ""
        if body:
            tm = TYPE_RE.match(body[0].strip())
            if tm:
                mtype = tm.group("t")
                rest = tm.group("rest").strip()
                body = ([rest] if rest else []) + body[1:]
        msgs.append({"sender": sender, "date": date, "time": time,
                     "type": mtype, "lines": [b.strip() for b in body if b.strip()]})
        i = j
    return msgs


def sanitize(name):
    return BAD_CHARS.sub("_", name).strip(" .")


def infer_group(text, names):
    """返回 (群名, 来源)；推断失败返回 (None, None)。"""
    for line in text.splitlines()[:200]:
        line = line.strip()
        if DATE_RE.match(line):
            continue
        for r in GROUP_RES:
            m = r.search(line)
            if m:
                g = re.sub(r"（\d+）\s*$", "", m.group("g")).strip()
                if g and len(g) <= 50:
                    return sanitize(g), "TXT 推断"
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(tops) == 1:
        t = next(iter(tops))
        if t.lower() not in GENERIC_DIRS:
            return sanitize(t), "ZIP 结构"
    return None, None


def zip_text(z, infos):
    parts = []
    for i in infos:
        if classify(i.filename) == "txt":
            parts.append(z.read(i).decode("utf-8", "replace"))
    return "\n\n".join(parts)


def read_note(p):
    """读 note.md，返回 (frontmatter dict, 正文 str)。"""
    txt = p.read_text(encoding="utf-8")
    fm = {}
    m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
    if m:
        block = m.group(1)
        for line in block.splitlines():
            kv = re.match(r"^(\w+):\s*(.+)$", line)
            if kv:
                fm[kv.group(1)] = kv.group(2).strip().strip('"')
        t = re.search(r"^\s+total:\s*(\d+)", block, re.M)
        if t:
            fm["attachments_total"] = t.group(1)
    body = ""
    mb = re.search(r"^## 正文\n(.*?)^## 附件清单", txt, re.S | re.M)
    if mb:
        body = mb.group(1)
    return fm, body


def die(msg, code=1):
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(code)


# ---------- scan ----------

def cmd_scan(args):
    root = Path(args.dir)
    if not root.is_dir():
        die(f"目录不存在: {root}")
    try:
        since_ts = datetime.datetime.strptime(args.since, "%Y-%m-%d").timestamp()
    except ValueError:
        die(f"--since 格式应为 YYYY-MM-DD: {args.since}", 2)

    batches = []
    for p in root.rglob("*"):
        if not p.is_file() or any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        mt = p.stat().st_mtime
        if mt < since_ts:
            continue
        e = {"path": str(p), "rel": str(p.relative_to(root)),
             "mtime": datetime.datetime.fromtimestamp(mt).isoformat(timespec="seconds")}
        if p.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(p) as z:
                    infos = [i for i in z.infolist() if not is_junk(i.filename)]
                    names = [i.filename for i in infos]
                    e["zip_contents"] = count_kinds(names)
                    if any(classify(n) == "txt" for n in names):
                        text = zip_text(z, infos)
                        msgs = parse_messages(text)
                        if msgs:
                            e["messages"] = len(msgs)
                            e["first_date"] = msgs[0]["date"]
                            e["last_date"] = msgs[-1]["date"]
                        g, _ = infer_group(text, names)
                        if g:
                            e["group"] = g
            except zipfile.BadZipFile:
                e["zip_contents"] = "损坏的 ZIP"
        elif p.suffix.lower() in (".md", ".txt"):
            e["chars"] = p.stat().st_size
        else:
            continue
        batches.append(e)
    batches.sort(key=lambda e: e["mtime"])

    if args.json:
        print(json.dumps(batches, ensure_ascii=False, indent=2))
        return
    if not batches:
        print(f"自 {args.since} 起没有新批次: {root}")
        return
    total_msg = total_att = 0
    for e in batches:
        line = f"{e['mtime']}  {e['rel']}"
        zc = e.get("zip_contents")
        if isinstance(zc, dict):
            total = sum(zc.values())
            line += f"  [TXT×{zc['txt']} 图×{zc['images']} 视频×{zc['videos']} 文件×{zc['files']} 共{total}项]"
            total_att += total
            if e.get("messages"):
                total_msg += e["messages"]
                line += f"  消息×{e['messages']} {e['first_date']}~{e['last_date']}"
            if e.get("group"):
                line += f"  群「{e['group']}」"
        elif isinstance(zc, str):
            line += f"  [{zc}]"
        elif "chars" in e:
            line += f"  [{e['chars']}B]"
        print(line)
    print(f"共 {len(batches)} 个批次 · 消息 {total_msg} 条 · 附件 {total_att} 项")


# ---------- land ----------

def land_one(zpath, vault, group_arg):
    zpath = Path(zpath)
    if not zpath.is_file():
        die(f"ZIP 不存在: {zpath}")
    try:
        with zipfile.ZipFile(zpath) as z:
            infos = [i for i in z.infolist() if not is_junk(i.filename)]
            names = [i.filename for i in infos]
            text = zip_text(z, infos)
            group, src = infer_group(text, names)
            if not group:
                group, src = group_arg, "--group 参数" if group_arg else None
            if not group:
                group, src = "未命名群", "默认"
            msgs = parse_messages(text)
            if msgs:
                date, time = msgs[0]["date"], msgs[0]["time"]
            else:
                mt = datetime.datetime.fromtimestamp(zpath.stat().st_mtime)
                date, time = mt.strftime("%Y-%m-%d"), mt.strftime("%H:%M:%S")
            bdir = vault / group / date / ("batch-" + time.replace(":", ""))
            n = 2
            while bdir.exists():
                bdir = vault / group / date / f"batch-{time.replace(':', '')}-{n}"
                n += 1
            adir = bdir / "attachments"
            adir.mkdir(parents=True)
            counts = count_kinds(names)
            rows = []
            for i in infos:
                if classify(i.filename) == "txt":
                    continue
                rel = os.path.normpath(i.filename)
                if rel.startswith("..") or os.path.isabs(rel):
                    continue
                target = adir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(i))
                rows.append((i.filename, classify(i.filename), i.file_size))
    except zipfile.BadZipFile:
        die(f"损坏的 ZIP: {zpath}")

    declared = {"image": 0, "video": 0, "file": 0}
    for m in msgs:
        k = {"图片": "image", "视频": "video", "文件": "file"}.get(m["type"])
        if k:
            declared[k] += 1

    title = f"{group} · {date} {time[:5]} 聊天批次"
    fm = [
        "---",
        f"title: {yq(title)}",
        f"group: {yq(group)}",
        f"date: {date}",
        f'time: "{time}"',
        f"message_count: {len(msgs)}",
        f"txt_count: {counts['txt']}",
        "attachments:",
        f"  images: {counts['images']}",
        f"  videos: {counts['videos']}",
        f"  files: {counts['files']}",
        f"  total: {len(rows)}",
        f"source: {yq(zpath.name)}",
        "---",
    ]
    table = ["| 文件名 | 类型 | 大小 | TXT 声明 |", "|---|---|---|---|"]
    if rows:
        for fn, k, sz in rows:
            decl = f"{KIND_CN[k]}×{declared[k]}" if declared.get(k) else "—"
            table.append(f"| {fn} | {KIND_CN[k]} | {human_size(sz)} | {decl} |")
    else:
        table.append("| （无附件） | — | — | — |")
    body = [f"# {title}", "", "## 正文", "", text.rstrip() or "（无 TXT 聊天记录）",
            "", "## 附件清单", "", *table, ""]
    (bdir / "note.md").write_text("\n".join(fm) + "\n\n" + "\n".join(body), encoding="utf-8")
    print(f"已落库 {group}/{date}/{bdir.name} · 消息 {len(msgs)} 条 · 附件 {len(rows)} 项 · 群名来源: {src}")
    return bdir


def cmd_land(args):
    vault = Path(args.vault)
    vault.mkdir(parents=True, exist_ok=True)
    for z in args.zips:
        land_one(z, vault, args.group)


# ---------- digest ----------

def classify_line(s):
    for name in PRIORITY:
        if any(re.search(p, s, re.I) for p in PATTERNS[name]):
            return name
    return None


def cmd_digest(args):
    vault = Path(args.vault)
    if not vault.is_dir():
        die(f"知识库目录不存在: {vault}")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
        die(f"--date 格式应为 YYYY-MM-DD: {args.date}", 2)
    notes = sorted(vault.glob(f"*/{args.date}/batch-*/note.md"))
    if not notes:
        print(f"{args.date} 没有批次: {vault}")
        return

    bucket = {name: [] for name in DISPLAY}
    batch_rows = []
    total_msg = total_att = 0
    groups = set()
    for note in notes:
        fm, body = read_note(note)
        group = fm.get("group", note.parents[2].name)
        groups.add(group)
        mc = int(fm.get("message_count", 0))
        at = int(fm.get("attachments_total", 0))
        total_msg += mc
        total_att += at
        batch_rows.append((group, note.parent.name, mc, at, note.relative_to(vault)))
        for m in parse_messages(body):
            for s in m["lines"]:
                name = classify_line(s)
                if name:
                    bucket[name].append(f"{m['sender']} {m['time'][:5]}：{s}")

    out = [
        f"# 群日报 · {args.date}",
        "",
        "> 由 `yizhan digest` 规则聚合生成：引擎只做结构化搬运，栏目摘要由 AI 补写。",
        "",
    ]
    for idx, name in enumerate(DISPLAY):
        out += [f"## {CN_NUM[idx]}、{name}", ""]
        if name in SECTION_HINT:
            out += [f"> {SECTION_HINT[name]}", ""]
        out += ["**原始消息摘录**", ""]
        if bucket[name]:
            out += [f"- {x}" for x in bucket[name]]
        else:
            out += ["- （规则未命中，待 AI 通读全文补写）"]
        out += [""]
    out += ["---", "", "## 批次清单", "",
            "| 群 | 批次 | 消息数 | 附件数 | 笔记 |", "|---|---|---|---|---|"]
    for g, b, mc, at, rel in batch_rows:
        out.append(f"| {g} | {b} | {mc} | {at} | [note.md]({rel}) |")
    out += ["", "## 当日统计", "",
            f"- 批次：{len(batch_rows)}（覆盖群：{len(groups)}）",
            f"- 消息总数：{total_msg}",
            f"- 附件总数：{total_att}", ""]

    ddir = vault / "日报"
    ddir.mkdir(exist_ok=True)
    target = ddir / f"{args.date}.md"
    target.write_text("\n".join(out), encoding="utf-8")
    hits = " · ".join(f"{n}×{len(bucket[n])}" for n in DISPLAY)
    print(f"已生成 {target.relative_to(vault)} · 批次 {len(batch_rows)} · 摘录命中: {hits}")


# ---------- index ----------

def cmd_index(args):
    vault = Path(args.vault)
    if not vault.is_dir():
        die(f"知识库目录不存在: {vault}")
    groups = sorted(d for d in vault.iterdir()
                    if d.is_dir() and not d.name.startswith(".") and d.name != "日报")
    rows, gstats = [], {}
    for g in groups:
        for ddir in sorted(g.iterdir()):
            if not ddir.is_dir() or not re.match(r"^\d{4}-\d{2}-\d{2}$", ddir.name):
                continue
            notes = sorted(ddir.glob("batch-*/note.md"))
            if not notes:
                continue
            msgs = sum(int(read_note(n)[0].get("message_count", 0)) for n in notes)
            digest = (vault / "日报" / f"{ddir.name}.md").exists()
            rows.append((g.name, ddir.name, len(notes), msgs, digest))
            st = gstats.setdefault(g.name, {"days": 0, "batches": 0, "msgs": 0, "latest": ""})
            st["days"] += 1
            st["batches"] += len(notes)
            st["msgs"] += msgs
            st["latest"] = max(st["latest"], ddir.name)

    today = datetime.date.today().isoformat()
    out = ["# 忆栈知识库索引", "",
           f"> 由 `yizhan index` 自动重建（{today}），请勿手工编辑。", ""]
    out += ["## 群列表", "",
            "| 群名 | 天数 | 批次总数 | 消息总数 | 最新批次 |", "|---|---|---|---|---|"]
    for g in groups:
        st = gstats.get(g.name)
        if st:
            out.append(f"| [{g.name}]({g.name}/) | {st['days']} | {st['batches']} | {st['msgs']} | {st['latest']} |")
    out += ["", "## 群 × 日期", "",
            "| 群名 | 日期 | 批次 | 消息 | 日报 |", "|---|---|---|---|---|"]
    for g, d, nb, msgs, has_digest in sorted(rows, key=lambda r: (r[1], r[0]), reverse=True):
        link = f"[日报](日报/{d}.md)" if has_digest else "—"
        out.append(f"| {g} | {d} | {nb} | {msgs} | {link} |")
    digests = sorted((vault / "日报").glob("*.md")) if (vault / "日报").is_dir() else []
    if digests:
        out += ["", "## 日报一览", ""]
        out += [f"- [{p.stem}](日报/{p.name})" for p in digests]
    out.append("")
    (vault / "README.md").write_text("\n".join(out), encoding="utf-8")
    print(f"已重建 {vault / 'README.md'} · 群 {len(gstats)} 个 · 日期 {len(rows)} 天 · 日报 {len(digests)} 份")


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser(prog="yizhan", description="忆栈 · 知识库沉淀引擎（纯标准库）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="扫描沉淀目录，列出批次与 ZIP 统计")
    p.add_argument("dir", help="沉淀目录")
    p.add_argument("--since", default="1970-01-01", help="只列出该日期之后修改的批次")
    p.add_argument("--json", action="store_true", help="JSON 输出")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("land", help="解压 ZIP 并生成标准笔记落库")
    p.add_argument("zips", nargs="+", help="一个或多个 ZIP 归档")
    p.add_argument("--vault", required=True, help="知识库目录")
    p.add_argument("--group", help="群名（TXT/ZIP 推断失败时使用）")
    p.set_defaults(fn=cmd_land)

    p = sub.add_parser("digest", help="聚合当天批次，生成日报骨架（摘要留给 AI）")
    p.add_argument("vault", help="知识库目录")
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.set_defaults(fn=cmd_digest)

    p = sub.add_parser("index", help="重建知识库 README.md 索引")
    p.add_argument("vault", help="知识库目录")
    p.set_defaults(fn=cmd_index)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
