#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
InvestBuddy 中国·央行公开市场操作日度采集器（cn_omo_daily）

蓝图的哪一块：**货币流动性 · 中国层「数量维度的短端抓手」**。
  已有的 `cn_liquidity_monthly` 是**月度存量**（M2 有多少），本表是**日度流量**
  （央行今天投了多少）—— 二者合起来才答得了「央行这周在放水还是收水」。

数据源：中国人民银行「公开市场业务」栏目群（零 Key、官网直连）。现采 **2 个栏目**：

  ① `omo_trade` 公开市场业务交易公告（7 天期逆回购，日度；本栏目还混着香港央票）
     https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html
  ② `outright_repo` 公开市场买断式逆回购业务公告（2024-10 起、月度；单次 5000~10000 亿）
     https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/5492845/index.html
  详情页 = `{栏目根}/.../{公告ID}/index.html`

  ⚠️ 两者**各自独立编号**（同一年「交易公告第1号」与「买断式第1号」并存）
     ⇒ 表主键必须是 `(section, notice_year, notice_no)`，否则两个栏目会互相覆盖。
  ⚠️ 同级还有若干栏目**已知未采**：公开市场国债买卖业务公告（2025-01 起已暂停）、
     中央国库现金管理业务公告、中央银行票据业务公告、央行票据互换(CBS)、
     互换便利(SFISF)、其他业务公告(SLO)。按需再补。

⭐ 五条实测纪律（2026-09-25 实测，脚本留 `_scratch/_impl_probe_cn2..10.py`、`_impl_diag_omo*.py`，勿回退）：

1. **翻页的真实形式是 `17081-{n}.html`，不是 `index_{n}.html`**。
   实测 `index_1.html` / `index1.html` / `index_2.html` / `index2.html` **全部 404**；
   而 `?page=N` 返回的是**同一页**（无效）。真实链接藏在分页按钮的 onclick 里：
   `queryArticleByCondition(this,'/zhengcehuobisi/.../17081-2.html')`
   ⇒ 即 `{moduleid}-{page}.html`，moduleid=17081。
   总记录数 3810、每页 20 条、共 **191 页**（实测 190×20+10=3810，第 192 页 404）。

   ⚠️ 且公告 ID **两种形式并存**，列表解析必须同时兼容：
     · 19 位时间戳型（2025-10-09 网站改版后）`2025100917205199877` —— **前 8 位即日期**
     · **7 位纯序号型**（改版前）`5817008`、`5816929` —— **完全不含日期**
   实测第 13~14 页仍是 19 位、**第 15 页起全部变成 7 位**。
   若用 `(\d{8})\d{11}` 这种定长匹配，会从第 15 页起**一条都抓不到**，
   外显症状只是「抓到 14 页就断」、**不报任何错**（本采集器最初就这么错过一次，
   回补出来只有 1 年数据还以为是正常的）。
   ⇒ ID 段一律用 `\d+` 匹配；日期来源改为「详情页正文优先、ID 前 8 位兜底」。
   ⚠️ 翻页 URL 里的 **modulekey 也不一定是数字**：交易公告是 `17081`（数字），
   买断式栏目是 `b0da893b`（UUID）。硬编码数字会让买断式栏目的翻页整个打空。
   ⇒ modulekey 必须从首页的分页链接里**动态解析**（见 `_module_key()`）。

2. **判停只能用「年份+编号」元组 —— 编号单独不行、日期也不行**。
   · 不能用编号单独判停：人行公告**按年重新编号**，跨年页会混合（实测 n=10 同页含
     [2026]第1~14号 与 [2025]第237~255号，打印出来像「第1~255号」）。
   · 不能用日期判停：人行存在**批量补发**（实测 2025-10-09 一个发布日挂了 17 条公告、
     2025-11-14 挂了 27 条）—— 这些公告的**发布日 ≠ 操作日**，按日期判停会误停/误过滤。
   · 表主键也**不能**用 `(trade_date, op_type)`：同一发布日的多份公告 op_type 相同，
     会互相覆盖（实测静默丢 38 条）⇒ 已改主键为 `(notice_year, notice_no)`。
   ⇒ 水位、判停、过滤一律用 `(notice_year, notice_no)` 元组（年内递增，跨年元组比较自然正确）。

