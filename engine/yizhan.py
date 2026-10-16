#!/usr/bin/env python3
"""忆栈引擎：把聊天归档 ZIP 沉淀为结构化 Markdown 知识库。纯标准库。"""

import argparse
import datetime
import hashlib
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

DEFAULT_TEMPLATE = "学习群"
PENDING = "待确认"
PENDING_HINT = "（AI 拿不准、待人确认：@ 候选 + 规则未命中；修订日报后跑 `yizhan learn` 回流规则）"
PENDING_CAP = 20

BUILTIN_TEMPLATES = {
    "学习群": {
        "name": "学习群",
        "sections": [
            {"name": "重要通知", "patterns": [r"通知", r"公告", r"请知悉", r"请大家"]},
            {"name": "技能与工具分享", "patterns": [r"工具", r"插件", r"教程", r"安装", r"技巧",
                                              r"开源", r"skill", r"脚本", r"模板"]},
            {"name": "实战案例与数据", "patterns": [r"案例", r"数据", r"转化", r"成交", r"收入",
                                              r"涨粉", r"实测", r"跑出", r"gmv"]},
            {"name": "有价值链接", "patterns": [r"https?://"]},
            {"name": "问答精华", "patterns": [r"@"], "hint": "（含 @ 的消息为问答候选，待 AI 确认）"},
            {"name": "成员动态", "patterns": [r"欢迎", r"加入", r"新人", r"生日", r"恭喜"]},
        ],
        # 命中优先级 ≠ 展示顺序：通知 > 链接 > 案例 > 工具 > 成员 > @（@ 最弱，作兜底）
        "priority": ["重要通知", "有价值链接", "实战案例与数据", "技能与工具分享", "成员动态", "问答精华"],
    },
    "投研": {
        "name": "投研",
        "sections": [
            {"name": "标的异动", "patterns": [r"异动", r"涨停", r"跌停", r"拉升", r"跳水", r"放量",
                                          r"缩量", r"突破", r"回调", r"建仓", r"减仓", r"加仓"]},
            {"name": "研报与链接", "patterns": [r"https?://", r"研报", r"财报", r"公告", r"调研"]},
            {"name": "情绪与信号", "patterns": [r"情绪", r"信号", r"分歧", r"一致", r"龙头", r"跟风",
                                           r"炸板", r"回封", r"复盘"]},
            {"name": "问答", "patterns": [r"@"], "hint": "（含 @ 的消息为问答候选，待 AI 确认）"},
            {"name": "成员动态", "patterns": [r"欢迎", r"加入", r"新人", r"生日", r"恭喜"]},
        ],
        "priority": ["研报与链接", "标的异动", "情绪与信号", "成员动态", "问答"],
    },
    "高新申报": {
        "name": "高新申报",
        "sections": [
            {"name": "政策窗口", "patterns": [r"政策", r"申报", r"通知", r"公告", r"窗口", r"指南",
                                          r"补贴", r"认定", r"高新", r"专精特新"]},
            {"name": "材料与截止", "patterns": [r"材料", r"截止", r"ddl", r"deadline", r"提交",
                                           r"清单", r"盖章", r"审计", r"软著", r"专利"]},
            {"name": "代理所动态", "patterns": [r"代理", r"律师", r"审查", r"答复", r"驳回", r"补正",
                                           r"受理", r"授权"]},
            {"name": "问答", "patterns": [r"@"], "hint": "（含 @ 的消息为问答候选，待 AI 确认）"},
            {"name": "成员动态", "patterns": [r"欢迎", r"加入", r"新人", r"生日", r"恭喜"]},
        ],
        "priority": ["政策窗口", "材料与截止", "代理所动态", "成员动态", "问答"],
    },
}
CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
          "十一", "十二", "十三", "十四", "十五"]

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

def land_one(zpath, vault, group_arg, group_first=False, group_label=None):
    zpath = Path(zpath)
    if not zpath.is_file():
        die(f"ZIP 不存在: {zpath}")
    try:
        with zipfile.ZipFile(zpath) as z:
            infos = [i for i in z.infolist() if not is_junk(i.filename)]
            names = [i.filename for i in infos]
            text = zip_text(z, infos)
            label = group_label or "--group 参数"
            if group_first and group_arg:
                group, src = group_arg, label
            else:
                group, src = infer_group(text, names)
                if not group:
                    group, src = group_arg, label if group_arg else None
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

