# ai-daily-sources

每天 08:02（北京时间）抓 4 个现成 AI 日报站，把正文里的图片下载到本仓库当图床，
输出一份标准化 JSON 给 n8n 直接读。**纯 stdlib，无依赖。**

## 数据源

| name | 站点 | 入口 |
|---|---|---|
| juya | daily.juya.uk AI 早报 | `rss.xml` |
| ainewslog | ainewslog.com AI 日报 | `rss.xml` + 回源取全文 |
| hex2077 | hex2077.dev 深度信号 | `rss-zh-CN.xml` |
| dailydawn | dailydawn.dev | `zh.json` (JSON Feed) |

> news.aibase.cn 未接入：纯客户端渲染的 Nuxt 站，无 RSS/API，抓下来是空壳。

## 给 n8n 的接口

```
GET https://raw.githubusercontent.com/siweimidu/ai-daily-sources/main/sources.json
```

```jsonc
{
  "date": "2026-10-13",
  "count": 6,
  "failures": [],            // 某站今天挂了会在这里列出，不阻塞其他站
  "articles": [
    {
      "source": "juya",
      "source_label": "Juya AI 早报",
      "title": "2026-10-13",
      "link": "https://daily.juya.uk/issues/2026-10-13/",
      "published_at": "2026-10-13T09:48:17+08:00",
      "summary": "…",
      "content_html": "…<img src=\"https://raw.githubusercontent.com/.../img/2026-10-13/xx.png\">…",
      "plain_text": "…",     // 去标签正文，喂给 LLM 用这个最省 token
      "images": ["…"],       // 全部已镜像到本仓库
      "cover": "…"           // 首图，适合当 Telegram 封面
    }
  ]
}
```

镜像失败时保留原始 URL，所以某张图下载不了不会让日报出现空白。

## 手动跑一次

Actions → Collect AI Sources → Run workflow → `lookback_hours` 填 `72`（补抓周末/漏跑）。

## 保留策略

`img/<日期>/` 超过 14 天自动删除；单图上限 2MB，每条最多 8 张，每次最多 120 张。
约 2.5 MB/天，仓库长期稳定在 40 MB 左右。
