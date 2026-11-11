"""十类构建维度小样本。不写入 openie.jsonl / openie_event.jsonl。

    python3 -m extract.pilot10
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from extract.llm import chat_json
from extract.prompt import parse_llm_json
from extract.validate import evidence_sentence, locate_span
from ingest.envelope import NewsDoc

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "extract" / "pilot10v3_sample.jsonl"
SUMMARY = ROOT / "data" / "extract" / "pilot10v3_summary.json"

TYPES = (
    "人物",
    "机构",
    "装备",
    "事件",
    "地点",
    "作品",
    "平台",
    "观点",
    "场景",
    "立场",
)
RELATIONS = (
    "参与",
    "实施",
    "搭载",
    "拦截",
    "发射",
    "展示",
    "生产",
    "部署",
    "从属",
    "去向",
    "发表",
    "针对",
    "表明",
    "关于",
    "处于",
    "涉及",
    "发布",
)
# 关系名 -> (起点类型集合, 终点类型集合)
DOMAIN = {
    "参与": ({"人物"}, {"事件"}),
    "实施": ({"机构"}, {"事件"}),
    "搭载": ({"事件"}, {"装备"}),
    "拦截": ({"事件"}, {"装备"}),
    "发射": ({"事件"}, {"装备"}),
    "展示": ({"事件"}, {"装备"}),
    "生产": ({"事件"}, {"装备"}),
    "部署": ({"机构"}, {"装备"}),
    "从属": ({"机构"}, {"机构"}),
    "去向": ({"装备"}, {"地点"}),
    "发表": ({"人物", "机构"}, {"观点"}),
    "针对": ({"观点"}, {"事件"}),
    "表明": ({"人物", "机构"}, {"立场"}),
    "关于": ({"立场"}, {"事件"}),
    "处于": ({"事件"}, {"场景"}),
    "涉及": ({"事件"}, {"装备", "作品"}),
    "发布": ({"平台"}, {"事件"}),
}
# 计划中的动作不产生这几类边
PLANNED_FORBIDDEN = {"部署", "拦截", "发射", "去向"}
EVENT_KINDS = ("发生", "表态", "计划")

# 搭载必须有「把装备投入行动」的动词线索
CARRY_CUE = re.compile(
    r"使用|动用|出动|投入|携带|编入|派出|搭载|起飞|实施|发动|展开|发射了|参加了|参与了"
)
# 拦截必须有防空或拦截动作
INTERCEPT_CUE = re.compile(r"拦截|击落|防空|防空部队|成功拦")
# 拦截的误判：统计摧毁数量、拦停车辆
INTERCEPT_FALSE = re.compile(r"摧毁[^。]{0,8}(辆|台|门|套)|客车|车辆|卡车|轿车|行人")
# 发射必须有发射动作，被袭击、被拦截不算
LAUNCH_CUE = re.compile(r"发射|试射|射出|打出|投下|投掷")
LAUNCH_FALSE = re.compile(r"遭|被拦截|被击落|被摧毁|受到")
# 从属的误判：职务描述、并列机构
SUB_FALSE = re.compile(r"常委|副省长|发言人|新闻发言人|数据显示|请愿")
# 应然句、祈使句：属于主张，不是发生类事件
NORMATIVE = re.compile(r"必须|应当|应该|不得|禁止|不能|务必|理应|需要立即|呼吁")
# 报道者与转述者：他们不是事件施动者，不得作为参与或实施的主体
# 只认「角色名」本身。前缀必须是机构式名称，不含动词性成分。
ROLE_TAIL = re.compile(
    r"(?:记者|通讯员|报道员|消息人士|知情人士|分析人士|观察员|评论员"
    r"|发言人|主编|编辑|主播|主持人)$"
)
# 前缀里出现这些字，说明是句子片段而不是机构名
PREFIX_BAD = re.compile(
    r"[，。；、！？：（）()「」《》\s]|无意|针对|表示|称|说|否认|警告|谴责|"
    r"呼吁|要求|决定|宣布|认为|指出|强调|获悉|得到|援引|"
    r"的|了|被|对|将|向|与|和"
)
# 前缀允许「当地」「该地」「本国」这类地点限定词
PREFIX_OK_LEAD = re.compile(r"^(?:当地|该地|本国|外媒|境外|该国|上述)")


def is_non_actor(name: str) -> bool:
    """判断是否为报道者或转述者，而非事件施动者。

    只把「某机构 + 记者/发言人」这类主体视为非施动者；
    「无意针对记者」这类句子片段不在此列。
    """
    t = (name or "").strip()
    if not t or not ROLE_TAIL.search(t):
        return False
    prefix = t[: -2] if len(t) > 2 else ""
    if not prefix:
        return True
    probe = PREFIX_OK_LEAD.sub("", prefix)
    if PREFIX_BAD.search(probe):
        return False
    # 前缀只允许中文机构名、字母数字与间隔号，长度不超过 16
    if len(prefix) > 16:
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9·]+", prefix))
# 只有口头动作的关系名，转述者可以作主体
SPEECH_REL = {"表明", "发表"}
PERSON_ATTRS = (
    "别名",
    "性别",
    "出生日期",
    "籍贯",
    "出生地",
    "国籍",
    "民族",
    "宗教",
    "社会评价",
    "身份/职业",
    "重大事件参与记录",
    "社会关系",
    "主要观点",
    "工作经历",
    "教育经历",
    "近期活动",
    "公开行程",
    "奖惩记录/作品",
    "意识形态",
)

SYSTEM_ZH = """你按给定的十类构建维度，从新闻中抽取实体、关系和人物要素。只使用原文，不要编造。
十类：人物、机构、装备、事件、地点、作品、平台、观点、场景、立场。
场景是事件所处的事态场面，不是地名。地点只收地名、海域、基地。
立场是人物或机构对事件表明的态度，只取态度词或短语，不超过20字，不是主张原句。观点只收主张原句。
作品指有题名的协议、条约、法律、规划、文件、报告、声明和影像资料，标题需在原文出现。
场景、立场都要连接到对应事件。国家、军队、部门分别作为机构。舰船和飞机归装备。媒体栏目、站点、账号归平台。
人物20项要素只填该块里写明的连续片段，没写的留空字符串。
输出一个 JSON 对象，不要 Markdown 围栏。"""

USER_ZH = """从下面新闻块抽取。所有 text、evidence、人物要素的非空值必须是标题或正文中的连续子串。

