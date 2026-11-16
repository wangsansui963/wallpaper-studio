# Wallpaper Studio · 个人本地壁纸批量下载工具

> ⚠️ **公开仓库 · 免责与合规声明**
>
> - 本仓库**仅包含自研工具代码**，不含任何第三方浏览器扩展的代码、CRX 安装包、图片或字体，也不含下载得到的壁纸文件。
> - `gallery_spec.json` 中的 API 端点来自静态分析，属于**非官方、非公开契约**，可能随时变更或失效；使用前请自行确认并遵守对应服务的使用条款。
> - 壁纸版权归原作者/原平台所有。本项目仅供个人学习与本地使用，**不得商用、不得再分发图片**。
> - 本项目不实现任何需要登录的端点，不绕过授权校验、签名、DRM 或付费墙。
> - 许可证状态见 [LICENSE.md](LICENSE.md)。

基于对 `infinity-new-tab-pro` 浏览器扩展的**静态逆向分析**重新实现的一款自研小工具。
它不包含任何壁纸图片，也不包含原扩展的任何代码；运行时按解码出的公开只读协议拉取清单并下载图片到你的本机目录。

- 语言：Python 3.9+，**仅标准库**，无需 pip 安装任何依赖
- 平台：Windows / macOS / Linux（GUI 需要系统带 Tkinter）
- 配置文件：`gallery_spec.json`（解码成果，可自行修改端点与来源）

## 目录

| 文件 | 说明 |
|---|---|
| `gallery_spec.json` | 解码出的图库协议：端点、14 个来源、分页模型、URL 变换规则、每日壁纸规则 |
| `wallpaper_studio.py` | 核心引擎 + 命令行（清单拉取、批量下载、断点续传、去重、限速、日志） |
| `gui.py` | 可选的 Tkinter 图形界面 |
| `tests/test_core.py` | 离线验证用例（不发网络请求） |

## 快速开始（Windows，在 `wallpaper-studio` 目录下）

```bash
# 1. 查看解码出的 14 个壁纸来源
python wallpaper_studio.py sources

# 2. 单次只读请求，验证接口连通与分页结构
python wallpaper_studio.py verify

# 3. 只导出清单，不下载任何图片（推荐先做这一步）
python wallpaper_studio.py list --source Unsplash --pages 2 --out ./wallpapers

# 4. 批量下载（默认低并发 + 请求间隔）
python wallpaper_studio.py download --source InfinityLandscape --pages 1 \
    --max-items 50 --out ./wallpapers --workers 4 --delay 0.3

# 5. 复用已保存的清单继续/重试下载
python wallpaper_studio.py download --manifest ./wallpapers/manifest.json \
    --limit 20 --out ./wallpapers

# 6. 下载当日壁纸
python wallpaper_studio.py daily --kind infinity

# 7. 图形界面
python gui.py

# 8. 离线验证用例
python -m unittest discover -s tests -v
```

常用参数：

| 参数 | 含义 |
|---|---|
| `--source` | 来源值，如 `Unsplash`、`InfinityLandscape`、`bing`；`all` = 全库（不传 source） |
| `--pages` | 最多抓取页数，每页 50 条 |
| `--max-items` / `--limit` | 限制条数，避免一次抓全库 |
| `--workers` / `--delay` | 并发数与请求最小间隔（默认 4 / 0.3 秒） |
| `--mode` | `raw` 原图（默认）、`display`、`thumbnail`（后两者用 imageView2 变换） |
| `--dry-run` | 只写清单，不下载 |
| `--out` | 输出目录 |

输出目录会生成：`manifest.json`（清单）、`manifest.jsonl`（逐条下载结果）、`download.log`（日志）、图片文件。
中断后重跑会自动跳过已存在文件，未完成的 `.part` 会用 HTTP Range 续传。

## 解码出的协议摘要

| 项目 | 结论 |
|---|---|
| 列表端点 | `GET https://api.inftab.com/v2/get_wallpaper_list?client=pc&page=0` |
| 分页 | `page` 从 0 起；响应 `data.{list,count,totalPages,nextPage}`；每页 50 条 |
| 全库规模 | 运行时验证为 `count=51632`、`totalPages=1033`（2026-11-14 单次探测） |
| 来源过滤 | `source=Unsplash` → 6915 条；`source=InfinityLandscape` → 184 条 |
| 陷阱 | `source=all` 是**无效值**（返回 0 条）；全库要省略该参数 |
| 排序 | `order=1` 会被改成 `like`，`order=2` 改成 `_id` |
| 单条结构 | `src.rawSrc`（原图 URL）、`_id`、`imgId`、`source`、`dimensions`、`like`、`colors` |
| 图片变换 | `原图?imageView2/2/w/{宽度}/[format/webp/]interlace/1` |
| 每日壁纸 | `https://wallpaper.infinitynewtab.com/InfinityWallpaper/{月}_{日}.jpg` |
| 默认壁纸 | `https://infinitypro-img.infinitynewtab.com/findaphoto/bigLink/default.png` |

完整的证据位置、哈希与推断边界见工作区根目录的 `REA-逆向分析报告.md`。

## 合规与使用边界（重要）

- **仅个人本地使用**，不得对外分发本工具产出的图片包，不得商用。壁纸版权归原作者/原平台所有。
- 只调用**无需鉴权的公开只读端点**。登录、点赞、收藏、上传、用户壁纸库等 `_auth` 端点一律不实现。
- **不绕过**任何授权校验、签名、DRM、付费墙或访问频率限制；不解密私有接口。
- 默认低并发、请求间隔、去重与断点续传，尽量减少对服务端的压力。若服务端条款不允许批量抓取，请立即停止使用。
- 第三方接口随时可能变更；`gallery_spec.json` 中的端点与字段是**静态推断 + 有限运行时验证**的结果，并非官方 API 契约。

## 已知限制

- `daily --kind bing` 需要你自己提供 `--md5`（该值来自浏览器扩展的本地存储）；工具不读取浏览器数据、不伪造该值。
- REA 全量静态分析输出约 199 MB，超出上下文，仅提取了元数据层；深层调用图谱未逐条人工解读（详见报告的「局限与未知」）。
- 分析结果代表「逻辑与行为的重建」，**不是源码还原**，也不等于官方稳定 API。

## 仓库内容边界

本仓库是 `wallpaper-studio/` 的独立发布版本。原始分析工作区中的以下内容**不在本仓库内**：第三方扩展的解包代码与 CRX、REA 生成的约 199 MB 分析图谱、`node_modules/` 依赖、以及下载得到的壁纸图片。分析结论只以 `gallery_spec.json` 的协议配置形式保留。
