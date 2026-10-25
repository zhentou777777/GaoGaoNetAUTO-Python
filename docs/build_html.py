# -*- coding: utf-8 -*-
"""把使用教程 Markdown 转成单文件 HTML（方便在手机上阅读）。

只依赖标准库，不引入 markdown 第三方包。

用法：
    python docs/build_html.py
产物：
    docs/使用教程.html
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 需要生成 HTML 的文档（源文件 → 输出文件）
DOCS = [
    ("使用教程.md", "使用教程.html", "糕糕校园网自动认证 · 使用教程",
     "怎么用：下载、配置、开机自启、断网重连、故障排查"),
    ("技术路径.md", "技术路径.html", "糕糕校园网自动认证 · 技术路径",
     "怎么做到的：接口逆向全过程、协议分析、自愈架构、实现取舍"),
    ("需求说明书.md", "需求说明书.html", "糕糕校园网自动认证 · 需求说明书",
     "要解决什么、要做到什么、做到没有"),
    ("生产交接文档.md", "生产交接文档.html", "糕糕校园网自动认证 · 生产交接文档",
     "怎么用、怎么维护、坏了怎么办"),
    ("Defender-误报排查.md", "Defender-误报排查.html", "糕糕校园网自动认证 · Defender 误报排查",
     "被杀毒软件拦截了怎么办：下载拦截、运行被杀、证书信任、彻底根治"),
    ("../README.md", "README.html", "糕糕校园网自动认证 · 项目说明",
     "项目总览、原理速览、快速开始、FAQ"),
]

CSS = """
:root{
  --bg:#0f1115; --card:#171a21; --card2:#1e222b; --border:#272c37;
  --text:#e6e9ef; --text2:#a8b0c0; --text3:#7b8496;
  --accent:#fb7299; --accent2:#4ade80; --code:#0b0d11;
}
@media (prefers-color-scheme: light){
  :root{
    --bg:#f7f8fa; --card:#ffffff; --card2:#f2f4f7; --border:#e2e6ec;
    --text:#1a1d23; --text2:#4a5262; --text3:#7b8496;
    --accent:#e0517d; --accent2:#16a34a; --code:#f4f6f9;
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--bg); color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei UI",
    "PingFang SC","Hiragino Sans GB",sans-serif;
  font-size:16px; line-height:1.78;
}
.wrap{max-width:820px;margin:0 auto;padding:32px 20px 80px}
h1{
  font-size:30px;line-height:1.3;margin:0 0 8px;font-weight:700;
  letter-spacing:-.02em;
}
.sub{color:var(--text3);font-size:14px;margin:0 0 28px}
h2{
  font-size:22px;margin:44px 0 14px;font-weight:650;
  padding-bottom:10px;border-bottom:1px solid var(--border);
  scroll-margin-top:16px;
}
h3{font-size:17.5px;margin:28px 0 10px;font-weight:600;color:var(--text)}
p{margin:12px 0}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
hr{border:0;border-top:1px solid var(--border);margin:36px 0}
blockquote{
  margin:16px 0;padding:12px 16px;background:var(--card2);
  border-left:3px solid var(--accent);border-radius:0 8px 8px 0;
  color:var(--text2);font-size:15px;
}
blockquote p{margin:6px 0}
ul,ol{padding-left:22px;margin:12px 0}
li{margin:6px 0}
code{
  background:var(--code);border:1px solid var(--border);border-radius:5px;
  padding:1px 6px;font-size:.87em;
  font-family:"Cascadia Mono",Consolas,"SF Mono",Menlo,monospace;
  color:var(--accent);
}
pre{
  background:var(--code);border:1px solid var(--border);border-radius:10px;
  padding:14px 16px;overflow-x:auto;margin:16px 0;
}
pre code{
  background:none;border:0;padding:0;color:var(--text2);font-size:13.5px;
  line-height:1.65;display:block;
}
table{
  width:100%;border-collapse:collapse;margin:16px 0;font-size:14.5px;
  display:block;overflow-x:auto;
}
th,td{
  border:1px solid var(--border);padding:9px 12px;text-align:left;
  vertical-align:top;
}
th{background:var(--card2);font-weight:600;white-space:nowrap}
tr:nth-child(even) td{background:rgba(255,255,255,.015)}
.toc{
  background:var(--card);border:1px solid var(--border);border-radius:12px;
  padding:18px 22px;margin:28px 0;
}
.toc h2{margin:0 0 10px;font-size:15px;border:0;padding:0;color:var(--text3);
  text-transform:uppercase;letter-spacing:.08em;font-weight:600}