一、事件边界
一个主谓结构算一个事件。目的状语、结果从句、能力描述不单独成事件。
正例：「宣布组建自主作战司令部，以提升自主作战能力」只抽「组建自主作战司令部」，不抽「提升作战能力」。
反例：把「提升美军规模化自主及机器人作战能力」另抽成一个事件。
同一句里的口头转述与已发生动作分开：据X称，Y袭击了Z，只抽「Y袭击Z」这一条发生事件。

事件的句子形态必须是陈述句，有明确施动者和动作。祈使句和应然句不算发生类事件：
- 「美军必须撤离该地区」「各方应保持克制」「不得进入该海域」是主张，标成表态，不标成发生。
- 「将会打击」「可能采取行动」是计划。
只把真实的动作与状态标成发生，例如「空袭了目标」「抵达港口」「签署协议」「选举举行」。

二、话语种类
每条事件必须标 kind：
- 发生：已经发生或正在发生的动作与状态
- 表态：称、表示、谴责、警告、否认、确认等口头口径
- 计划：将、拟、考虑、讨论、准备、寻求、拟议等尚未发生
反例：「考虑派出第84师」是计划，不是部署；「将部署到」是计划，不是已部署。

三、槽位
主体＝施动者，对象＝动作直接作用的对象，地点＝动作发生或抵达的位置，装备＝被使用、发射、拦截、部署的器械。
反例：不要把打击目标标成装备；不要把「拦截一辆客车」中的客车当装备。

主体必须是事件的施动者或直接承受者。以下三种不算事件的参与方，不得进入「参与」「实施」：
- 记者、通讯员、总台记者、本报记者：他们是报道的署名者或现场观察者，「新华社记者听到爆炸声」不是记者参与事件。
- 发言人、消息人士、知情人士、分析人士：他们转述别人的行为，本身不是施动者。他们有口头表态时只走「表明」与「发表」，不走「参与」。
- 观察员、评论员、编辑、主播：旁观或评论角色。
正例：「伊朗外交部发言人否认袭击」，关系是「发言人 表明 否认」，不是「发言人 参与 否认」。
反例：「总台记者获悉无人机失联」不得写成「总台记者 参与 无人机失联」。