3. **零操作日也发公告**，措辞是「N天期逆回购操作量为零」。
   实测前 40 条里零操作占 **14 条（35%）**，量级远高于直觉。
   注意措辞**不是**「未开展」「不开展」——按那两种写法匹配会一条都抓不到。
   ⇒ 零操作日落库为 `win_amount = 0`（不是 NULL），NULL 表示「源侧没给」。

4. **操作利率不在正文、只在公告表格里**，且表格索引不可硬编码。
   实测详情页共 10 张 table，目标是含「中标量 / 期限 / 操作利率」的那张（实测恰好是第 8 张，
   但**不同公告表格数会变**，故一律按**列名**定位，取表头行的下一行）。
   实测目标表：`['7天', '1.40%', '515亿元', '515亿元']`。

5. **官网有 403 反爬限流，必须做跨线程全局节流**。
   实测并发 4、无间隔抓 631 条详情页，中途开始**整批返回 `HTTP 403 Forbidden`**，
   一次跑丢掉 20+ 条公告 —— 而且**不报错、只是少数据**，最难察觉。
   ⇒ 所有请求必须走 `_throttle()` 做全局最小间隔（`_MIN_INTERVAL`，默认 0.35s ≈ 2.9 req/s），
     详情页重试放宽到 `attempts=4, base_delay=3.0`（退避 3/6/12s）。
     代价：首次回补 631 条约 3~4 分钟；日度增量只有几条，感知不到。

另有两条口径事实：
  · 同一栏目下**不止逆回购**：香港央票（离岸回笼，2026-09-23 第188号）、买断式逆回购、
    国库现金定存都发在这里、标题也一模一样，只能靠正文区分 ⇒ 落 `op_type` 供分析层筛选。
    ⚠️ 且**同一发布日可挂多份公告**（2026-09-23 既有第187号逆回购又有第188号香港央票；
    改版补发日更极端，2025-10-09 挂了 17 条）⇒ 主键必须是 `(notice_year, notice_no)`。
  · 央票公告的表格列名是「**发行量**」而非「中标量」，`_find_table` 已一并识别并映射到 `win_amount`。

抓取范围：默认最近 `max_pages=25` 页（约 500 条 / 近 2 年），可调。
增量：先取表内 `MAX(trade_date)` 作水位，逐页抓到「该页最旧日期 ≤ 水位 − 重叠」即止 ——
     日度增量通常**只抓 1 页列表**，然后只对超出水位的少量条目抓详情。

