<p align="center">
  <img src="brand/png/yizhan-combo-512.png" alt="忆栈 YIZHAN" width="360">
</p>

# 忆栈 YIZHAN · 知识库沉淀引擎

> **每天 1 分钟，群聊变资产。** 聊天记录不沉淀，等于没发生。

忆栈是知识库沉淀大师：把微信群聊记录（经 WeChatBridge 官方转发通道沉淀的 ZIP 归档）和未来的网页剪藏，落地为结构化 Markdown 知识库——批次扫描、标准笔记落库、日报骨架聚合、索引自动维护，一条命令一级。

- **数据源解耦**：输入不绑死任何导出工具，微信流沉淀目录、手工导出的 ZIP 一律可吃
- **输出解耦**：标准 Markdown + YAML frontmatter，Obsidian / 任何笔记工具可读
- **零依赖**：Python 3 标准库实现，clone 即用

主色黛青 `#1F3A4D`，品牌规范见 [brand/YIZHAN-brand-spec.md](brand/YIZHAN-brand-spec.md)。

## 安装

```bash
git clone https://github.com/jackeyunjie/yizhan-app.git
cd yizhan-app
./yizhan --help
```

无需 pip install，只要求 Python 3（macOS 自带）。

## 快速上手（三命令）

```bash
# 1. 扫描沉淀目录：批次 / 时间 / ZIP 内 TXT·图·视频统计 / 消息数 / 群名推断
./yizhan scan ~/WeChatBridge沉淀 --since 2026-10-01

# 2. 落库：解压 ZIP → 标准笔记（frontmatter + 正文全文 + 附件清单）
./yizhan land ~/WeChatBridge沉淀/*.zip --vault ~/知识库 --group 我的群

# 3. 日报骨架：聚合当天所有批次，六栏目原始摘录就位，摘要交给 AI
./yizhan digest ~/知识库 --date 2026-10-14
```

落库结构：

```
知识库/
├── <群名>/<YYYY-MM-DD>/batch-HHMMSS/
│   ├── note.md          # YAML frontmatter + TXT 正文全文 + 附件清单表
│   └── attachments/     # 图片/视频/文件原样归档
└── 日报/<date>.md       # 六栏目骨架 + 批次清单 + 当日统计
```

群名推断顺序：TXT 内容 / ZIP 内结构 → `--group` 参数 → 「未命名群」。

日报六栏目（重要通知 / 技能与工具分享 / 实战案例与数据 / 有价值链接 / 问答精华 / 成员动态）先做规则抽取的「原始消息摘录」——含链接的行进链接栏、含 @ 的进行问答候选。**内容摘要由 AI 完成，引擎只做结构化聚合，不假装做 NLP。**

## 维护索引

```bash
./yizhan index ~/知识库   # 重建 知识库/README.md：群列表 × 日期 × 日报链接
```

## 自动化：sync + launchd 守护

```bash
./yizhan sync --vault ~/群情报/知识库   # 按数据源配置自动发现新 ZIP 并落库
bash scripts/install-daemon.sh        # 装 launchd 守护：每 30 分钟自动 sync
```

`sync` 首次运行会在 `<vault>/.yizhan/` 下创建两个文件：

- `sources.json` — 数据源配置，可编辑增删。默认两路：
  - `dir_groups`：`~/群情报/<群名>/`，一级子目录的目录名直接作群名（推荐：用微信流「沉淀到文件夹」转发）
  - `inbox`：`~/Library/Application Support/WeChatBridge/Inbox/Ready/*/items/*/*.zip`，群名拿不到 → TXT 推断 → 落「未认领/」等认领
- `landed.json` — sha256 去重台账，已落库的 ZIP 永远跳过（幂等，连续跑两次第二次全跳过）

落库后只对受影响日期重跑日报骨架并重建索引。守护日志在 `<vault>/.yizhan/sync.log`，卸载用 `bash scripts/uninstall-daemon.sh`。

## 与 WeChatBridge 的关系

忆栈是独立的沉淀层产品，站在入口层之上建自己的层。入口层（合并转发、Share Extension、双端适配）由 WeChatBridge 完成——**Built on [WeChatBridge](https://github.com/freestylefly/WeChatBridge) (MIT)**。

生态位：忆栈同时是 [AIWORK 开放平台](https://aiwork.opcai.cc) 垂直行业工作台的数据入口层——行业群的群聊情报由忆栈沉淀为知识库，供工作台的行业 skill 消费。

## 设计纪律

- 纯标准库，零依赖，代码风格克制
- 永不碰微信数据库读取/注入——只做「老实人的沉淀工具」
- 引擎只做结构化搬运，AI 负责摘要与蒸馏

## 路线图

- [x] v0.1：`scan` / `land` / `digest` / `index` 四命令
- [x] v0.2：`sync` 自动流水线 + launchd 守护（30 分钟轮询）
- [ ] 日报 / 周报 / 月报三档
- [ ] 网页剪藏入口（Chrome 扩展规划中）
- [ ] 群 × 主题 × 人物三轴索引

## 开发

```bash
python3 tests/make_fixtures.py     # 重建合成测试夹具（确定性）
python3 -m unittest discover -s tests -v
```

## License

[MIT](LICENSE) · Copyright (c) 2026 jackeyunjie
