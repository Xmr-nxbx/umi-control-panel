# -*- coding: utf-8 -*-
"""前端静态自检：JS 拿的 DOM id、导航分区、CSS 变量都得真的存在。

    runtime\\python.exe tests\\test_web_ids.py

新界面没上框架，全靠 id 取元素；漏一个 id 就是运行时 null 崩溃，
而 Python 测试和 curl 都看不出来。这里做三件事：
  * index.html 里没有重复 id；
  * app.js 里每个字面量 $('x') 都能在 index.html 找到（动态生成的列白名单）；
  * 侧边栏 data-view / data-goto 与 JS 的 VIEW_META 一一对上，每个分区都有 view 容器；
  * style.css 里用到的 var(--x) 都在 :root 定义过。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(ROOT, 'app', 'web', 'index.html')
JS_PATH = os.path.join(ROOT, 'app', 'web', 'app.js')
CSS_PATH = os.path.join(ROOT, 'app', 'web', 'style.css')

# 运行期由 createElement 生成、id 是拼出来的；innerHTML 里的 id="x" 会自动从 app.js 扫到
DYNAMIC_IDS = {
    'light-color-0', 'light-color-1', 'light-color-2', 'light-color-3',
    'light-color-4', 'light-color-5', 'light-color-6',
}


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def html_ids(html):
    return re.findall(r'\bid="([^"]+)"', html)


def js_id_refs(js):
    # 只看字面量；$('light-color-' + i) 这种运行期拼的不算
    return set(re.findall(r"\$\('([^']+)'\)", js))


def view_meta_keys(js):
    m = re.search(r'const VIEW_META = \{(.*?)\n\};', js, re.S)
    assert m, 'app.js 里找不到 VIEW_META'
    return set(re.findall(r'(\w+)\s*:\s*\[', m.group(1)))


def nav_values(html, attr):
    block = re.search(r'<nav class="side-nav".*?</nav>', html, re.S)
    scope = block.group(0) if block else html
    return set(re.findall(r'data-%s="([^"]+)"' % attr, scope))


def css_vars(css):
    defined = set(re.findall(r'(--[\w-]+)\s*:', css))
    used = set(re.findall(r'var\((--[\w-]+)', css))
    return defined, used


def check():
    html = read(HTML_PATH)
    js = read(JS_PATH)
    css = read(CSS_PATH)

    ids = html_ids(html)
    dup = sorted({i for i in ids if ids.count(i) > 1})
    assert not dup, 'index.html 有重复 id：%s' % ', '.join(dup)

    id_set = set(ids)
    refs = js_id_refs(js)
    generated = set(re.findall(r'id="([^"]+)"', js))
    missing = sorted(refs - id_set - generated - DYNAMIC_IDS)
    assert not missing, 'app.js 引用了 index.html 里不存在的 id：%s' % ', '.join(missing)

    # 旧测试里点名要保留的控件（PL 页文案测试也靠它们）
    for required in ('tier-now', 'intents', 'hw-buttons', 'oem-buttons', 'chart-wall',
                     'preset-list', 'btn-pl-apply', 'pl1-range', 'foot-status'):
        assert required in id_set, '缺少关键 id：%s' % required

    views = view_meta_keys(js)
    assert 'overview' in views and 'custom' in views, 'VIEW_META 缺分区：%s' % sorted(views)
    for key in views:
        assert 'view-' + key in id_set, 'VIEW_META 有 %s，但 index.html 没有 view-%s' % (key, key)
    assert nav_values(html, 'view') == views, (
        '侧边栏 data-view 与 VIEW_META 不一致：%s vs %s'
        % (sorted(nav_values(html, 'view')), sorted(views)))
    goto = nav_values(html, 'goto')
    assert goto <= views, 'data-goto 指向不存在的分区：%s' % sorted(goto - views)

    defined, used = css_vars(css)
    assert not (used - defined), 'style.css 用了没定义的变量：%s' % sorted(used - defined)

    print('  ok  %d 个 id，%d 处 JS 取值，%d 个分区，%d 个 CSS 变量'
          % (len(id_set), len(refs), len(views), len(used)))


def main():
    try:
        check()
    except AssertionError as exc:
        print('  FAIL %r' % (exc,))
        return 1
    print('1/1 通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