四、关系
关系名与方向必须按下面登记，起点终点都要出现在 entities 中且类型相符：
参与 人物→事件（人物参与、出席、指挥）
实施 机构→事件（机构发动、执行、决定）
搭载 事件→装备（该事件由施动方把该装备投入行动：使用、动用、出动、携带、编入）
拦截 事件→装备（该装备被防空或拦截行动击中、击落；单纯统计摧毁数量不算）
发射 事件→装备（该装备被发射、试射出去）
展示 事件→装备（展会活动，该装备参展亮相）
生产 事件→装备（涉及该装备的生产、研制、交付、建造）
部署 机构→装备（机构已实际部署该装备；计划、考虑、探讨引进都不写这条）
去向 装备→地点（装备实际移动到该地：部署、转移、抵达、靠泊、飞抵、驶往；从某地起飞、在某地被击中都不写这条）
从属 机构→机构（后者是上级或国家）
发表 人物或机构→观点（说出、写出、发布该主张）
针对 观点→事件（该观点针对该事件）
表明 人物或机构→立场（表明该态度）
关于 立场→事件（该立场针对该事件）
处于 事件→场景（事件处于该事态场面）
涉及 事件→作品（事件涉及该文件、协议、法案、报告、影像）
发布 平台→事件（该平台刊发或播出该事件）

关系的 evidence 取同时包含两端或能明确支撑这条边的原句。
计划、考虑、讨论中的动作不写部署、拦截、发射、去向四条边。

五、两条容易错的边
搭载只用于「事件把装备投入行动」。装备被摧毁、被拦截、被击中不算搭载；装备只是被提及、统计、列装、运送物资也不算。
反例：「一架无人机在加油站附近坠落」不是搭载；「医院船开展医疗服务」不是搭载；「携带武器的定居者」不是搭载。
拦截只用于「防空或拦截行动击中装备」。摧毁对方车辆、装备统计数字不算拦截。
反例：「摧毁380辆军用车辆」不是拦截；「拦截一辆客车」不是拦截。

六、正反例
正例：「以军在加沙地带空袭」，地点=加沙地带，场景=空袭。
反例：不要把「加沙地带」标成场景，不要把「空袭」标成地点。
正例：「中方对此表示谴责，并称将捍卫主权」，立场=谴责，观点=将捍卫主权。
反例：不要把整句主张标成立场，不要把「谴责」标成观点。
正例：「双方签署《吉达停火协议》」，作品=《吉达停火协议》，关系「事件 涉及 作品」。
反例：不要把「停火」「协议」这类单独词标成作品。
正例：「沙特防空部队拦截并摧毁一架无人机」，事件=拦截并摧毁一架无人机，关系「事件 拦截 无人机」。
反例：不要把无人机写成「事件 搭载 无人机」。

人物要素名只能是：{attrs}。姓名用 entities 里的人物 text。

JSON：
{{
  "entities": [{{"type": "十类之一", "text": "原文片段"}}],
  "relations": [{{"name": "关系名", "head": "起点原文", "tail": "终点原文", "evidence": "支撑该边的原句"}}],
  "events": [{{"text": "事件原文片段", "kind": "发生|表态|计划"}}],
  "persons": [{{"name": "人物姓名原文", "attrs": {{"别名": "", "性别": ""}}}}]
}}

新闻块：
{block}
"""

SYSTEM_EN = """Extract entities, relations, and person attributes for a ten-type event graph. Use only the given text.
Type labels must be exactly these Chinese words: 人物, 机构, 装备, 事件, 地点, 作品, 平台, 观点, 场景, 立场.
场景 is the situational setting of the event, not a place name. 地点 is only a place name, sea, or base.
立场 is a short attitude phrase of at most 20 characters, not the claim sentence. 观点 is the claim sentence.
作品 means a titled agreement, treaty, law, plan, document, report, statement, or video whose title appears in the text.
Link 场景 and 立场 to the event. Countries, militaries, and departments are 机构. Ships and aircraft are 装备. Outlets and accounts are 平台.
Fill a person attribute only with a contiguous span from this block. Leave it "" when the text does not say it.
Return one JSON object, no Markdown fence."""

USER_EN = """Extract from the news block. Every non-empty text, evidence, and person attribute MUST be a contiguous substring of the title or body.

1. Event boundaries. One subject-predicate structure is one event. Purpose clauses, result clauses, and capability descriptions are not separate events.
Positive: "announced the creation of an autonomous warfare command to boost capability" → only "creation of an autonomous warfare command".
Negative: do not also extract "boost capability" as a second event.
Speech framing and the action reported are separate: in "X said Y struck Z", keep only "Y struck Z" as 发生.