def load_template(vault, name):
    """模板查找：vault 自定义文件优先，内置兜底（内置不依赖 vault 任何文件）。"""
    if "/" in name or "\\" in name or name.startswith("."):
        die(f"非法模板名: {name}", 2)
    p = vault / ".yizhan" / "templates" / f"{name}.json"
    if p.exists():
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            die(f"模板文件损坏: {p}（{e}）")
        if not t.get("sections"):
            die(f"模板缺少 sections: {p}")
        t.setdefault("name", name)
        return t
    if name in BUILTIN_TEMPLATES:
        return BUILTIN_TEMPLATES[name]
    die(f"未知模板: {name}（内置: {'/'.join(BUILTIN_TEMPLATES)}；或放 .yizhan/templates/{name}.json）", 2)


def load_group_cfg(vault):
    p = vault / ".yizhan" / "groups.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"群映射配置损坏: {p}（{e}）")


def resolve_template(vault, group, explicit, cfg):
    """群 → (模板名, 模板, overrides)：--template 显式 > groups.json 映射 > 学习群兜底。"""
    name, overrides = explicit, {}
    ent = cfg.get(group)
    if isinstance(ent, str) and not explicit:
        name = ent
    elif isinstance(ent, dict):
        if not explicit:
            name = ent.get("template")
        overrides = ent.get("overrides", {})
    if not name:
        name = DEFAULT_TEMPLATE
    return name, load_template(vault, name), overrides


def classify_line(s, tmpl):
    pats = {sec["name"]: sec.get("patterns", []) for sec in tmpl["sections"]}
    for name in tmpl.get("priority") or list(pats):
        if any(re.search(p, s, re.I) for p in pats.get(name, [])):
            return name
    return None


def build_digest(vault, date, template_name=None):
    """聚合当天批次，返回结构化日报数据（不落盘）。无批次返回 None。"""
    notes = sorted(vault.glob(f"*/{date}/batch-*/note.md"))
    if not notes:
        return None
    cfg = load_group_cfg(vault)
    bucket, hints, order = {}, {}, []
    pending = []                      # (批次标识, 消息文本, 摘录行)
    egroup = {}                       # 消息文本 -> 群（learn 归属用）
    batch_rows, groups, tused = [], set(), {}
    total_msg = total_att = 0
    for note in notes:
        fm, body = read_note(note)
        group = fm.get("group", note.parents[2].name)
        groups.add(group)
        tname, tmpl, overrides = resolve_template(vault, group, template_name, cfg)
        tused[group] = tname
        for sec in tmpl["sections"]:
            if sec["name"] not in bucket:
                order.append(sec["name"])
                bucket[sec["name"]] = []
                if sec.get("hint"):
                    hints[sec["name"]] = sec["hint"]
        mc = int(fm.get("message_count", 0))
        at = int(fm.get("attachments_total", 0))
        total_msg += mc
        total_att += at
        batch_rows.append((group, note.parent.name, mc, at, note.relative_to(vault)))
        bkey = f"{group}/{note.parent.name}"
        for m in parse_messages(body):
            for s in m["lines"]:
                entry = f"{m['sender']} {m['time'][:5]}：{s}"
                egroup.setdefault(s, group)
                target = overrides.get(s)
                if target == "忽略":
                    continue
                if target == PENDING:
                    pending.append((bkey, s, entry))
                    continue
                name = target if target in bucket else classify_line(s, tmpl)
                if name:
                    bucket[name].append((s, entry))
                if not target and ("@" in s or not name):
                    pending.append((bkey, s, entry))
    return {"order": order, "bucket": bucket, "hints": hints, "pending": pending,
            "egroup": egroup, "batch_rows": batch_rows, "groups": groups, "tused": tused,
            "total_msg": total_msg, "total_att": total_att}


def cmd_digest(args):
    vault = Path(args.vault)
    if not vault.is_dir():
        die(f"知识库目录不存在: {vault}")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
        die(f"--date 格式应为 YYYY-MM-DD: {args.date}", 2)
    run_digest(vault, args.date, args.template)


