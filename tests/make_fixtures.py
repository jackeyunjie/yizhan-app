#!/usr/bin/env python3
"""生成合成测试夹具 ZIP（确定性：固定条目时间戳与内容，重复生成字节一致）。

TXT 为微信 macOS 合并转发真实导出格式：
    ·昵称
    2026年10月14日 9:12
    [链接] 正文……
昵称为合成假名，不含真实用户。"""

import struct
import zipfile
import zlib
from pathlib import Path

FIX = Path(__file__).resolve().parent / "fixtures"


def png_1px(rgb):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00" + bytes(rgb)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64

# 群名线索走标题行（GROUP_RES「的聊天记录」）；消息头两行：·昵称 + 不补零的中文日期
TXT_A = """AI实战派的聊天记录

·阿乔
2026年10月14日 9:12
大家早上好，今天开始跑新的日报流水线

·小满
2026年10月14日 9:15
[链接] 这篇文章讲知识库沉淀很透 https://example.com/precipitation

·老白
2026年10月14日 10:02
【通知】本周六晚 8 点线上分享，请大家准时参加

·阿麦
2026年10月14日 10:20
@阿乔 日报里的工具清单能展开讲讲吗？

·阿麦
2026年10月14日 10:22
[图片]

·阿乔
2026年10月14日 10:25
实测新剪辑工具，3 条视频跑出 12 个咨询，转化率 8%

·阿乔
2026年10月14日 10:28
[视频]

·周舟
2026年10月14日 11:05
推荐一个开源工具：obsidian-git，自动备份笔记
"""

# 无群名线索；月/日/时/分全部不补零
TXT_B = """·小黎
2026年3月5日 8:01
早，签到

·大熊
2026年3月5日 8:20
[链接] 昨天那个案例的数据出来了，转化率 15%，详见 https://example.com/case

·小黎
2026年3月5日 8:25
@大熊 牛，怎么做到的？
"""

# 跨天 + 多行正文 + 多类型 + 带秒时间 + 无 · 昵称（防御）+ 昵称含空格
TXT_C = """·夜猫子
2026年10月14日 23:58
今天复盘写到一半，先发个预览
第二行：核心观点是三句话
第三行：明天继续补

·阿 乔
2026年10月15日 0:03
[图片]

·夜猫子
2026年10月15日 0:05:33
[链接] 睡前读物 https://example.com/night

·阿 乔
2026年10月15日 0:07
收到，明早看

路人甲
2026年10月15日 0:10
围观
"""


def write_zip(path, entries):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 15, 8, 30, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)


def main():
    FIX.mkdir(exist_ok=True)
    write_zip(FIX / "fixture-a.zip", [
        ("聊天记录.txt", TXT_A.encode("utf-8")),
        ("images/photo1.png", png_1px((0x1F, 0x3A, 0x4D))),
        ("images/photo2.png", png_1px((0xC0, 0x48, 0x51))),
        ("videos/clip.mp4", FAKE_MP4),
    ])
    write_zip(FIX / "fixture-b.zip", [
        ("chat.txt", TXT_B.encode("utf-8")),
        ("p1.png", png_1px((0x11, 0x22, 0x33))),
        ("p2.png", png_1px((0x44, 0x55, 0x66))),
        ("v1.mp4", FAKE_MP4),
    ])
    write_zip(FIX / "fixture-c.zip", [
        ("聊天记录.txt", TXT_C.encode("utf-8")),
        ("images/night.png", png_1px((0xAA, 0xBB, 0xCC))),
    ])
    for z in sorted(FIX.glob("*.zip")):
        print(f"生成 {z.name} · {z.stat().st_size} B")


if __name__ == "__main__":
    main()