An event must be a declarative clause with an agent and an action. Imperatives and normative statements are not 发生 events:
- "US forces must leave the region", "all sides should exercise restraint", "vessels shall not enter these waters" are claims: label them 表态, never 发生.
- "will strike", "may take action" are 计划.
Only real actions and states are 发生, for example "struck the target", "arrived at port", "signed the agreement", "the election was held".

2. Discourse kind. Every event carries kind:
- 发生: already happened or ongoing
- 表态: said, stated, condemned, warned, denied, confirmed
- 计划: will, plans to, considering, discussing, preparing, seeking
Negative: "considering sending the 84th Division" is 计划, not a deployment; "will deploy" is 计划.

3. Slots. 主体 is the agent, 对象 is what the action directly affects, 地点 is where it happened or arrived, 装备 is the equipment used, launched, intercepted, or deployed.
Negative: do not mark a strike target as 装备; the bus in "intercepted a bus" is not 装备.

The 主体 must be the actor or the direct victim of the event. These three kinds of names are never participants and must not appear as the head of 参与 or 实施:
- Reporters and correspondents ("Xinhua reporter", "staff writer"): they are bylines or observers. "A Xinhua reporter heard explosions" is not the reporter taking part in an event.
- Spokespeople, sources, people familiar with the matter, analysts: they relay what others did. When they make a statement, use 表明 and 发表 only, never 参与.
- Observers, commentators, editors, anchors: onlookers or commentators.
Positive: "the foreign ministry spokesperson denied the attack" → 发言人 表明 否认, not 发言人 参与 否认.
Negative: "the reporter learned that a drone went missing" must not become 记者 参与 无人机失联.

4. Relations. Names and directions are fixed; both endpoints must appear in entities with matching types:
参与 人物→事件 (took part in, attended, commanded)
实施 机构→事件 (carried out, decided, executed)
搭载 事件→装备 (the event employed this equipment: sortied, used, carried)
拦截 事件→装备 (the equipment was intercepted, shot down, destroyed in the event)
发射 事件→装备 (the equipment was launched or test-fired in the event)
展示 事件→装备 (exhibition: the equipment was displayed)
生产 事件→装备 (production, development, delivery, construction)
部署 机构→装备 (the organization has actually deployed it; planned deployment does not count)
去向 装备→地点 (the equipment actually moved there: deployed, transferred, arrived, docked, flew to; the scene of an incident does not count)
从属 机构→机构 (the latter is the parent body or the state)
发表 人物 or 机构→观点 (said, wrote, published the claim)
针对 观点→事件 (the claim is about the event)
表明 人物 or 机构→立场 (expressed the attitude)
关于 立场→事件 (the attitude is about the event)
处于 事件→场景 (the event sits in this setting)
涉及 事件→作品 (the event involves this document, agreement, bill, report, or video)
发布 平台→事件 (the outlet carried the report)

The evidence must be the sentence that supports this edge.
Planned, considered, or merely discussed actions do not produce 部署, 拦截, 发射, or 去向 edges.

5. Examples.
Positive: "The IDF struck targets in the Gaza Strip" → 地点=Gaza Strip, 场景=struck targets.
Negative: do not label "Gaza Strip" as 场景, nor the strike wording as 地点.
Positive: "Beijing condemned the strike and said it would defend its sovereignty" → 立场=condemned, 观点=it would defend its sovereignty.
Negative: do not label the whole claim sentence as 立场.
Positive: "The two sides signed the Jeddah ceasefire agreement" → 作品=Jeddah ceasefire agreement with edge 事件 涉及 作品.
Positive: "Saudi air defences intercepted and destroyed a drone" → 事件=intercepted and destroyed a drone, edge 事件 拦截 无人机.
Negative: do not record that as 事件 搭载 无人机.

6. Two edges that are easy to get wrong.
搭载 is only for "the event commits the equipment to action". Equipment that was destroyed, intercepted, or hit is not 搭载. Equipment that is merely mentioned, counted, fielded, or used to ship supplies is not 搭载.
Negative: "a drone crashed near a petrol station" is not 搭载; "the hospital ship provided medical services" is not 搭载; "settlers carrying weapons" is not 搭载.
拦截 is only for "an air-defence or intercept action hit the equipment". Destroying the other side's vehicles or reporting cumulative kill counts is not 拦截.
Negative: "destroyed 380 military vehicles" is not 拦截; "intercepted a bus" is not 拦截.