def run_digest(vault, date, template_name=None):
    data = build_digest(vault, date, template_name)
    if not data:
        print(f"{date} 没有批次: {vault}")
        return None
    tset = sorted(set(data["tused"].values()))
    tline = (tset[0] if len(tset) == 1
             else " · ".join(f"{g}={data['tused'][g]}" for g in sorted(data["tused"])))
    out = [
        f"# 群日报 · {date}",
        "",
        "> 由 `yizhan digest` 规则聚合生成：引擎只做结构化搬运，栏目摘要由 AI 补写。",
        f"> 栏目模板：{tline}",
        "",
    ]
    for idx, name in enumerate(data["order"]):
        out += [f"## {CN_NUM[idx]}、{name}", ""]
        if name in data["hints"]:
            out += [f"> {data['hints'][name]}", ""]
        out += ["**原始消息摘录**", ""]
        if data["bucket"][name]:
            out += [f"- {e}" for _, e in data["bucket"][name]]
        else:
            out += ["- （规则未命中，待 AI 通读全文补写）"]
        out += [""]
    out += [f"## {CN_NUM[len(data['order'])]}、{PENDING}", "",
            f"> {PENDING_HINT}", "", "**原始消息摘录**", ""]
    if data["pending"]:
        per = {}
        for bkey, _, entry in data["pending"]:
            per.setdefault(bkey, []).append(entry)
        for bkey, entries in per.items():
            out += [f"- {e}" for e in entries[:PENDING_CAP]]
            if len(entries) > PENDING_CAP:
                out.append(f"- …另有 {len(entries) - PENDING_CAP} 条未列出（{bkey}）")
    else:
        out += ["- （无待确认消息）"]
    out += [""]
    out += ["---", "", "## 批次清单", "",
            "| 群 | 批次 | 消息数 | 附件数 | 笔记 |", "|---|---|---|---|---|"]
    for g, b, mc, at, rel in data["batch_rows"]:
        out.append(f"| {g} | {b} | {mc} | {at} | [note.md]({rel}) |")
    out += ["", "## 当日统计", "",
            f"- 批次：{len(data['batch_rows'])}（覆盖群：{len(data['groups'])}）",
            f"- 消息总数：{data['total_msg']}",
            f"- 附件总数：{data['total_att']}", ""]

    ddir = vault / "日报"
    ddir.mkdir(exist_ok=True)
    target = ddir / f"{date}.md"
    target.write_text("\n".join(out), encoding="utf-8")
    hits = " · ".join(f"{n}×{len(data['bucket'][n])}" for n in data["order"])
    print(f"已生成 {target.relative_to(vault)} · 批次 {len(data['batch_rows'])} · "
          f"摘录命中: {hits} · {PENDING}×{len(data['pending'])}")
    return target


# ---------- learn ----------

SECTION_RE = re.compile(r"^##\s+[一二三四五六七八九十]+、(.+?)\s*$")


