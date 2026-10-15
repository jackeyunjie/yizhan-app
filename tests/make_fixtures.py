#!/usr/bin/env python3
"""生成合成测试夹具 ZIP（确定性：固定条目时间戳与内容，重复生成字节一致）。"""

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

TXT_A = """群聊名称：AI实战派
导出时间：2026-10-15 08:30:00

乔木 2026-10-14 09:12:03
大家早上好，今天开始跑新的日报流水线

向明 2026-10-14 09:15:40
这篇文章讲知识库沉淀很透 https://example.com/precipitation

王老师 2026-10-14 10:02:11
【通知】本周六晚 8 点线上分享，请大家准时参加

阿May 2026-10-14 10:20:56
@乔木 日报里的工具清单能展开讲讲吗？

乔木 2026-10-14 10:25:02
实测新剪辑工具，3 条视频跑出 12 个咨询，转化率 8%

老周 2026-10-14 11:05:47
推荐一个开源工具：obsidian-git，自动备份笔记
"""

TXT_B = """小李 2026-10-15 08:01:22
早，签到

大熊 2026-10-15 08:20:10
昨天那个案例的数据出来了，转化率 15%，详见 https://example.com/case

小李 2026-10-15 08:25:33
@大熊 牛，怎么做到的？
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
    for z in sorted(FIX.glob("*.zip")):
        print(f"生成 {z.name} · {z.stat().st_size} B")


if __name__ == "__main__":
    main()
