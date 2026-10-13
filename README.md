# AI 日报 · 信息源采集站

**在线日报：https://siweimidu.github.io/ai-daily-sources/**

这个仓库每天自动抓取 4 个现成 AI 日报站的内容，把正文里的图片下载到自己的仓库当图床，
然后产出两样东西：

1. **一个能直接打开看的网页**（上面的链接）——有配图、能翻历史、每条都能点回原文；
2. **一份标准化 JSON**（`sources.json`）——给 n8n 当输入，做二次汇总。

纯 Python 标准库，**零第三方依赖**，全部跑在 GitHub Actions 免费额度上，不需要服务器。

---

## 一、它在整套系统里的位置

每天早上三个任务接力，前两个就在 GitHub 上，第三个在你本机：

| 时间（北京） | 在哪跑 | 干什么 | 产出 |
|---|---|---|---|
| **08:02** | 本仓库 Actions | 抓 4 站 + 镜像图片 | `sources.json`、`archive/日期.json`、`img/日期/*` |
| **08:11** | [daily-briefing](https://github.com/siweimidu/daily-briefing) Actions | Groq+Tavily 检索精选 5 条 | `data/briefing.json` |
| **08:25** | 本机 n8n | 读上面两份 JSON → LLM 汇总 | Telegram 封面图+正文、QQ 邮箱 HTML 日报 |

网页本身**不需要每天改**：它是一份固定静态文件，运行时去读当天的 `sources.json`。
所以采集任务只写数据，页面永远不用重新部署。

---

## 二、数据接口（给 n8n / 其他程序用）

```
GET https://raw.githubusercontent.com/siweimidu/ai-daily-sources/main/sources.json
GET https://raw.githubusercontent.com/siweimidu/ai-daily-sources/main/archive/index.json
GET https://raw.githubusercontent.com/siweimidu/ai-daily-sources/main/archive/2026-10-13.json
```

三个地址都带 `Access-Control-Allow-Origin: *`，浏览器里可以直接 fetch。

```jsonc
{
  "date": "2026-10-13",              // 本期日期（北京时区）
  "generated_at": "2026-10-13T08:02:11+08:00",
  "count": 6,                        // 去重后条目数
  "images_mirrored": 21,             // 本次下载了多少张图
  "sources": [                       // 每个源收到几条，0 表示当天该站没更新
    { "name": "juya", "label": "Juya AI 早报", "feed": "…", "articles": 2 }
  ],
  "failures": [],                    // 抓取失败的源（不阻塞其他源）
  "articles": [
    {
      "source": "juya",
      "source_label": "Juya AI 早报",
      "title": "2026-10-13",
      "link": "https://daily.juya.uk/issues/2026-10-13/",
      "published_at": "2026-10-13T09:48:17+08:00",
      "summary": "…",                // 站点给的摘要，可能很短
      "plain_text": "…",             // 去标签正文，最长 6000 字，喂 LLM 用这个最省 token
      "content_html": "…",           // 完整正文，<img> 已替换成自己仓库的地址
      "images": ["…"],               // 全部已镜像；镜像失败的保留原站地址，不会空白
      "cover": "…"                   // 首图，适合当 Telegram 封面
    }
  ]
}
```

`archive/index.json` 是按日期倒序的轻量列表：

```json
[{ "date": "2026-10-13", "count": 6, "images": 21 }]
```

---

## 三、接入的 4 个源

| name | 站点 | 入口 | 备注 |
|---|---|---|---|
| `juya` | daily.juya.uk | `rss.xml` | 每天 1 期，正文 12 张图 |
| `ainewslog` | ainewslog.com | `rss.xml` | RSS 只给一行摘要，脚本会**回源抓正文**；无图 |
| `hex2077` | hex2077.dev | `rss-zh-CN.xml` | 该站 RSS 只有深度信号**周报**，没有日报 |
| `dailydawn` | dailydawn.dev | `zh.json` | JSON Feed，另有 `feed.xml` 可选 |

> **news.aibase.cn 没接入**：它是纯客户端渲染的 Nuxt 站，没有 RSS 也没有公开 API，
> 抓下来是空壳。要接只能自建 RSSHub。

### 加一个新源

编辑 `scripts/collect.py` 里的 `SOURCES`：

```python
{
    "name": "myfeed",              # 短 id，用于去重和统计
    "label": "我的信息源",          # 网页上显示的名字
    "type": "rss",                 # "rss" 或 "jsonfeed"
    "url": "https://example.com/rss.xml",
    "max_items": 2,                # 每期最多取几条
    "window_hours": 48,            # 多少小时内算"新"；窗口内没有就退化为取最新几条
    "title_include": r"[\u4e00-\u9fff]",  # 可选：只保留标题匹配此正则的条目
    "fulltext": True,              # 可选：RSS 是摘要时，回源抓 <article> 正文
    "og_image": "https://example.com/og/{date}.png",  # 可选：正文无图时用这个封面
}
```

推上去会自动触发一次采集（workflow 监听 `scripts/collect.py` 的改动），不用等第二天。

---

## 四、日常操作

| 想做什么 | 怎么做 |
|---|---|
| 手动补跑一次 | **Actions → Collect AI Sources → Run workflow**，`lookback_hours` 填 `72` 可补抓漏掉的日期 |
| 看某天的原始数据 | 打开 `archive/2026-10-13.json` |
| 改网页样式 | 编辑 `index.html`（CSS 全在文件里，字体和配色在 `:root` 变量里），推上去 Pages 自动重新发布 |
| 改网页上的源标签 | 改 `SOURCES[].label`，第二天生效；或手动跑一次 |
| 查某次运行为什么失败 | Actions 里点进那次 run，看 `Run collector` 步骤的输出 |

Pages 配置：**Settings → Pages → Source = Deploy from a branch (main / root)**。已经配好，不用动。

---

## 五、限制与成本

- **图片保留 30 天**（`RETENTION_DAYS` 环境变量可改），归档同样 30 天，两者对齐——
  否则历史日期的网页会引用到已删除的图。
- 单图上限 2MB（超限保留原站链接，不下载）、每条最多 8 张、每次最多 120 张。
- 实测约 **2.5 MB / 天**，仓库长期稳定在 80 MB 左右，远低于 GitHub 单仓库建议上限。
- 免费额度：GitHub Actions 公开仓库不限时；Pages 免费。零花费。

## 六、本地跑（可选）

只需要 Python 3.10+，不用装任何包：

```bash
git clone https://github.com/siweimidu/ai-daily-sources.git
cd ai-daily-sources
python scripts/collect.py
python -m http.server 8899     # 打开 http://127.0.0.1:8899/index.html
```

本地跑时图片链接会指向 `REPO`/`BRANCH` 环境变量拼出的 raw 地址（默认
`siweimidu/ai-daily-sources/main`），所以本地图要提交后才显示。

## 许可证

MIT
