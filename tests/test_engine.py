#!/usr/bin/env python3
"""忆栈引擎端到端测试：scan / land / digest / index。"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine" / "yizhan.py"
FIX = Path(__file__).resolve().parent / "fixtures"
ZIP_A = FIX / "fixture-a.zip"   # 群聊名称：AI实战派 · 2026-10-14 · 6 条消息
ZIP_B = FIX / "fixture-b.zip"   # 无群名 · 2026-10-15 · 3 条消息


def run(*args, check=True):
    r = subprocess.run([sys.executable, str(ENGINE), *map(str, args)],
                       capture_output=True, text=True)
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
        self.assertIn("消息×6", out)
        self.assertIn("群「AI实战派」", out)
        self.assertIn("共 2 个批次", out)

    def test_scan_json(self):
        data = json.loads(run("scan", FIX, "--json").stdout)
        self.assertEqual(len(data), 2)
        a = next(e for e in data if e["rel"] == "fixture-a.zip")
        self.assertEqual(a["zip_contents"],
                         {"txt": 1, "images": 2, "videos": 1, "files": 0})
        self.assertEqual(a["messages"], 6)
        self.assertEqual(a["first_date"], "2026-10-14")
        self.assertEqual(a["group"], "AI实战派")

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
        self.assertTrue(note.parent.name.startswith("batch-091203"))
        md = note.read_text(encoding="utf-8")
        for field in ('title: "AI实战派 · 2026-10-14 09:12 聊天批次"',
                      "group: AI实战派", "date: 2026-10-14", 'time: "09:12:03"',
                      "message_count: 6", "txt_count: 1", "images: 2", "videos: 1",
                      "  total: 3", "source: fixture-a.zip"):
            self.assertIn(field, md)
        self.assertIn("大家早上好，今天开始跑新的日报流水线", md)
        self.assertIn("| images/photo1.png | 图片 |", md)
        self.assertIn("| videos/clip.mp4 | 视频 |", md)
        atts = sorted(p.relative_to(note.parent / "attachments")
                      for p in (note.parent / "attachments").rglob("*") if p.is_file())
        self.assertEqual([str(a) for a in atts],
                         ["images/photo1.png", "images/photo2.png", "videos/clip.mp4"])

    def test_land_group_arg_fallback(self):
        run("land", ZIP_B, "--vault", self.vault, "--group", "测试二群")
        notes = find_notes(self.vault)
        self.assertEqual(notes[0].parents[2].name, "测试二群")
        self.assertEqual(notes[0].parents[1].name, "2026-10-15")

    def test_land_unnamed_fallback(self):
        run("land", ZIP_B, "--vault", self.vault)
        self.assertEqual(find_notes(self.vault)[0].parents[2].name, "未命名群")

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
        self.assertIn("消息总数：6", md)
        self.assertIn("附件总数：3", md)
        self.assertIn("AI实战派/2026-10-14/batch-091203/note.md", md)

    def test_digest_at_lines_to_qa(self):
        run("digest", self.vault, "--date", "2026-10-15")
        md = self.digest_md("2026-10-15")
        self.assertIn("@大熊 牛，怎么做到的？", section(md, "问答精华"))
        self.assertIn("https://example.com/case", section(md, "有价值链接"))
        self.assertIn("测试二群", md)

    def test_digest_empty_date(self):
        out = run("digest", self.vault, "--date", "2001-01-01").stdout
        self.assertIn("没有批次", out)

    def test_index(self):
        run("digest", self.vault, "--date", "2026-10-14")
        run("index", self.vault)
        md = (self.vault / "README.md").read_text(encoding="utf-8")
        for kw in ("忆栈知识库索引", "群列表", "群 × 日期",
                   "AI实战派", "测试二群", "2026-10-14", "2026-10-15",
                   "[日报](日报/2026-10-14.md)", "日报一览"):
            self.assertIn(kw, md)
        # 2026-10-15 没跑过 digest，群 × 日期表里日报栏应为占位符
        row_b = next(l for l in md.splitlines() if l.startswith("| 测试二群 | 2026-10-15"))
        self.assertTrue(row_b.rstrip().endswith("| — |"))

    def test_index_idempotent(self):
        run("index", self.vault)
        first = (self.vault / "README.md").read_text(encoding="utf-8")
        run("index", self.vault)
        self.assertEqual(first, (self.vault / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