def cmd_learn(args):
    vault = Path(args.vault)
    if not vault.is_dir():
        die(f"知识库目录不存在: {vault}")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
        die(f"--date 格式应为 YYYY-MM-DD: {args.date}", 2)
    target = vault / "日报" / f"{args.date}.md"
    if not target.exists():
        die(f"日报不存在: {target}（先跑 digest 再修订）")
    data = build_digest(vault, args.date)
    if not data:
        die(f"{args.date} 没有批次: {vault}")

    skel = {}
    for sec in data["order"]:
        for text, _ in data["bucket"][sec]:
            skel.setdefault(text, sec)
    for _, text, _ in data["pending"]:
        skel.setdefault(text, PENDING)
    edited = {}
    cur = None
    for line in target.read_text(encoding="utf-8").splitlines():
        m = SECTION_RE.match(line)
        if m:
            cur = m.group(1)
            continue
        if line.startswith("## "):
            cur = None
        b = re.match(r"^- (.*\S)\s*$", line)
        if b and cur:
            t = b.group(1)
            if t.startswith("（") or t.startswith("…"):
                continue
            if "：" in t:
                edited.setdefault(t.split("：", 1)[1], cur)

    valid = set(data["order"]) | {PENDING, "忽略"}
    cfg = load_group_cfg(vault)
    moved = ignored = invalid = 0
    new_ov = {}
    for text, ssec in skel.items():
        esec = edited.get(text)
        if esec == ssec:
            continue
        tgt = esec or "忽略"
        if tgt not in valid:
            invalid += 1
            continue
        new_ov.setdefault(data["egroup"][text], {})[text] = tgt
        if esec:
            moved += 1
        else:
            ignored += 1
    stray = sum(1 for t in edited if t not in skel)
    for g, ovs in new_ov.items():
        ent = cfg.get(g)
        if not isinstance(ent, dict):
            ent = {"template": ent if isinstance(ent, str)
                   else data["tused"].get(g, DEFAULT_TEMPLATE)}
        ent.setdefault("overrides", {}).update(ovs)
        cfg[g] = ent
    if new_ov:
        yz = vault / ".yizhan"
        yz.mkdir(exist_ok=True)
        (yz / "groups.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    n_ov = sum(len(v) for v in new_ov.values())
    print(f"learn 完成：移动 {moved} 条 · 忽略 {ignored} 条 · override 写入 {n_ov} 条（群 {len(new_ov)} 个）")
    if stray:
        print(f"无法归属的手写条目 {stray} 条（非规则产物，已跳过）")
    if invalid:
        print(f"目标栏目无效 {invalid} 条（已跳过）")
    if not new_ov:
        print("无新增修正（日报与当前规则一致）")


# ---------- index ----------

def cmd_index(args):
    vault = Path(args.vault)
    if not vault.is_dir():
        die(f"知识库目录不存在: {vault}")
    run_index(vault)


def run_index(vault):
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


# ---------- sync ----------

DEFAULT_SOURCES = {
    "sources": [
        {"name": "群情报文件夹（目录名=群名）", "type": "dir_groups", "path": "~/群情报"},
        {"name": "WeChatBridge Inbox", "type": "inbox",
         "path": "~/Library/Application Support/WeChatBridge/Inbox/Ready"},
    ]
}


def sha256_of(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover(sources, vault):
    """发现待落库 ZIP：[(路径, 目录名群名或 None, 数据源名)]。数据源 A 排除知识库自身。"""
    found = []
    vres = vault.resolve()
    for s in sources.get("sources", []):
        if s.get("enabled") is False:
            continue
        base = Path(os.path.expanduser(s["path"]))
        if not base.is_dir():
            print(f"数据源不存在，跳过: {base}")
            continue
        if s.get("type") == "dir_groups":
            for d in sorted(base.iterdir()):
                if not d.is_dir() or d.name.startswith(".") or d.resolve() == vres:
                    continue
                for z in sorted(d.rglob("*.zip")):
                    found.append((z, d.name, s.get("name", s["type"])))
        elif s.get("type") == "inbox":
            for z in sorted(base.glob("*/items/*/*.zip")):
                found.append((z, None, s.get("name", s["type"])))
        else:
            print(f"未知数据源类型，跳过: {s}")
    return found


def cmd_sync(args):
    vault = Path(args.vault)
    vault.mkdir(parents=True, exist_ok=True)
    yz = vault / ".yizhan"
    yz.mkdir(exist_ok=True)
    cfg = yz / "sources.json"
    if not cfg.exists():
        cfg.write_text(json.dumps(DEFAULT_SOURCES, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        print(f"已创建默认数据源配置: {cfg}（可编辑增删）")
    try:
        sources = json.loads(cfg.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"数据源配置损坏: {cfg}（{e}）")
    landed_p = yz / "landed.json"
    try:
        landed = json.loads(landed_p.read_text(encoding="utf-8")) if landed_p.exists() else {}
    except json.JSONDecodeError as e:
        die(f"落库台账损坏: {landed_p}（{e}）")

    found = discover(sources, vault)
    new, unclaimed, skipped = [], [], 0
    dates = set()
    for zp, hint, sname in found:
        sha = sha256_of(zp)
        if sha in landed:
            skipped += 1
            continue
        if hint is not None:
            bdir = land_one(zp, vault, hint, group_first=True, group_label="目录名")
        else:
            bdir = land_one(zp, vault, "未认领", group_label="未认领兜底")
        group, date = bdir.parent.parent.name, bdir.parent.name
        landed[sha] = {"group": group, "batch": bdir.name,
                       "landed_at": datetime.datetime.now().isoformat(timespec="seconds"),
                       "path": str(zp)}
        dates.add(date)
        new.append((group, date, bdir.name))
        if group == "未认领":
            unclaimed.append((group, date, bdir.name, zp.name))
    landed_p.write_text(json.dumps(landed, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    if dates:
        for d in sorted(dates):
            run_digest(vault, d)
        run_index(vault)
    print(f"sync 完成：新落库 {len(new)} 批 · 跳过 {skipped} 批 · 发现 {len(found)} 个 ZIP")
    if dates:
        print("受影响日期（已生成日报骨架）: " + "、".join(sorted(dates)))
    if unclaimed:
        print(f"未认领 {len(unclaimed)} 批（落在「未认领/」，请手动认领）:")
        for g, d, b, zn in unclaimed:
            print(f"  - {g}/{d}/{b} ← {zn}")


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
    p.add_argument("--template", help="栏目模板（内置: 学习群/投研/高新申报；或 .yizhan/templates/<名>.json）")
    p.set_defaults(fn=cmd_digest)

    p = sub.add_parser("learn", help="从手工修订的日报提取栏目修正，回流为该群 overrides")
    p.add_argument("vault", help="知识库目录")
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.set_defaults(fn=cmd_learn)

    p = sub.add_parser("index", help="重建知识库 README.md 索引")
    p.add_argument("vault", help="知识库目录")
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("sync", help="按数据源配置自动发现新 ZIP 并落库（sha256 去重）")
    p.add_argument("--vault", required=True, help="知识库目录")
    p.set_defaults(fn=cmd_sync)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