幂等：PRIMARY KEY(trade_date, op_type) + upsert，重跑只补新日期/新类型。
"""
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from html import unescape

import pymysql

from ..db import get_db_config
from ._common import with_steps
from ._http import make_session, get_text

logger = logging.getLogger(__name__)

_BASE = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/"
_HDR = {"Referer": _BASE, "Accept-Language": "zh-CN,zh;q=0.9"}

RUN_STEPS = [
    {"no": 1, "name": "读各栏目水位", "params": "按 section 各取最大 (notice_year, notice_no)；空表则全量回补"},
    {"no": 2, "name": "逐栏目抓列表页", "params": "index.html → {modulekey}-{n}.html（modulekey 动态解析，可能是数字或 UUID）；逐页抓到「页内最小 (年,号) ≤ 该栏目水位」即止"},
    {"no": 3, "name": "抓详情页解析", "params": "并发 4；正文抽日期(三级 fallback)/类型/操作量/期限，表格按列名抽 期限/利率/投标量/中标量"},
    {"no": 4, "name": "按 (栏目,年,号) 过滤", "params": "只留比各级水位新的公告；⚠️ 不可按日期过滤（批量补发日 ≠ 操作日）"},
    {"no": 5, "name": "Upsert", "params": "PRIMARY KEY(section, notice_year, notice_no)，重跑只补新增"},
]

_TOTALPAGE_RE = re.compile(r'totalpage="(\d+)"')
# 翻页链接形如 /xxx/{modulekey}-{n}.html。
# ⚠️⚠️ modulekey **不一定是数字**：交易公告 = `17081`（数字），买断式 = `b0da893b`（UUID）。
# 硬编码成数字会把买断式栏目的翻页整个打空（又是「静默少数据」）。
_PAGE_LINK_RE = re.compile(r'["\']([^"\']*?/([\w\-]{4,})-(\d+)\.html)["\']')

# 采集栏目。⭐ 多栏目并存，且**各自独立编号**（同一年「交易公告第1号」与「买断式第1号」并存）
# ⇒ 共用一张表、但主键必须含 section。
_SECTIONS = [
    {
        "section": "omo_trade",                     # 7 天期逆回购（+ 香港央票），日度
        "name": "公开市场业务交易公告",
        "base": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/",
        "title": r"公开市场业务交易公告",
        "pages": None,                              # None → 用调用方给的 max_pages
    },
    {
        "section": "outright_repo",                 # 买断式逆回购，2024-10 起、月度
        "name": "公开市场买断式逆回购业务公告",
        "base": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/5492845/",
        "title": r"公开市场买断式逆回购招标公告",
        "pages": 3,                                 # 源侧仅 2 页（39 条），抓 3 页足够
    },
]


def _item_re(title_pat: str):
    """按栏目标题构造列表页条目正则。公告 ID 长度不定（19 位/7 位）故用 \\d+"""
    return re.compile(
        r'href=["\']([^"\']*?/(\d+)/index\.html)["\'][^>]*>'
        r'(?:\s|<[^>]+>)*'
        + title_pat + r'\s*\[(\d{4})\]\s*第(\d+)\s*号',
        re.I,
    )


def _module_key(doc: str, total_pages: int | None):
    """从分页链接里抠出 modulekey（可能是数字，也可能是 UUID）"""
    if total_pages:
        m = re.search(r'["\']([^"\']*?/([\w\-]{4,})-' + str(total_pages) + r'\.html)["\']', doc)
        if m:
            return m.group(2)
    for m in _PAGE_LINK_RE.finditer(doc):
        if m.group(3) != "1":
            return m.group(2)
    return None


def _id_date(sid: str):
    """公告 ID → 日期。19 位时间戳型取前 8 位；7 位序号型不含日期 → None"""
    if len(sid) >= 12:
        try:
            return date(int(sid[:4]), int(sid[4:6]), int(sid[6:8]))
        except ValueError:
            return None
    return None

_RE_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
# 常规/买断式：「…开展了515亿元7天期逆回购操作」「…开展了XX亿元买断式逆回购操作」
_RE_OP = re.compile(r"开\s*展\s*了?\s*([\d,]+(?:\.\d+)?)\s*亿元\s*(?:(\d+)\s*天期\s*)?(买断式\s*)?逆回购")
# 零操作：两种措辞并存 ——「2026年8月20日7天期逆回购操作量为零」（新）
# 与「2024年8月7日逆回购操作量为零」（旧，**无「N天期」前缀**，实测 2024-08-07 第155号）。
# 故「天期」设为可选；`(\d+)\s*天期` 捕获组用于回填期限（旧措辞无期限则 tenor 留空）。
_RE_ZERO = re.compile(r"(\d+)\s*天期\s*逆回购\s*操作量为零|逆回购\s*操作量为零")
# 买断式：「…开展5000亿元买断式逆回购操作，期限为6个月（181天），到期日为2027年3月15日」
# ⚠️ 买断式公告**没有操作明细表**，期限只能从正文这句里读
_RE_TENOR_TXT = re.compile(r"期限为\s*([^，,。；;]{1,24})")
# 中文落款日期「二〇二五年三月二十四日」。⚠️ 香港央票公告的**正文不含带年份的阿拉伯日期**
# （只写「本周三（9月23日）」甚至完全不写），文末落款是唯一可用于定日期的字段；
# 漏了它，实测有 7 条央票会 trade_date = NULL
_RE_CN_DATE = re.compile(
    r"([〇零一二三四五六七八九]{2,4})\s*年\s*([〇零一二三四五六七八九十]{1,3})\s*月\s*"
    r"([〇零一二三四五六七八九十]{1,3})\s*日")
_CN_DIGIT = {"〇": "0", "零": "0", "一": "1", "二": "2", "三": "3",
             "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
_RE_TBL = re.compile(r"(?is)<table[^>]*>.*?</table>")
_RE_TR = re.compile(r"(?is)<tr.*?</tr>")
_RE_CELL = re.compile(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>")

_WORKERS = 3
# ⚠️⚠️ 人行官网**有反爬限流**：实测并发 4 无间隔抓 631 条详情页，很快开始整批返回
#     `HTTP 403 Forbidden`（一次跑丢掉 20+ 条公告）。故必须**全局节流**——
#     不管开几个线程，整体请求频率都压在 `1/_MIN_INTERVAL` 次/秒。
#     回补 631 条约需 3~4 分钟，这是必要代价；增量每天只几条，感知不到。
_MIN_INTERVAL = 0.35

_local = threading.local()
_throttle_lock = threading.Lock()
_last_req = [0.0]


def _throttle() -> None:
    """全局最小请求间隔（跨线程共享），用来绕开人行官网的 403 限流"""
    with _throttle_lock:
        wait = _MIN_INTERVAL - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.time()


def _sess():
    """每线程一个绕过环境代理的会话（requests.Session 不保证跨线程安全）"""
    s = getattr(_local, "s", None)
    if s is None:
        s = make_session()
        _local.s = s
    return s


def _plain(doc: str) -> str:
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", doc)
    p = unescape(re.sub(r"(?s)<[^>]+>", " ", body))
    p = re.sub(r"\s+", " ", p.replace("\xa0", " ").replace("\u3000", " "))
    # ⚠️ 必须先剥掉页面公共导航块。它夹在「页面标题」与「正文」之间，且**块内没有任何句号**——
    # 不剥的话，按句号切分后正文会跟导航黏成一整句。
    # ⚠️ 且要**贪婪**匹配到「关于我们」：各栏目页面的导航长度不同（买断式栏目在
    #    「意见征集」之后还多出「金融知识 关于我们」，非贪婪会只剥一半、留下残渣）。
    p = re.sub(r"术语表.*(?:意见征集|金融知识|关于我们)", " ", p, count=1)
    return re.sub(r"\s+", " ", p).strip()


def _cn_num(s: str):
    """中文数字 → int。支持逐字型（二〇二五 → 2025）与十进制型（二十四 → 24、十 → 10）"""
    s = (s or "").strip()
    if not s:
        return None
    if "十" not in s:
        return int("".join(_CN_DIGIT[c] for c in s)) if all(c in _CN_DIGIT for c in s) else None
    a, _, b = s.partition("十")
    try:
        tens = int(_CN_DIGIT.get(a, "1")) if a else 1
        ones = int("".join(_CN_DIGIT.get(c, "") for c in b)) if b else 0
    except ValueError:
        return None
    return tens * 10 + ones


def _cn_date(txt: str):
    """正文里的中文落款日期 → date；找不到返回 None"""
    m = _RE_CN_DATE.search(txt)
    if not m:
        return None
    y, mo, d = _cn_num(m.group(1)), _cn_num(m.group(2)), _cn_num(m.group(3))
    if not (y and mo and d):
        return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _norm(s):
    """删掉全部空白 —— ⚠️ 人行公告的表格单元格/数字常被拆出空格（'操作 量' '1. 40 %' '6 00亿元'），
    任何列名匹配与数值解析前都必须先做这一步，否则大面积静默为空。"""
    return re.sub(r"\s+", "", str(s or ""))


def _f_amt(cell):
    """'515亿元' / '6 00亿元' → 515.0 / 600.0"""
    if not cell:
        return None
    m = re.search(r"([\d,]+(?:\.\d+)?)", _norm(cell).replace(",", ""))
    return round(float(m.group(1)), 4) if m else None


def _f_rate(cell):
    """'1.40%' / '1. 40 %' → 1.4"""
    if not cell:
        return None
    s = _norm(cell)
    m = re.search(r"([\d.]+)%", s) or re.search(r"^([\d.]+)$", s)
    return round(float(m.group(1)), 4) if m else None


def _f_tenor(cell):
    """'7天' → 7 ; '6个月 （182天）' → 182（优先取「天」）; '3个月' → 90（记 30 天，近似）; '1年' → 365"""
    if not cell:
        return None
    s = _norm(cell)
    m = re.search(r"(\d+)天", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)个月", s)
    if m:
        return int(m.group(1)) * 30
    m = re.search(r"(\d+)年", s)
    if m:
        return int(m.group(1)) * 365
    return None


class CnOmoSyncCollector:
    """央行公开市场操作日度同步（人行公开市场业务交易公告）"""

    def __init__(self, timeout_sec: float = 900, max_pages: int = 25, workers: int = _WORKERS,
                 full: bool = False):
        self.timeout_sec = float(timeout_sec)
        self.max_pages = max(1, int(max_pages))
        self.workers = max(1, int(workers))
        # full=True → **忽略水位**、抓满 max_pages 页（首次回补 / 事后扩大历史窗口用）。
        # ⚠️ 必须有这个开关：否则一旦表里有水位，逐页抓取会在第 1 页就判停，
        #    想「把 max_pages 从 25 调到 60 来回补更早历史」会静默无效。
        self.full = bool(full)

    # ---------- 列表页 ----------

    def _list_page(self, sec: dict, n: int, key: str | None
                   ) -> tuple[list[dict], int | None, str | None]:
        """→ (条目列表, 总页数, modulekey)。条目含 href / id_date（7 位 ID 时为 None）/ 年份 / 编号"""
        if n > 1 and not key:
            return [], None, None            # 没能解析出 modulekey → 无法翻页，只能停
        url = sec["base"] + ("index.html" if n == 1 or not key else f"{key}-{n}.html")
        try:
            _throttle()
            doc = get_text(url, timeout=25, attempts=3, base_delay=2.0,
                           encoding="utf-8", headers=_HDR, session=_sess())
        except Exception:
            # 翻过末页会 404（如买断式栏目只有 2 页）。这是正常的边界，不该算错误。
            if n > 1:
                return [], None, key
            raise
        tp = _TOTALPAGE_RE.search(doc)
        total = int(tp.group(1)) if tp else None
        if not key:
            key = _module_key(doc, total)    # ⚠️ 动态解析：可能是数字，也可能是 UUID
        items = []
        for m in _item_re(sec["title"]).finditer(doc):
            href, sid, yr, no = m.group(1), m.group(2), m.group(3), m.group(4)
            items.append({"href": href, "id_date": _id_date(sid),
                          "notice_year": int(yr), "notice_no": int(no),
                          "section": sec["section"]})
        return items, total, key

    # ---------- 详情页 ----------

    @staticmethod
    def _find_table(doc: str):
        """按**列名**定位操作表 → (表头单元格, 数据单元格)。找不到返回 None。
        ⚠️ 不可硬编码表索引：实测不同公告表格数会变（虽然实测目标恰好是第 8 张）。"""
        for t in _RE_TBL.findall(doc):
            rows = []
            for tr in _RE_TR.findall(t):
                cells = [re.sub(r"\s+", " ",
                                unescape(re.sub(r"(?s)<[^>]+>", "", c))).replace("\xa0", " ").strip()
                         for c in _RE_CELL.findall(tr)]
                rows.append(cells)
            for ri, cells in enumerate(rows):
                # ⚠️⚠️ 两个必须做对的地方（实测踩过）：
                #   (1) 列名会被**拆出空格**：「操作 量」「中标 利率」「1. 40 %」——
                #       匹配前必须删掉全部空白，否则一条都定位不到（表现为 tenor 大面积为空）。
                #   (2) 列名**代际不同**：2024 年及更早的公告表头是「操作量 / 操作利率」，
                #       之后才改成「投标量 + 中标量」，只认后者会漏掉一半历史。
                norm = [re.sub(r"\s+", "", c) for c in cells]
                flat = "".join(norm)
                if ("期限" in flat
                        and any(k in flat for k in ("中标量", "操作量", "发行量", "投标量"))
                        and ri + 1 < len(rows)):
                    data = rows[ri + 1]
                    if data and any(x for x in data):
                        return norm, data
        return None

    def _detail(self, x: dict) -> dict:
        url = "https://www.pbc.gov.cn" + x["href"]
        _throttle()
        doc = get_text(url, timeout=25, attempts=4, base_delay=3.0,
                       encoding="utf-8", headers=_HDR, session=_sess())
        txt = _plain(doc)

        # 操作日：三级 fallback，**一律不设日期容差**
        # 根因：人行存在批量补发，**发布日 ≠ 操作日**（实测 2025-10-09 一个发布日挂了 17 条
        # 公告，真实操作日散在前几周、相差可达 9 天）—— 若沿用「与 ID 日期差 ≤3 天才采纳」，
        # 这 17 条会全落到同一日期并互相覆盖。
        #   ① 正文阿拉伯日期（「…2026年9月24日中国人民银行…」）—— 常规公告走这条
        #   ② 中文落款日期（「二〇二五年三月二十四日」）—— 香港央票唯一可用来源
        #   ③ 19 位公告 ID 前 8 位 —— 最后兜底（7 位序号型 ID 不含日期，可能仍为 None）
        d = None
        m = _RE_DATE.search(txt)
        if m:
            try:
                dd = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                # 年份合理性保护：正文偶尔会提及往年日期，限定在 ID 年份 ±1 年内
                if x["id_date"] is None or abs(dd.year - x["id_date"].year) <= 1:
                    d = dd
            except ValueError:
                pass
        if d is None:
            d = _cn_date(txt)
        if d is None:
            d = x["id_date"]

        # 类型：同栏目同标题，只能靠正文区分
        if "央行票据" in txt and "逆回购" not in txt:
            op_type = "cbb"
        elif "买断式逆回购" in txt:
            op_type = "outright_reverse_repo"
        elif "逆回购" in txt:
            op_type = "reverse_repo"
        elif "国库现金" in txt:
            op_type = "treasury_deposit"
        else:
            op_type = "other"
        # 栏目兜底：买断式栏目下的公告，即使正文措辞变化没匹配到「逆回购」字样，
        # 也不该退化成 other（该栏目只有买断式一种业务）
        if op_type == "other" and x.get("section") == "outright_repo":
            op_type = "outright_reverse_repo"

        tenor = rate = bid = win = None

        # 正文：常规操作量 / 零操作
        mo = _RE_OP.search(txt)
        if mo:
            amt = _f_amt(mo.group(1))
            win = bid = amt
            if mo.group(2):
                tenor = int(mo.group(2))
        else:
            mz = _RE_ZERO.search(txt)
            if mz:
                # ⚠️ 旧措辞「逆回购操作量为零」无「N天期」，group(1) 为 None ⇒ tenor 保持 None
                if mz.group(1):
                    tenor = int(mz.group(1))
                win = bid = 0.0        # ⚠️ 零操作 = 0，不是 NULL

        # 表格覆盖（更权威：含利率）
        tbl = self._find_table(doc)
        if tbl:
            header, data = tbl
            col = {}
            for i, h in enumerate(header):      # header 已去空格（见 _find_table）
                if "期限" in h:
                    col["tenor"] = i
                elif "中标利率" in h or "操作利率" in h:
                    col["rate"] = i
                elif "投标量" in h:
                    col["bid"] = i
                elif "中标量" in h:
                    col["win"] = i
                elif "操作量" in h or "发行量" in h:
                    # 「操作量」= 2024 年及更早版本的表头（同义于后来的「中标量」）
                    # 「发行量」= 香港央票的用词
                    col["win"] = i

            def cell(k):
                i = col.get(k)
                return data[i] if (i is not None and i < len(data)) else None

            v = _f_tenor(cell("tenor"))
            if v:
                tenor = v
            v = _f_rate(cell("rate"))
            if v is not None:
                rate = v
            v = _f_amt(cell("bid"))
            if v is not None:
                bid = v
            v = _f_amt(cell("win"))
            if v is not None:
                win = v

        # 买断式公告**没有操作明细表**，期限只能从正文「期限为6个月（181天）」里读
        if tenor is None:
            mt = _RE_TENOR_TXT.search(txt)
            if mt:
                tenor = _f_tenor(mt.group(1))

        # 留档首个含操作事实的正文句：**以金额为锚点开窗**，并切掉可能卷进来的页面标题。
        # ⚠️ 不用「按句号切分取句子」——买断式页面的「文章头」（我的位置/高级搜索/字号/打印本页）
        # 与正文之间没有句号，会把整段黏成一句、连带正文一起被标题过滤规则丢掉
        # （实测 31 条买断式公告的 raw_text 曾全为 NULL）。
        raw = None
        mraw = re.search(r".{0,60}(?:亿元|操作量为零).{0,240}", txt)
        if mraw:
            s = mraw.group(0).strip()
            s = re.sub(r"^.*公告\s*[\[［]\s*\d{4}\s*第\s*\d+\s*号", "", s, flags=re.S).strip()
            raw = (s[:500] or None)

        return {"section": x["section"], "trade_date": d, "op_type": op_type,
                "notice_year": x["notice_year"], "notice_no": x["notice_no"],
                "tenor_days": tenor, "op_rate": rate, "bid_amount": bid,
                "win_amount": win, "notice_url": url, "raw_text": raw or None}

    # ---------- 主流程 ----------

    def run(self) -> dict:
        errors: list[str] = []

        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM cn_omo_daily")
                before = int(cur.fetchone()[0])
                # 水位**按栏目各取一个**：各栏目独立编号，共用一个水位会互相压掉
                cur.execute("SELECT section, notice_year, notice_no FROM cn_omo_daily")
                wms: dict[str, tuple[int, int]] = {}
                for s_name, y, no in cur.fetchall():
                    k = (int(y), int(no))
                    if k > wms.get(s_name, (0, 0)):
                        wms[s_name] = k
        finally:
            conn.close()

        logger.info("  cn_omo_daily 现有 %s 行，各栏目水位 (年,号) = %s%s",
                    before, wms, "（full 模式：忽略水位）" if self.full else "")

        # ---- 逐栏目抓列表页（按 (年,号) 判停）----
        pool: list[dict] = []
        sec_stat: list[tuple[str, int, int | None]] = []
        for sec in _SECTIONS:
            sid = sec["section"]
            wm = wms.get(sid)
            key = None
            limit = self.max_pages if sec["pages"] is None else sec["pages"]
            got, total = 0, None
            for n in range(1, limit + 1):
                try:
                    items, tp, key = self._list_page(sec, n, key)
                except Exception as e:      # noqa: BLE001
                    errors.append(f"{sid} list p{n}: {type(e).__name__} {str(e)[:50]}")
                    break
                if tp:
                    total = tp
                if not items:
                    break
                got += 1
                for x in items:
                    if not self.full and wm and (x["notice_year"], x["notice_no"]) <= wm:
                        continue
                    pool.append(x)
                if not self.full and wm and \
                        min((x["notice_year"], x["notice_no"]) for x in items) <= wm:
                    break
            sec_stat.append((sid, got, total))

        if not any(g for _, g, _ in sec_stat):
            raise RuntimeError("所有栏目的列表页都没抓到：" + "; ".join(errors[:3]))

        # ---- 去重（主键维度是 (section, 年, 号)）----
        seen, todo = set(), []
        for x in pool:
            k3 = (x["section"], x["notice_year"], x["notice_no"])
            if k3 in seen:
                continue
            seen.add(k3)
            todo.append(x)
        todo.sort(key=lambda x: (x["notice_year"], x["notice_no"]), reverse=True)
        logger.info("  栏目 %s · 待抓详情 %s 条",
                    "、".join(f"{s}:{g}页/{t if t else '?'}" for s, g, t in sec_stat), len(todo))

        # ---- 抓详情（并发）----
        rows, zero_n, fail_n = [], 0, 0
        if todo:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                futs = {ex.submit(self._detail, x): x for x in todo}
                for fu in as_completed(futs):
                    x = futs[fu]
                    try:
                        d = fu.result()
                    except Exception as e:  # noqa: BLE001
                        fail_n += 1
                        if len(errors) < 20:
                            errors.append(f"detail 第{x['notice_no']}号: {type(e).__name__} {str(e)[:50]}")
                        continue
                    if d["win_amount"] == 0:
                        zero_n += 1
                    rows.append(d)

        if todo and not rows:
            raise RuntimeError(f"详情页全部失败（{fail_n} 条）：" + "; ".join(errors[:3]))

        # ---- Upsert ----
        cols = ["section", "trade_date", "op_type", "notice_year", "notice_no", "tenor_days",
                "op_rate", "bid_amount", "win_amount", "notice_url", "raw_text"]
        sql = (
            f"INSERT INTO cn_omo_daily ({', '.join(cols)}, update_time, data_source) "
            f"VALUES ({', '.join(['%s'] * len(cols))}, NOW(), 'PBC') "
            "ON DUPLICATE KEY UPDATE "
            + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in cols
                        if c not in ("section", "notice_year", "notice_no"))
            + ", update_time=NOW()"
        )
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n, MIN(trade_date) AS lo, MAX(trade_date) AS hi, "
                    "SUM(CASE WHEN win_amount = 0 THEN 1 ELSE 0 END) AS zero_n, "
                    "SUM(CASE WHEN op_type='reverse_repo' THEN 1 ELSE 0 END) AS n_repo, "
                    "SUM(CASE WHEN op_type='outright_reverse_repo' THEN 1 ELSE 0 END) AS n_out, "
                    "SUM(CASE WHEN op_type='cbb' THEN 1 ELSE 0 END) AS n_cbb "
                    "FROM cn_omo_daily")
                r = cur.fetchone()
        finally:
            conn.close()

        new_rows = max(0, int(r[0]) - before)
        msg = (f"央行 OMO upsert {len(rows)} 条（新增 {new_rows}）｜"
               f"{r[1]} ~ {r[2]}｜逆回购 {r[4]} · 买断式 {r[5]} · 央票 {r[6]} · 零操作 {int(r[3] or 0)}")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": new_rows if new_rows else len(rows),
             "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: f"表内 {before} 行，各栏目水位 (年,号) {wms or '--'}",
                2: "、".join(f"{s}: {g} 页/{t if t else '?'}" for s, g, t in sec_stat),
                3: f"解析 {len(rows)} 条（失败 {fail_n}，零操作 {zero_n}）",
                4: f"待抓 {len(todo)} 条（按 (栏目,年,号) 比水位，只取更新的公告）",
                5: f"表内 {r[0]} 行（{r[1]} ~ {r[2]}）",
            },
        )