Person attribute names must be: {attrs}. The person's name is the 人物 entity text.

JSON:
{{
  "entities": [{{"type": "one of the ten Chinese labels", "text": "source span"}}],
  "relations": [{{"name": "relation", "head": "source span", "tail": "source span", "evidence": "sentence supporting the edge"}}],
  "events": [{{"text": "event span", "kind": "发生|表态|计划"}}],
  "persons": [{{"name": "name span", "attrs": {{"别名": "", "性别": ""}}}}]
}}

News block:
{block}
"""


def _load_source(source: str, date_from: str, date_to: str) -> list[NewsDoc]:
    by_url: dict[str, dict] = {}
    for path in sorted((RAW / source).glob("*.jsonl")):
        day = path.stem
        if day < date_from or day > date_to:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = raw.get("url") or ""
            if url:
                by_url[url] = raw
    docs = []
    for raw in by_url.values():
        doc = NewsDoc(
            doc_id=raw.get("doc_id") or "",
            title=raw.get("title") or "",
            body=raw.get("body") or "",
            publish_time=raw.get("publish_time") or "",
            source=raw.get("source") or source,
            source_name=raw.get("source_name") or "",
            url=raw.get("url") or "",
            html_hash=raw.get("html_hash") or "",
            lang=raw.get("lang") or "",
            fetched_at=raw.get("fetched_at") or "",
            channel=raw.get("channel") or "",
            extra=raw.get("extra"),
        )
        docs.append(doc)
    return docs


def _chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def pick_stratified(docs: list[NewsDoc], n: int) -> list[NewsDoc]:
    usable = [d for d in docs if _chars(d.body) >= 80 and d.title]
    usable.sort(key=lambda d: _chars(d.body))
    if len(usable) <= n:
        return usable
    idxs = []
    for i in range(n):
        idxs.append(round(i * (len(usable) - 1) / (n - 1)))
    seen = set()
    out = []
    for i in idxs:
        doc = usable[i]
        if doc.doc_id in seen:
            continue
        seen.add(doc.doc_id)
        out.append(doc)
    return out


def _pieces(text: str, limit: int = 3500) -> list[str]:
    """按句切块，块大小接近模型的可用上下文，不丢弃后文。

    3500 字符约 3.5K token，加上系统提示与输出余量，
    仍在 64K 上下文的舒适范围内。长稿按实际块数处理，不做截断。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    parts = [p.strip() for p in re.split(r"(?<=[。！？.!?])\s+", text) if p.strip()]
    if len(parts) <= 1:
        # 无句读的长文本按长度硬切
        return [text[i : i + limit] for i in range(0, len(text), limit)]
    groups: list[str] = []
    buf: list[str] = []
    n = 0
    for part in parts:
        # 单句超长时先切开，避免一个块无限增长
        while len(part) > limit:
            if buf:
                groups.append(" ".join(buf))
                buf, n = [], 0
            groups.append(part[:limit])
            part = part[limit:]
        if buf and n + len(part) + 1 > limit:
            groups.append(" ".join(buf))
            buf, n = [part], len(part)
        else:
            buf.append(part)
            n += len(part) + 1
    if buf:
        groups.append(" ".join(buf))
    return groups

def _prompt(doc: NewsDoc, chunk: str) -> tuple[str, str]:
    block = f"【标题】{doc.title}\n【发稿时间】{doc.publish_time}\n【正文】\n{chunk}"
    attrs = "、".join(PERSON_ATTRS)
    if (doc.lang or "").startswith("en"):
        return SYSTEM_EN, USER_EN.format(attrs=attrs, block=block)
    return SYSTEM_ZH, USER_ZH.format(attrs=attrs, block=block)


