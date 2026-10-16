#!/usr/bin/env python3
"""忆栈引擎端到端测试：scan / land / digest / index（微信真实导出格式）。"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine" / "yizhan.py"
FIX = Path(__file__).resolve().parent / "fixtures"
ZIP_A = FIX / "fixture-a.zip"   # 标题行群名线索 · 2026-10-14 · 8 条消息（含 [图片]/[视频]）
ZIP_B = FIX / "fixture-b.zip"   # 无群名 · 2026年3月5日（不补零）· 3 条消息
ZIP_C = FIX / "fixture-c.zip"   # 跨天 2026-10-14→15 · 5 条消息 · 多行正文/带秒/无·昵称


def run(*args, check=True, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run([sys.executable, str(ENGINE), *map(str, args)],
                       capture_output=True, text=True, env=e)
    if check and r.returncode != 0:
        raise AssertionError(f"命令失败 {args}:\n{r.stdout}\n{r.stderr}")
    return r


def find_notes(vault):
    return sorted(vault.glob("*/*/batch-*/note.md"))


def section(md, keyword):
    """截取某个 `## 栏目` 到下一个 `## ` 之间的文本。"""
    m = re.search(rf"^## .*{re.escape(keyword)}.*$", md, re.M)
    assert m, f"日报缺栏目: {keyword}"
    nxt = re.search(r"^## ", md[m.end():], re.M)
    return md[m.end(): m.end() + nxt.start()] if nxt else md[m.end():]


class ScanTest(unittest.TestCase):
    def test_scan_text(self):
        out = run("scan", FIX).stdout
        self.assertIn("fixture-a.zip", out)
        self.assertIn("TXT×1", out)
        self.assertIn("图×2", out)
        self.assertIn("视频×1", out)
        self.assertIn("消息×8", out)
        self.assertIn("群「AI实战派」", out)
        self.assertIn("共 3 个批次", out)

    def test_scan_json(self):
        data = json.loads(run("scan", FIX, "--json").stdout)
        self.assertEqual(len(data), 3)
        a = next(e for e in data if e["rel"] == "fixture-a.zip")
        self.assertEqual(a["zip_contents"],
                         {"txt": 1, "images": 2, "videos": 1, "files": 0})
        self.assertEqual(a["messages"], 8)
        self.assertEqual(a["first_date"], "2026-10-14")
        self.assertEqual(a["group"], "AI实战派")

    def test_scan_json_crossday(self):
        data = json.loads(run("scan", FIX, "--json").stdout)
        c = next(e for e in data if e["rel"] == "fixture-c.zip")
        self.assertEqual(c["messages"], 5)
        self.assertEqual((c["first_date"], c["last_date"]), ("2026-10-14", "2026-10-15"))

    def test_scan_since_empty(self):
        out = run("scan", FIX, "--since", "2099-01-01").stdout
        self.assertIn("没有新批次", out)

    def test_scan_bad_dir(self):
        self.assertNotEqual(run("scan", "/nonexistent-dir", check=False).returncode, 0)


class LandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"

    def tearDown(self):
        self.tmp.cleanup()

    def test_land_structure_and_frontmatter(self):
        run("land", ZIP_A, "--vault", self.vault)
        notes = find_notes(self.vault)
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(note.parents[2].name, "AI实战派")
        self.assertEqual(note.parents[1].name, "2026-10-14")
        self.assertTrue(note.parent.name.startswith("batch-091200"))
        md = note.read_text(encoding="utf-8")
        for field in ('title: "AI实战派 · 2026-10-14 09:12 聊天批次"',
                      "group: AI实战派", "date: 2026-10-14", 'time: "09:12:00"',
                      "message_count: 8", "txt_count: 1", "images: 2", "videos: 1",
                      "  total: 3", "source: fixture-a.zip"):
            self.assertIn(field, md)
        self.assertIn("大家早上好，今天开始跑新的日报流水线", md)
        # 真实格式消息头原文保留
        self.assertIn("·小满\n2026年10月14日 9:15\n[链接]", md)
        # 附件清单含 TXT 声明列：[图片]×1 [视频]×1
        self.assertIn("| 文件名 | 类型 | 大小 | TXT 声明 |", md)
        self.assertIn("| images/photo1.png | 图片 | 69 B | 图片×1 |", md)
        self.assertIn("| videos/clip.mp4 | 视频 | 88 B | 视频×1 |", md)
        atts = sorted(p.relative_to(note.parent / "attachments")
                      for p in (note.parent / "attachments").rglob("*") if p.is_file())
        self.assertEqual([str(a) for a in atts],
                         ["images/photo1.png", "images/photo2.png", "videos/clip.mp4"])

    def test_land_nonpadded_datetime(self):
        """月/日/时/分不补零的真实日期行应解析为零填充的标准日期。"""
        run("land", ZIP_B, "--vault", self.vault, "--group", "测试二群")
        note = find_notes(self.vault)[0]
        self.assertEqual(note.parents[2].name, "测试二群")
        self.assertEqual(note.parents[1].name, "2026-03-05")
        self.assertTrue(note.parent.name.startswith("batch-080100"))
        md = note.read_text(encoding="utf-8")
        self.assertIn("date: 2026-03-05", md)
        self.assertIn('time: "08:01:00"', md)
        self.assertIn("message_count: 3", md)

    def test_land_unnamed_fallback(self):
        run("land", ZIP_B, "--vault", self.vault)
        self.assertEqual(find_notes(self.vault)[0].parents[2].name, "未命名群")

    def test_land_crossday_multiline_types(self):
        """跨天批次归首条消息日期；多行正文完整保留；类型前缀计入 TXT 声明。"""
        run("land", ZIP_C, "--vault", self.vault)
        note = find_notes(self.vault)[0]
        self.assertEqual(note.parents[2].name, "未命名群")
        self.assertEqual(note.parents[1].name, "2026-10-14")
        self.assertTrue(note.parent.name.startswith("batch-235800"))
        md = note.read_text(encoding="utf-8")
        self.assertIn("message_count: 5", md)
        self.assertIn("第二行：核心观点是三句话\n第三行：明天继续补", md)
        self.assertIn("2026年10月15日 0:05:33", md)  # 带秒时间原文保留
        self.assertIn("路人甲", md)                   # 无 · 昵称防御
        self.assertIn("| images/night.png | 图片 | 69 B | 图片×1 |", md)

    def test_land_multi_zip(self):
        out = run("land", ZIP_A, ZIP_B, "--vault", self.vault, "--group", "测试二群").stdout
        self.assertEqual(len(find_notes(self.vault)), 2)
        self.assertIn("群名来源: TXT 推断", out)
        self.assertIn("群名来源: --group 参数", out)

    def test_land_bad_zip(self):
        self.assertNotEqual(run("land", FIX, "--vault", self.vault, check=False).returncode, 0)


class DigestIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        run("land", ZIP_A, ZIP_B, "--vault", self.vault, "--group", "测试二群")

    def tearDown(self):
        self.tmp.cleanup()

    def digest_md(self, date):
        return (self.vault / "日报" / f"{date}.md").read_text(encoding="utf-8")

    def test_digest_skeleton_and_heuristics(self):
        run("digest", self.vault, "--date", "2026-10-14")
        md = self.digest_md("2026-10-14")
        for kw in ("一、重要通知", "二、技能与工具分享", "三、实战案例与数据",
                   "四、有价值链接", "五、问答精华", "六、成员动态",
                   "原始消息摘录", "批次清单", "当日统计", "摘要由 AI 补写"):
            self.assertIn(kw, md)
        self.assertIn("【通知】本周六晚 8 点线上分享", section(md, "重要通知"))
        self.assertIn("https://example.com/precipitation", section(md, "有价值链接"))
        self.assertIn("转化率 8%", section(md, "实战案例与数据"))
        self.assertIn("obsidian-git", section(md, "技能与工具分享"))
        self.assertIn("规则未命中", section(md, "成员动态"))
        self.assertIn("小满 09:15：", md)
        self.assertIn("消息总数：8", md)
        self.assertIn("附件总数：3", md)
        self.assertIn("AI实战派/2026-10-14/batch-091200/note.md", md)

    def test_digest_at_lines_to_qa(self):
        run("digest", self.vault, "--date", "2026-03-05")
        md = self.digest_md("2026-03-05")
        self.assertIn("@大熊 牛，怎么做到的？", section(md, "问答精华"))
        self.assertIn("https://example.com/case", section(md, "有价值链接"))
        self.assertIn("测试二群", md)

    def test_digest_crossday_batch(self):
        """跨天批次（C）按首条消息日期聚合；[链接] 行归链接栏。"""
        run("land", ZIP_C, "--vault", self.vault)
        run("digest", self.vault, "--date", "2026-10-14")
        md = self.digest_md("2026-10-14")
        self.assertIn("批次：2", md)
        self.assertIn("https://example.com/night", section(md, "有价值链接"))
        self.assertIn("消息总数：13", md)

    def test_digest_empty_date(self):
        out = run("digest", self.vault, "--date", "2001-01-01").stdout
        self.assertIn("没有批次", out)

    def test_index(self):
        run("digest", self.vault, "--date", "2026-10-14")
        run("index", self.vault)
        md = (self.vault / "README.md").read_text(encoding="utf-8")
        for kw in ("忆栈知识库索引", "群列表", "群 × 日期",
                   "AI实战派", "测试二群", "2026-10-14", "2026-03-05",
                   "[日报](日报/2026-10-14.md)", "日报一览"):
            self.assertIn(kw, md)
        # 2026-03-05 没跑过 digest，群 × 日期表里日报栏应为占位符
        row_b = next(l for l in md.splitlines() if l.startswith("| 测试二群 | 2026-03-05"))
        self.assertTrue(row_b.rstrip().endswith("| — |"))

    def test_index_idempotent(self):
        run("index", self.vault)
        first = (self.vault / "README.md").read_text(encoding="utf-8")
        run("index", self.vault)
        self.assertEqual(first, (self.vault / "README.md").read_text(encoding="utf-8"))


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        # 数据源 A：srcA/<群名>/*.zip；知识库就住在 srcA 里（测 vault 自我排除）
        self.srcA = t / "群情报"
        self.vault = self.srcA / "知识库"
        (self.srcA / "群甲").mkdir(parents=True)
        shutil.copy(ZIP_A, self.srcA / "群甲" / "fixture-a.zip")
        # 数据源 B：Inbox 结构 <uuid>/items/<uuid>/*.zip
        self.srcB = t / "Inbox"
        itemdir = self.srcB / "B5D924BD" / "items" / "9CD0AA89"
        itemdir.mkdir(parents=True)
        shutil.copy(ZIP_C, itemdir / "Zip归档.zip")
        yz = self.vault / ".yizhan"
        yz.mkdir(parents=True)
        (yz / "sources.json").write_text(json.dumps({"sources": [
            {"name": "测试A", "type": "dir_groups", "path": str(self.srcA)},
            {"name": "测试B", "type": "inbox", "path": str(self.srcB)},
        ]}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sync_creates_default_config(self):
        vault = Path(self.tmp.name) / "全新库"
        fake_home = Path(self.tmp.name) / "fakehome"
        out = run("sync", "--vault", vault, env={"HOME": str(fake_home)}).stdout
        cfg = vault / ".yizhan" / "sources.json"
        self.assertTrue(cfg.exists())
        data = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual([s["type"] for s in data["sources"]], ["dir_groups", "inbox"])
        self.assertIn("~/群情报", data["sources"][0]["path"])
        self.assertIn("已创建默认数据源配置", out)
        self.assertIn("新落库 0 批", out)

    def test_sync_end_to_end(self):
        out = run("sync", "--vault", self.vault).stdout
        self.assertIn("新落库 2 批 · 跳过 0 批", out)
        self.assertIn("受影响日期（已生成日报骨架）: 2026-10-14", out)
        self.assertIn("未认领 1 批", out)
        # 数据源 A：目录名优先于 TXT 标题行推断
        self.assertTrue((self.vault / "群甲" / "2026-10-14" / "batch-091200" / "note.md").exists())
        self.assertFalse((self.vault / "AI实战派").exists())
        # 数据源 B：无群名线索 → 未认领
        self.assertTrue((self.vault / "未认领" / "2026-10-14" / "batch-235800" / "note.md").exists())
        # 只对受影响日期生成日报 + 重建索引
        digest = (self.vault / "日报" / "2026-10-14.md").read_text(encoding="utf-8")
        self.assertIn("群甲", digest)
        self.assertIn("未认领", digest)
        readme = (self.vault / "README.md").read_text(encoding="utf-8")
        self.assertIn("群甲", readme)
        self.assertIn("未认领", readme)
        landed = json.loads((self.vault / ".yizhan" / "landed.json").read_text(encoding="utf-8"))
        self.assertEqual(len(landed), 2)
        rec = next(iter(landed.values()))
        self.assertIn("landed_at", rec)
        self.assertIn("batch", rec)

    def test_sync_idempotent(self):
        run("sync", "--vault", self.vault)
        before = sorted(find_notes(self.vault))
        out = run("sync", "--vault", self.vault).stdout
        self.assertIn("新落库 0 批 · 跳过 2 批", out)
        self.assertNotIn("受影响日期", out)
        self.assertEqual(before, sorted(find_notes(self.vault)))

    def test_sync_digest_only_affected_dates(self):
        # 先手动落一个 2026-03-05 的批次，sync 只应生成受影响日期的日报
        run("land", ZIP_B, "--vault", self.vault, "--group", "测试二群")
        shutil.move(str(self.srcB / "B5D924BD" / "items" / "9CD0AA89" / "Zip归档.zip"),
                    str(Path(self.tmp.name) / "moved-away.zip"))
        run("sync", "--vault", self.vault)
        self.assertTrue((self.vault / "日报" / "2026-10-14.md").exists())
        self.assertFalse((self.vault / "日报" / "2026-03-05.md").exists())


class TemplatePendingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        run("land", ZIP_A, ZIP_B, "--vault", self.vault, "--group", "测试二群")

    def tearDown(self):
        self.tmp.cleanup()

    def digest_md(self, date):
        return (self.vault / "日报" / f"{date}.md").read_text(encoding="utf-8")

    def test_template_explicit_builtin(self):
        run("digest", self.vault, "--date", "2026-10-14", "--template", "投研")
        md = self.digest_md("2026-10-14")
        for kw in ("一、标的异动", "二、研报与链接", "三、情绪与信号", "四、问答",
                   "五、成员动态", "六、待确认", "栏目模板：投研"):
            self.assertIn(kw, md)
        self.assertIn("https://example.com/precipitation", section(md, "研报与链接"))
        self.assertNotIn("重要通知", md)

    def test_template_group_mapping(self):
        run("land", ZIP_C, "--vault", self.vault, "--group", "测试二群")
        yz = self.vault / ".yizhan"
        yz.mkdir(exist_ok=True)
        (yz / "groups.json").write_text(json.dumps(
            {"测试二群": "投研"}, ensure_ascii=False), encoding="utf-8")
        run("digest", self.vault, "--date", "2026-10-14")
        md = self.digest_md("2026-10-14")
        self.assertIn("栏目模板：AI实战派=学习群 · 测试二群=投研", md)
        self.assertIn("https://example.com/precipitation", section(md, "有价值链接"))
        self.assertIn("https://example.com/night", section(md, "研报与链接"))

    def test_template_custom_file(self):
        tdir = self.vault / ".yizhan" / "templates"
        tdir.mkdir(parents=True)
        (tdir / "极简.json").write_text(json.dumps(
            {"sections": [{"name": "全部", "patterns": ["."]}]}, ensure_ascii=False),
            encoding="utf-8")
        run("digest", self.vault, "--date", "2026-10-14", "--template", "极简")
        md = self.digest_md("2026-10-14")
        self.assertIn("一、全部", md)
        self.assertIn("二、待确认", md)
        self.assertIn("大家早上好", section(md, "全部"))
        # 规则全命中时，待确认只剩 @ 候选
        self.assertIn("@阿乔", section(md, "待确认"))
        self.assertNotIn("大家早上好", section(md, "待确认"))

    def test_template_unknown(self):
        r = run("digest", self.vault, "--date", "2026-10-14", "--template", "不存在",
                check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("未知模板", r.stderr)

    def test_pending_section(self):
        run("digest", self.vault, "--date", "2026-10-14")
        md = self.digest_md("2026-10-14")
        self.assertIn("七、待确认", md)
        pend = section(md, "待确认")
        self.assertIn("大家早上好，今天开始跑新的日报流水线", pend)   # 规则未命中
        self.assertIn("@阿乔 日报里的工具清单能展开讲讲吗？", pend)      # @ 候选
        # 已有栏目命中逻辑不变：@ 行仍在工具栏
        self.assertIn("@阿乔 日报里的工具清单能展开讲讲吗？", section(md, "技能与工具分享"))

    def test_pending_cap(self):
        txt = "\n\n".join(f"·小测\n2026年10月16日 9:{i:02d}\n随便说点啥 {i} 号"
                          for i in range(25))
        zp = Path(self.tmp.name) / "noise.zip"
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("聊天记录.txt", txt.encode("utf-8"))
        run("land", zp, "--vault", self.vault, "--group", "噪音群")
        run("digest", self.vault, "--date", "2026-10-16")
        pend = section(self.digest_md("2026-10-16"), "待确认")
        bullets = [l for l in pend.splitlines()
                   if l.startswith("- ") and not l.startswith(("- （", "- …"))]
        self.assertEqual(len(bullets), 20)
        self.assertIn("另有 5 条未列出（噪音群/batch-090000）", pend)


class LearnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        run("land", ZIP_A, "--vault", self.vault)
        run("digest", self.vault, "--date", "2026-10-14")
        self.dp = self.vault / "日报" / "2026-10-14.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_learn_move_and_ignore(self):
        bullet_tool = "- 周舟 11:05：推荐一个开源工具：obsidian-git，自动备份笔记"
        bullet_notice = "- 老白 10:02：【通知】本周六晚 8 点线上分享，请大家准时参加"
        case_line = "- 阿乔 10:25：实测新剪辑工具，3 条视频跑出 12 个咨询，转化率 8%"
        md = self.dp.read_text(encoding="utf-8")
        self.assertIn(bullet_tool, md)
        md = md.replace(bullet_tool + "\n", "")
        md = md.replace(case_line, case_line + "\n" + bullet_tool)  # 挪到案例栏
        md = md.replace(bullet_notice + "\n", "")                   # 删掉噪音
        self.dp.write_text(md, encoding="utf-8")

        out = run("learn", self.vault, "--date", "2026-10-14").stdout
        self.assertIn("移动 1 条 · 忽略 1 条", out)
        cfg = json.loads((self.vault / ".yizhan" / "groups.json").read_text(encoding="utf-8"))
        ov = cfg["AI实战派"]["overrides"]
        self.assertEqual(ov["推荐一个开源工具：obsidian-git，自动备份笔记"], "实战案例与数据")
        self.assertEqual(ov["【通知】本周六晚 8 点线上分享，请大家准时参加"], "忽略")
        self.assertEqual(cfg["AI实战派"]["template"], "学习群")

        run("digest", self.vault, "--date", "2026-10-14")
        md2 = self.dp.read_text(encoding="utf-8")
        self.assertIn("obsidian-git", section(md2, "实战案例与数据"))
        self.assertNotIn("obsidian-git", section(md2, "技能与工具分享"))
        self.assertNotIn("【通知】", md2)

    def test_learn_idempotent(self):
        md = self.dp.read_text(encoding="utf-8")
        bullet = "- 老白 10:02：【通知】本周六晚 8 点线上分享，请大家准时参加"
        self.dp.write_text(md.replace(bullet + "\n", ""), encoding="utf-8")
        run("learn", self.vault, "--date", "2026-10-14")
        cfg_p = self.vault / ".yizhan" / "groups.json"
        first = cfg_p.read_text(encoding="utf-8")
        out = run("learn", self.vault, "--date", "2026-10-14").stdout
        self.assertIn("无新增修正", out)
        self.assertEqual(first, cfg_p.read_text(encoding="utf-8"))

    def test_learn_missing_digest(self):
        r = run("learn", self.vault, "--date", "2026-01-01", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("日报不存在", r.stderr)


if __name__ == "__main__":
    unittest.main()