.toc ol{margin:0;padding-left:20px;columns:2;column-gap:28px}
.toc li{margin:4px 0;font-size:14.5px;break-inside:avoid}
.toc a{color:var(--text2)}
.toc a:hover{color:var(--accent)}
.anchor{
  margin:0 0 6px;padding:12px 18px;background:var(--card);
  border:1px solid var(--border);border-radius:10px;
}
.anchor b{color:var(--accent2)}
@media (max-width:600px){
  .wrap{padding:22px 16px 60px}
  h1{font-size:24px} h2{font-size:19px} h3{font-size:16.5px}
  .toc ol{columns:1}
  body{font-size:15.5px}
}
"""

_SCRIPT = """
(function(){
  var h=document.querySelector('h1');
  if(!h) return;
  var b=document.createElement('div');
  b.className='anchor';
  b.innerHTML='<b>三十秒速览</b> &nbsp;下载 exe → 双击运行 → <b>先连上校园网</b> → 点「扫码登录」→ 用糕糕 APP 扫一下 → 立刻联网。'+
    ' 想要以后彻底不掏手机，见下方「抓取 Token」一节。';
  h.parentNode.insertBefore(b,h.nextSibling);
  var t=document.querySelector('.toc');
  if(t){ t.scrollIntoView({behavior:'smooth'}); }
})();
"""


def _inline(text: str) -> str:
    """处理行内 Markdown（先保护代码块，再转义，再替换标签）。"""
    spans: list[str] = []

    def stash(m: re.Match) -> str:
        spans.append("<code>" + html.escape(m.group(1)) + "</code>")
        return f"\x00{len(spans) - 1}\x00"

    # 行内代码（先转义内容，再整体转义其余文本）
    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text)
    # 粗体 / 斜体
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    # 链接
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        text,
    )
    # 还原代码（还原时不能再次转义）
    def restore(m: re.Match) -> str:
        return spans[int(m.group(1))]

    text = re.sub(r"\x00(\d+)\x00", restore, text)
    return text


def _slug(text: str) -> str:
    s = re.sub(r"<[^>]+>", "", text)
    s = re.sub(r"[^\w一-鿿]+", "-", s).strip("-").lower()
    return s or "h"


def convert(md: str, title: str = "文档", subtitle: str = "") -> str:
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    in_code = False
    code_buf: list[str] = []
    toc: list[tuple[str, str]] = []

    while i < len(lines):
        line = lines[i]

        # 代码块
        if line.startswith("```"):
            if not in_code:
                in_code = True
                code_buf = []
            else:
                in_code = False
                out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # 表格
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(
            r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]
        ):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append("<table><thead><tr>")
            for h in header:
                out.append(f"<th>{_inline(h)}</th>")
            out.append("</tr></thead><tbody>")
            for r in rows:
                out.append("<tr>")
                for c in r:
                    out.append(f"<td>{_inline(c)}</td>")
                out.append("</tr>")
            out.append("</tbody></table>")
            continue

        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            txt = m.group(2).strip()
            if level == 1:
                out.append(f"<h1>{_inline(txt)}</h1>")
                out.append(f'<p class="sub">{subtitle}</p>')
            elif level == 2:
                anchor = _slug(txt)
                toc.append((txt, anchor))
                out.append(f'<h2 id="{anchor}">{_inline(txt)}</h2>')
            elif level == 3:
                out.append(f"<h3>{_inline(txt)}</h3>")
            else:
                out.append(f"<h4>{_inline(txt)}</h4>")
            i += 1
            continue

        # 引用
        if line.startswith("> "):
            buf: list[str] = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip())
                i += 1
            inner = "\n".join(buf)
            # 引用块内可能含列表
            inner_html = []
            for bl in inner.split("\n"):
                inner_html.append(f"<p>{_inline(bl)}</p>" if bl.strip() else "")
            out.append("<blockquote>" + "".join(inner_html) + "</blockquote>")
            continue

        # 分隔线
        if re.match(r"^-{3,}$", line.strip()):
            out.append("<hr>")
            i += 1
            continue

        # 列表
        if re.match(r"^\s*([-*]|\d+\.)\s+", line):
            ordered = bool(re.match(r"^\s*\d+\.\s+", line))
            items: list[str] = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
            continue

        # 空行
        if not line.strip():
            i += 1
            continue

        # 段落
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^(#{1,4}\s|> |```|\s*([-*]|\d+\.)\s|\|)", lines[i]
        ) and not re.match(r"^-{3,}$", lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append("<p>" + _inline(" ".join(buf)) + "</p>")
        else:
            i += 1

    # 目录
    toc_html = "".join(
        f'<li><a href="#{a}">{t}</a></li>' for t, a in toc if t != "目录"
    )
    toc_block = f'<div class="toc"><h2>目录</h2><ol>{toc_html}</ol></div>'

    body = "\n".join(out)
    # 把目录插到第一个 h2 之前
    idx = body.find("<h2")
    if idx != -1:
        body = body[:idx] + toc_block + body[idx:]

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body>
<div class="wrap">
{body}
</div>
<script>__SCRIPT__</script>
</body>
</html>
""".replace("__TITLE__", title).replace("__CSS__", CSS) \
    .replace("__SCRIPT__", _SCRIPT).replace("{body}", body)


def main() -> int:
    ok = 0
    for src_name, out_name, title, subtitle in DOCS:
        src = HERE / src_name
        out = HERE / out_name
        if not src.exists():
            print(f"✗ 找不到 {src}")
            continue
        md = src.read_text(encoding="utf-8")
        page = convert(md, title=title, subtitle=subtitle)
        out.write_text(page, encoding="utf-8")
        print(f"✓ 已生成 {out}  ({len(page) / 1024:.1f} KB)")
        ok += 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