def _keep_span(text: str, hay: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if locate_span(text, hay):
        return text
    return ""


MAX_STANCE_CHARS = 20


def _check(parsed: dict, hay: str) -> dict:
    parsed = parsed if isinstance(parsed, dict) else {}
    entities = []
    dropped = Counter()
    raw_entities = parsed.get("entities")
    if not isinstance(raw_entities, list):
        raw_entities = []
    for ent in raw_entities:
        if not isinstance(ent, dict):
            dropped["entity"] += 1
            continue
        typ = str(ent.get("type") or "").strip()
        text = _keep_span(str(ent.get("text") or ""), hay)
        if typ not in TYPES or not text:
            dropped["entity"] += 1
            continue
        if typ == "立场":
            compact = re.sub(r"\s+", "", text)
            if len(compact) > MAX_STANCE_CHARS:
                dropped["stance_too_long"] += 1
                continue
        entities.append({"type": typ, "text": text})
    types_of: dict[str, set[str]] = {}
    for ent in entities:
        types_of.setdefault(ent["text"], set()).add(ent["type"])
    relations = []
    seen_rel: set[tuple[str, str, str]] = set()
    for rel in parsed.get("relations") or []:
        if not isinstance(rel, dict):
            dropped["relation"] += 1
            continue
        name = str(rel.get("name") or "").strip()
        head = _keep_span(str(rel.get("head") or ""), hay)
        tail = _keep_span(str(rel.get("tail") or ""), hay)
        evidence = _keep_span(str(rel.get("evidence") or ""), hay)
        if name not in RELATIONS or not head or not tail or not evidence:
            dropped["relation"] += 1
            continue
        # 搭载与拦截要求证据里有对应动词线索
        if name == "搭载" and not CARRY_CUE.search(evidence):
            dropped["carry_no_cue"] += 1
            continue
        if name == "拦截":
            if not INTERCEPT_CUE.search(evidence) or INTERCEPT_FALSE.search(evidence):
                dropped["intercept_no_cue"] += 1
                continue
        if name == "发射":
            if not LAUNCH_CUE.search(evidence) or LAUNCH_FALSE.search(evidence):
                dropped["launch_no_cue"] += 1
                continue
        if name == "从属" and SUB_FALSE.search(evidence):
            dropped["sub_role_desc"] += 1
            continue
        # 记者、发言人、消息人士不是事件施动者
        if name not in SPEECH_REL and is_non_actor(head):
            dropped["non_actor_head"] += 1
            continue
        if head not in types_of or tail not in types_of:
            dropped["relation_untyped_end"] += 1
            continue
        want_head, want_tail = DOMAIN[name]
        if not (types_of[head] & want_head) or not (types_of[tail] & want_tail):
            dropped["relation_type_mismatch"] += 1
            continue
        key = (name, head, tail)
        if key in seen_rel:
            dropped["relation_dup"] += 1
            continue
        seen_rel.add(key)
        relations.append({"name": name, "head": head, "tail": tail, "evidence": evidence})

    # 事件话语种类：抽取时给出，缺失时按动词线索补判，仍判不出记「发生」
    kinds: dict[str, str] = {}
    raw_events = parsed.get("events") if isinstance(parsed.get("events"), list) else []
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        text = _keep_span(str(item.get("text") or ""), hay)
        kind = str(item.get("kind") or "").strip()
        if text and kind in EVENT_KINDS:
            kinds[text] = kind
    for ent in entities:
        if ent["type"] != "事件":
            continue
        if ent["text"] in kinds:
            continue
        kinds[ent["text"]] = _guess_kind(ent["text"])

    # 计划中的动作不产生部署、拦截、发射、去向
    kept_rels = []
    for rel in relations:
        name = rel["name"]
        # 计划中的动作不产生这些边：计划、考虑、探讨都不算已经发生
        if name in PLANNED_FORBIDDEN or name in ("搭载", "生产", "展示"):
            kind = kinds.get(rel["head"]) or kinds.get(rel["tail"]) or ""
            if kind == "计划":
                dropped["relation_planned"] += 1
                continue
            if name in PLANNED_FORBIDDEN and _planned_evidence(rel.get("evidence") or ""):
                dropped["relation_planned"] += 1
                continue
        kept_rels.append(rel)
    relations = kept_rels

    # 从属不允许双向：同一块里 a→b 与 b→a 同时出现时，保留证据更长的一条
    by_pair: dict[tuple[str, str], list[dict]] = {}
    for rel in relations:
        if rel["name"] != "从属":
            continue
        by_pair.setdefault((rel["head"], rel["tail"]), []).append(rel)
    drop_ids: set[int] = set()
    for (a, b), fwd in by_pair.items():
        rev = by_pair.get((b, a))
        if not rev:
            continue
        keep = max(fwd + rev, key=lambda r: len(r.get("evidence") or ""))
        for rel in fwd + rev:
            if rel is not keep:
                drop_ids.add(id(rel))
    if drop_ids:
        dropped["sub_bidirectional"] += sum(1 for r in relations if id(r) in drop_ids)
        relations = [r for r in relations if id(r) not in drop_ids]

    # 事件节点补充 kind
    for ent in entities:
        if ent["type"] == "事件":
            ent["kind"] = kinds.get(ent["text"], "发生")

    attached = _attach_to_event(entities, relations, hay)
    relations.extend(attached)
    persons = []
    for person in parsed.get("persons") or []:
        if not isinstance(person, dict):
            dropped["person"] += 1
            continue
        name = _keep_span(str(person.get("name") or ""), hay)
        if not name:
            dropped["person"] += 1
            continue
        attrs_in = person.get("attrs") if isinstance(person.get("attrs"), dict) else {}
        attrs = {}
        for key in PERSON_ATTRS:
            val = _keep_span(str(attrs_in.get(key) or ""), hay)
            attrs[key] = val
        persons.append({"name": name, "attrs": attrs})
    return {
        "entities": entities,
        "relations": relations,
        "persons": persons,
        "dropped": dict(dropped),
    }


PLAN_CUE = re.compile(r"将|拟|计划|准备|考虑|讨论|寻求|打算|有望|或于|即将|拟于|有意")
DONE_CUE = re.compile(r"已|了|当天|当日|近日|完成|成功|宣布|抵达|出动|实施")


def _planned_evidence(evidence: str) -> bool:
    """证据句只有计划线索、没有完成线索时，视为计划动作。"""
    ev = evidence or ""
    if not ev:
        return False
    return bool(PLAN_CUE.search(ev)) and not DONE_CUE.search(ev)


def _guess_kind(text: str) -> str:
    """事件文本缺 kind 时的兜底判断。"""
    t = text or ""
    if PLAN_CUE.search(t):
        return "计划"
    if NORMATIVE.search(t):
        return "表态"
    if re.search(r"称|表示|谴责|警告|否认|确认|宣布|指出|强调", t):
        return "表态"
    return "发生"


def _attach_to_event(entities: list[dict], relations: list[dict], hay: str) -> list[dict]:
    """块内只有一个事件时，把场景、观点、立场挂到该事件上，原句取事件所在句。"""
    events = {e["text"] for e in entities if e["type"] == "事件"}
    if len(events) != 1:
        return []
    event = next(iter(events))
    have = {(r["name"], r["head"], r["tail"]) for r in relations}
    out = []
    for ent in entities:
        if ent["type"] == "场景":
            pair = ("处于", event, ent["text"])
        elif ent["type"] == "立场":
            pair = ("关于", ent["text"], event)
        elif ent["type"] == "观点":
            pair = ("针对", ent["text"], event)
        else:
            continue
        if pair in have:
            continue
        have.add(pair)
        evidence = (
            evidence_sentence(hay, ent["text"])
            or evidence_sentence(hay, event)
            or ""
        )
        out.append(
            {
                "name": pair[0],
                "head": pair[1],
                "tail": pair[2],
                "evidence": evidence,
                "derived": "single_event_attach",
            }
        )
    return out


def extract_one(doc: NewsDoc) -> dict:
    chunks = _pieces(doc.body)
    merged = {"entities": [], "relations": [], "persons": [], "dropped": Counter()}
    covered = 0
    for i, chunk in enumerate(chunks):
        hay = f"{doc.title}\n{chunk}"
        covered += len(chunk)
        system, user = _prompt(doc, chunk)
        raw = chat_json(system, user)
        parsed = parse_llm_json(raw)
        checked = _check(parsed, hay)
        for key in ("entities", "relations", "persons"):
            for item in checked[key]:
                item["chunk"] = i
                merged[key].append(item)
        merged["dropped"].update(checked["dropped"])
    return {
        "doc_id": doc.doc_id,
        "source": doc.source,
        "lang": doc.lang,
        "title": doc.title,
        "publish_time": doc.publish_time,
        "url": doc.url,
        "domain": (doc.extra or {}).get("domain") or "",
        "body_chars": _chars(doc.body),
        "n_chunks": len(chunks),
        "body_chars": len(doc.body or ""),
        "covered_chars": covered,
        "truncated_chars": max(0, len(doc.body or "") - covered),
        "entities": merged["entities"],
        "relations": merged["relations"],
        "persons": merged["persons"],
        "dropped": dict(merged["dropped"]),
    }


def _confusion(rows: list[dict]) -> dict:
    scene_as_place = 0
    stance_as_claim = 0
    stance_missing_evidence = 0
    derived_missing_evidence = 0
    scenes = 0
    stances = 0
    for row in rows:
        places = {e["text"] for e in row["entities"] if e["type"] == "地点"}
        claims = {e["text"] for e in row["entities"] if e["type"] == "观点"}
        for ent in row["entities"]:
            if ent["type"] == "场景":
                scenes += 1
                if ent["text"] in places:
                    scene_as_place += 1
            if ent["type"] == "立场":
                stances += 1
                if len(ent["text"]) >= 40 or ent["text"] in claims:
                    stance_as_claim += 1
        for rel in row["relations"]:
            if not (rel.get("evidence") or "").strip():
                if rel.get("derived"):
                    derived_missing_evidence += 1
                elif rel["name"] == "表明":
                    stance_missing_evidence += 1
    return {
        "scenes": scenes,
        "scene_overlap_place": scene_as_place,
        "stances": stances,
        "stance_long_or_claim": stance_as_claim,
        "stance_rel_missing_evidence": stance_missing_evidence,
        "derived_missing_evidence": derived_missing_evidence,
    }


def summarize(rows: list[dict]) -> dict:
    by_src = Counter(r["source"] for r in rows)
    types = Counter()
    rels = Counter()
    derived = 0
    conform = 0
    mismatch = 0
    attr_filled = Counter()
    persons = 0
    for row in rows:
        types.update(e["type"] for e in row["entities"])
        types_of: dict[str, set[str]] = {}
        for ent in row["entities"]:
            types_of.setdefault(ent["text"], set()).add(ent["type"])
        for rel in row["relations"]:
            rels[rel["name"]] += 1
            if rel.get("derived"):
                derived += 1
            want_head, want_tail = DOMAIN.get(rel["name"], (set(), set()))
            ht = types_of.get(rel["head"], set())
            tt = types_of.get(rel["tail"], set())
            if (ht & want_head) and (tt & want_tail):
                conform += 1
            else:
                mismatch += 1
        for person in row["persons"]:
            persons += 1
            for key, val in person["attrs"].items():
                if val:
                    attr_filled[key] += 1
    missing = [name for name in TYPES if types[name] == 0]
    total_rel = sum(rels.values())
    return {
        "docs": len(rows),
        "by_source": dict(by_src),
        "types": dict(types),
        "missing_types": missing,
        "relations": dict(rels),
        "relations_total": total_rel,
        "relations_derived": derived,
        "relations_conform": conform,
        "relations_mismatch": mismatch,
        "conform_rate": round(conform / total_rel, 4) if total_rel else None,
        "persons": persons,
        "attr_filled": dict(attr_filled),
        "confusion": _confusion(rows),
        "dropped": dict(sum((Counter(r.get("dropped") or {}) for r in rows), Counter())),
    }


def main() -> int:
    zh = pick_stratified(_load_source("huanqiu_mil", "2026-10-24", "2026-11-03"), 20)
    en = [
        d
        for d in pick_stratified(_load_source("rss_api", "2026-10-28", "2026-11-02"), 40)
        if (d.lang or "").startswith("en")
    ][:20]
    docs = zh + en
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with OUT.open("w", encoding="utf-8") as f:
        for i, doc in enumerate(docs, 1):
            print(f"[{i}/{len(docs)}] {doc.source} {(doc.publish_time or '')[:10]} {doc.title[:40]}", flush=True)
            try:
                row = extract_one(doc)
            except Exception as exc:
                row = {
                    "doc_id": doc.doc_id,
                    "source": doc.source,
                    "lang": doc.lang,
                    "title": doc.title,
                    "publish_time": doc.publish_time,
                    "url": doc.url,
                    "error": f"{type(exc).__name__}: {exc}",
                    "entities": [],
                    "relations": [],
                    "persons": [],
                    "dropped": {},
                }
                print(f"  error {row['error']}", flush=True)
            else:
                print(
                    f"  entities={len(row['entities'])} relations={len(row['relations'])} persons={len(row['persons'])}",
                    flush=True,
                )
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
    ok = [r for r in rows if not r.get("error")]
    summary = summarize(ok)
    summary["errors"] = len(rows) - len(ok)
    summary_path = SUMMARY
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
