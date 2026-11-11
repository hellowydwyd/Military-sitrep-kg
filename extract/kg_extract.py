"""事件中心抽取：五类实体、两级事件、事件起点的角色关系。

设计要点：
- 五类实体：事件、机构、人物、装备、地点。作品为受限实体。
- 平台、观点、场景、立场不进图，分别落为文档属性、事件属性、边属性。
- 关系只表语义角色，动作类型上移到事件的 event_type。
- 所有角色边以事件为起点，元路径方向统一。

    python3 -m extract.kg_extract --source huanqiu_mil --from 2026-10-01 --to 2026-10-31
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
DATA = ROOT / "data"

# ---------------- 类型与枚举 ----------------

ENTITY_TYPES = ("机构", "人物", "装备", "地点", "作品")
NAME_TYPES = ("机构", "人物", "装备", "地点", "作品")
# 事件不是实体，但仍是图节点；单独统计，实体 F1 不含事件
NODE_TYPES = ENTITY_TYPES + ("事件",)

# 事件类型：动作差异落在这里，不再用关系名区分
EVENT_TYPES = (
    "空袭", "打击", "拦截", "发射", "部署", "撤离", "演习",
    "控制", "签署", "谈判", "会议", "访问", "逮捕", "援助", "声明",
    "其他",
)
EVENT_TYPES_STR = "、".join(EVENT_TYPES)

# 场景：事件发生的场合形式，与事件类型正交
SETTINGS = (
    "战斗", "演习", "访问", "会谈", "记者会", "视察",
    "会议", "谈判", "审判", "选举", "签署", "部署",
    "巡逻", "救援", "其他",
)
SETTINGS_STR = "、".join(SETTINGS)

# 立场：表态边上的受控取值
STANCES = (
    "支持", "反对", "谴责", "警告", "否认",
    "承认", "呼吁", "要求", "关切", "中立",
)
STANCES_STR = "、".join(STANCES)

EVENT_KINDS = ("发生", "表态", "计划")

# 关系表：关系名 -> (起点类型集合, 终点类型集合)
# 角色关系以事件为起点，结构关系两端都是实体，层级关系在事件之间
RELATIONS = (
    "施事", "受事", "到场", "使用", "涉及",
    "表态", "包含",
    "隶属", "辖属", "驻地", "部署方", "去向",
    "任职", "会晤", "搭载", "位于", "敌对",
)
DOMAIN = {
    "施事": ({"事件"}, {"机构", "人物"}),
    "受事": ({"事件"}, {"机构", "人物", "地点"}),
    "到场": ({"事件"}, {"人物"}),
    "使用": ({"事件"}, {"装备"}),
    "涉及": ({"事件"}, {"作品"}),
    "表态": ({"事件"}, {"机构", "人物"}),
    "包含": ({"事件"}, {"事件"}),
    "隶属": ({"机构"}, {"机构"}),
    "辖属": ({"机构"}, {"机构"}),
    "驻地": ({"机构"}, {"地点"}),
    "部署方": ({"装备"}, {"机构"}),
    "去向": ({"装备"}, {"地点"}),
    # 以下 5 种为实体间的稳定关系，归 RE
    "任职": ({"人物"}, {"机构"}),
    "会晤": ({"人物"}, {"人物"}),
    "搭载": ({"装备"}, {"装备"}),
    "位于": ({"地点"}, {"地点"}),
    "敌对": ({"机构"}, {"机构"}),
}

# 计划中的动作不产生这些边
PLANNED_FORBIDDEN = {"使用", "部署方", "去向", "包含"}

# 原子事件长度边界
ATOM_MIN = 8
ATOM_MAX = 60

# 条件句与推测句：不是已发生或已计划的确定事件
CONDITIONAL = re.compile(r"^如果|^若|^假如|^一旦|假如.*将|如果.*将|一旦.*将")
SPECULATIVE = re.compile(r"可能|或许|预计将|有望|或将|恐怕|大概|也许|不排除")

# ---------------- 判定辅助 ----------------

PLAN_CUE = re.compile(r"将|拟|计划|准备|考虑|讨论|寻求|打算|有望|或于|即将|拟于|有意")
DONE_CUE = re.compile(r"已|了|当天|当日|近日|完成|成功|宣布|抵达|出动|实施")
NORMATIVE = re.compile(r"必须|应当|应该|不得|禁止|不能|务必|理应|需要立即|呼吁")

# 事件文本里的谓语动词，用来判断是否是完整事件
PREDICATE = re.compile(
    r"袭击|打击|空袭|轰炸|炮击|发射|试射|拦截|击落|击退|击毁|摧毁|俘获|缴获|"
    r"部署|增派|调动|撤离|撤出|进攻|反攻|推进|占领|控制|包围|封锁|"
    r"发动|展开|实施|进行|遭到|遭受|受到|遇袭|"
    r"签署|达成|通过|批准|宣布|决定|谴责|警告|否认|承认|呼吁|要求|"
    r"会谈|会晤|磋商|访问|抵达|离开|返回|靠泊|起飞|降落|坠毁|"
    r"死亡|受伤|丧生|遇难|打死|打伤|逮捕|拘押|处决|"
    r"演习|训练|巡逻|侦察|监视|搜救|救援|运送|交付|生产|建造|"
    r"停火|休战|和谈|谈判|成立|加入|退出|接管|移交|组建|举行|召开|"
    r"视察|检查|走访|发表|表示|称|指出|强调|确认|访问"
)

# 报道者与转述者：不是事件施动者
ROLE_TAIL = re.compile(
    r"(?:记者|通讯员|报道员|消息人士|知情人士|分析人士|观察员|评论员"
    r"|发言人|主编|编辑|主播|主持人)$"
)
PREFIX_BAD = re.compile(
    r"[，。；、！？：（）()「」《》\s]|无意|针对|表示|称|说|否认|警告|谴责|"
    r"呼吁|要求|决定|宣布|认为|指出|强调|获悉|得到|援引|"
    r"的|了|被|对|将|向|与|和"
)
PREFIX_OK_LEAD = re.compile(r"^(?:当地|该地|本国|外媒|境外|该国|上述)")

# 事件类型判定的动词线索，按优先级
# 判定顺序即语义优先级：越具体的动作越先判，泛化的排在后面
TYPE_CUES = (
    ("空袭", re.compile(r"空袭|轰炸|空投|炮击|火箭弹袭击|导弹袭击")),
    ("拦截", re.compile(r"拦截|击落|击毁|防空作战|成功拦")),
    ("发射", re.compile(r"发射|试射|射出|打出")),
    ("打击",
     re.compile(
         r"打击|袭击|攻击|突击|猛攻|强攻|摧毁|击毙|打死|打伤|交火|激战|"
         r"作战|攻势|反攻|进攻|推进|夺取|攻占|清剿|扫荡|镇压|开火|射击|"
         r"军事行动|地面行动|政变|政权更迭|"
         r"造成.{0,8}死亡|造成.{0,8}伤|伤亡|阵亡"
     )),
    ("部署",
     re.compile(
         r"部署|增派|调动|调往|进驻|驻扎|派遣|派驻|集结|起飞|运送|飞行|巡航|"
         r"移交.{0,8}指挥权|转入.{0,4}态势|战备警巡|警巡|组建|成立|"
         r"交付|移交给|交给|转交|军售|调整|改组|重组"
     )),
    ("撤离", re.compile(r"撤离|撤出|撤退|回撤|撤回|返航|撤走")),
    ("控制",
     re.compile(
         r"控制|占领|攻占|占据|接管|接手|封锁|包围|夺取|"
         r"隔离|戒严|清场|驱离|扣留"
     )),
    ("演习",
     re.compile(
         r"演习|演练|训练|军演|实弹|测试|试验|筹备|评估|"
         r"联演|联合演习|军事考察|考察"
     )),
    ("签署",
     re.compile(
         r"签署|签订|缔约|达成.{0,6}协议|达成一致|达成.{0,4}共识|"
         r"加入|退出|脱离|中止|批准|通过.{0,6}(法案|决议|预算)|"
         r"合作|联合|共同|协同"
     )),
    ("谈判", re.compile(r"谈判|和谈|磋商|对话|会谈|会晤|协商|调解|斡旋")),
    ("会议",
     re.compile(
         r"会议|峰会|论坛|大会|例会|座谈|仪式|庆典|典礼|集会|游行|示威|抗议|"
         r"选举|投票|获胜|就职|任命|掌握.{0,6}情况|了解.{0,6}情况"
     )),
    ("访问", re.compile(r"访问|出访|到访|抵达|前往|接见|会见|视察|检查|走访|慰问")),
    ("逮捕", re.compile(r"逮捕|拘押|拘留|抓获|抓捕|起诉|审判|判决|宣判")),
    ("援助", re.compile(r"援助|军援|支援|救济|提供.{0,6}(物资|装备|资金|贷款)")),
    ("声明",
     re.compile(
         r"声明|表示|称|指出|强调|谴责|警告|否认|承认|呼吁|要求|敦促|"
         r"提议|提出|发表.{0,4}(演讲|讲话|致辞|发言|演说|谈话|宣言)|"
         r"演讲|讲话|致辞|发言|宣布|下令|指示|讨论|"
         r"新闻发布会|发布会|记者会|宣言"
     )),
)

# 场景判定的线索
SETTING_CUES = (
    ("记者会", re.compile(r"记者会|新闻发布会|发布会|记者招待会")),
    ("视察", re.compile(r"视察|检查|走访|考察|慰问|探望")),
    ("访问", re.compile(r"访问|出访|到访|国事访问")),
    ("会谈", re.compile(r"会谈|会晤|磋商|会见|通话|通电话")),
    ("谈判", re.compile(r"谈判|和谈|停火谈判|磋商")),
    ("演习", re.compile(r"演习|演练|军演|实弹")),
    ("战斗", re.compile(r"战斗|交火|激战|巷战|空袭|袭击|打击|轰炸|炮击|遭袭")),
    ("会议", re.compile(r"会议|峰会|论坛|大会|例会|国会")),
    ("审判", re.compile(r"审判|开庭|庭审|判决|起诉")),
    ("选举", re.compile(r"选举|投票|公投|大选")),
    ("签署", re.compile(r"签署|签订|缔约")),
    ("部署", re.compile(r"部署|增派|派驻|进驻|调动")),
    ("巡逻", re.compile(r"巡逻|巡航|侦察|监视|警戒")),
    ("救援", re.compile(r"救援|搜救|救助|赈灾|撤侨")),
)

# 立场判定
STANCE_CUES = (
    ("谴责", re.compile(r"谴责")),
    ("警告", re.compile(r"警告|威胁")),
    ("否认", re.compile(r"否认|驳斥|否认称")),
    ("承认", re.compile(r"承认|证实|确认")),
    ("呼吁", re.compile(r"呼吁|敦促")),
    ("要求", re.compile(r"要求|责令|勒令")),
    ("反对", re.compile(r"反对|拒绝|抗议")),
    ("支持", re.compile(r"支持|声援|力挺")),
    ("关切", re.compile(r"关切|担忧|忧虑")),
)

PERSON_ATTRS = (
    "别名", "性别", "出生日期", "籍贯", "出生地", "国籍", "民族", "宗教",
    "社会评价", "身份/职业", "重大事件参与记录", "社会关系", "主要观点",
    "工作经历", "教育经历", "近期活动", "公开行程", "奖惩记录/作品", "意识形态",
)

# ---------------- 提示 ----------------

SYSTEM_ZH = """你从新闻中抽取事件、实体和它们之间的关系。只使用原文，不要编造。
实体只有六类：事件、机构、人物、装备、地点、作品。平台不是实体，由文档元数据提供。
关系只表示语义角色，动作类型写在事件上。
所有角色关系都以事件为起点。
输出一个 JSON 对象，不要 Markdown 围栏。"""

USER_ZH = """从下面新闻块抽取。所有取值必须是标题或正文中的连续子串。

一、事件
一个事件是「谁做了什么」的完整陈述，必须有明确施动者和一个动作。

每条事件先自问两个问题，都答得出才写：
- 谁做的？（施动者，必须能在 entities 里找到）
- 做了什么？（一个具体动作）

正例：「俄军当天凌晨对基辅发动空袭」——施动者是俄军，动作是发动空袭。
反例：「造成3名军人受伤」——没有施动者，是上一句的结果，不要单独成事件。
反例：「空袭」「撤离」「抵达」——只有动作，没有施动者。
反例：「导致利雅得机场运行受到影响」——结果从句，不要单独成事件。
反例：「提升作战能力」「阻止该图谋得逞」——目的从句，不要单独成事件。

事件文本要自带施动者。不要把施动者留在事件外面靠关系补：
反例：事件写「强行部署远程导弹」，另用「施事」连到日本。应当写成「日本强行部署远程导弹」。
反例：事件写「已准备好对伊朗进行新一轮大规模打击」，应当写成「美国军方已准备好对伊朗进行新一轮大规模打击」。

条件句与推测句不算事件：
反例：「如果美国和以色列发动新一轮打击」不是事件，它是条件。
反例：「特朗普可能在11月中期选举后下达指令」可以保留，但 kind 必须是计划，且 event_type 是其他。
只陈述已发生、正在发生、或明确计划的动作。

每条事件必须给两个标签：
1. event_type，从这些里选一个：{event_types}
   看事件本身做了什么。「美军空袭伊朗」是空袭，「伊朗否认美军空袭」是声明。

   各类的判据：
   - 空袭：用航空器投弹或扫射。地面炮击、导弹袭击归打击。
   - 打击：袭击、攻击、突击、交火、作战、政变、政权更迭、造成伤亡。
   - 拦截：击落、击毁、防空作战。
   - 发射：发射、试射。
   - 部署：部署、增派、调动、进驻、起飞、巡航、交付、移交、组建、调整编制。
   - 撤离：撤出、撤退、返航。
   - 演习：演练、训练、实弹测试、联演、军事考察、筹备评估。
   - 控制：占领、攻占、接管、封锁、包围、隔离、宵禁、管控。
   - 签署：签署、缔结、达成协议或共识、加入、退出、批准、合作。
   - 谈判：谈判、和谈、磋商、对话、会谈、会晤、斡旋。
   - 会议：会议、峰会、论坛、例会、仪式、庆典、选举、任命、集会抗议。
   - 访问：访问、出访、到访、抵达、接见、视察。
   - 逮捕：逮捕、拘押、起诉、审判、判决。
   - 援助：军援、支援、救济、提供物资或资金。
   - 声明：声明、表示、称、谴责、警告、否认、呼吁、演讲、讲话、发布会。

   两条消歧规则：
   - kind 为表态时，event_type 一律填声明。转述内容里出现的空袭、部署等动作属于被转述方，不算本次事件的动作类型。
   - 签署是已达成的结果，谈判是正在进行的交涉过程。

2. setting，从这些里选一个：{settings}
   看事件在什么场合发生。「在记者会上发表声明」是记者会，不是声明。
   两个标签分不清时都填「其他」，不要留空。

事件还要标 kind：
- 发生：已发生或正在发生
- 表态：口头口径，如称、表示、谴责、警告、否认
- 计划：将、拟、考虑、讨论、准备，尚未发生

反例：「美军必须撤离该地区」「各方应保持克制」是表态，不是发生。
反例：「考虑派出第84师」是计划，不是发生。

二、实体
只抽这五类，且每个实体都要通过关系或事件挂到图上：
- 机构：政府、军队、部门、国际组织，国家也算机构
- 人物：有姓名的公开身份
- 装备：武器、系统、舰船、飞机。泛称如「无人机」「导弹」只在原文无具体型号时才写
- 地点：地名、海域、基地
- 作品：有正式题名的协议、条约、法案、报告、影像

职务、官衔、职业称呼是人物的一部分，不是机构：「美国总统」「俄军总参谋长」「国防部长」「联合国秘书长」「以色列总理」都要按人物抽；只有后面接机关后缀的才是机构，如「总统府」「总统办公室」「国防部」「联合国秘书处」。
一个人只抽一个实体：原文出现「美国总统特朗普」时，抽「特朗普」为人物，不要再单独抽「美国总统」。

事件不是实体，写在上面的 events 段里，不要放进 entities。
不要抽平台（媒体名、站点、账号），它由文档元数据提供。
不要抽观点、立场、场景，它们不是实体。

记者、通讯员、发言人、消息人士、观察员不是事件施动者，不要作为角色关系的一端。

三、关系
起点终点都必须出现在 entities 中且类型相符，方向必须按下面来。

角色关系（以事件为起点）：
施事 事件→机构/人物（谁发起或执行）
受事 事件→机构/人物/地点（谁被打击、被控制、被波及）
到场 事件→人物（谁出席、参加，但不是施事）
使用 事件→装备（事件中动用了什么装备）
涉及 事件→作品（事件关联哪份文书）
表态 事件→机构/人物（谁就此事表态，边带 stance）
包含 事件→事件（复合事件包含原子事件，只在概括层用）

结构关系（实体与实体之间，只收稳定关系，不随单次事件变化）：
隶属 机构→机构（上级机关）
辖属 机构→机构（国别归属，终点是国家）
驻地 机构→地点（常驻位置）
部署方 装备→机构（装备由谁部署）
去向 装备→地点（装备实际移动到该地）
任职 人物→机构（原文写明职务与机构，如「国防部长赫格塞思」）
会晤 人物→人物（面对面或通话，如「特朗普与塞西举行会谈」）
搭载 装备→装备（载体与载荷，如「台风战机搭载流星导弹」）
位于 地点→地点（行政或地理包含，如「塔伊兹省位于也门」）
敌对 机构→机构（交火、宣战、明确对抗，如「以军与哈马斯交火」）

表态边的 stance 从这些里选：{stances}。原文没有明确态度词就不写这条边。

计划中的动作不写使用、部署方、去向三条边。

五条结构关系的边界：
任职只看原文是否写明职务与机构的对应关系，「曾在该机构工作」不算任职。
会晤要求双方都是人物，且是直接会面或通话；一方为机构时归到事件。
搭载的两个端点都必须是装备，人员搭乘不算。
位于要求两个端点都是地点，且是稳定的行政或地理关系；「部队位于某地」是驻地不是位于。
敌对要求两端都是机构，且有明确的对抗事实；仅有口头交锋归到表态。

四、容易错的地方
「空袭」和「打击」的区别在 event_type，不要造两个关系名。
「日本强行部署远程导弹」应写成：事件=强行部署远程导弹，施事=日本。不要把主语重复写进事件。
「英国海军会合了邓肯号」不是实施，是事件的受事或到场。
「美军向基地发射导弹」：事件类型是发射，不是打击。
「摧毁380辆军用车辆」不是拦截，事件类型是打击。
装备被摧毁、被拦截不是「使用」；装备只是被提及、统计、列装也不算。

五、正反例
正例：「以军在加沙地带空袭」→ 事件=空袭（type=空袭），施事=以军，受事=加沙地带（若无具体目标）
反例：不要把「加沙地带」标成场景或事件。
正例：「双方签署《吉达停火协议》」→ 事件=签署《吉达停火协议》（type=签署），涉及=《吉达停火协议》
反例：不要把「停火」「协议」单独标成作品。
正例：「伊朗外交部发言人称美方指控不实」→ 事件=称美方指控不实（type=声明，kind=表态），表态=伊朗外交部发言人（stance=否认）
反例：不要写成「伊朗外交部发言人 施事 称美方指控不实」。

人物要素名只能是：{attrs}。姓名用 entities 里的人物 text。

JSON：
{{
  "entities": [{{"type": "机构|人物|装备|地点|作品", "text": "原文片段"}}],
  "events": [{{"text": "事件原文片段", "event_type": "上面的枚举之一", "setting": "上面的枚举之一", "kind": "发生|表态|计划"}}],
  "relations": [{{"name": "关系名", "head": "起点原文", "tail": "终点原文", "evidence": "支撑该边的原句", "stance": "仅表态边需要"}}],
  "persons": [{{"name": "人物姓名原文", "attrs": {{"别名": "", "性别": ""}}}}]
}}

新闻块：
{block}
"""

SYSTEM_EN = """Extract events, entities, and role relations from news text. Use only the given text.
Entities are only these six types: 事件, 机构, 人物, 装备, 地点, 作品. Platforms are not entities.
Relations express semantic roles only; the action type lives on the event.
All role relations start from an event.
Return one JSON object, no Markdown fence."""

USER_EN = """Extract from the news block. Every value must be a contiguous substring of the title or body.

1. Events
An event is a complete statement of who did what, with an explicit agent and one action.

Ask two questions before writing an event; both must be answerable:
- Who did it? The agent must also appear in entities.
- What was done? One concrete action.

Positive: "Russian forces launched an airstrike on Kyiv early today" — agent Russian forces, action launched.
Negative: "three soldiers were wounded" — no agent, it is the result of the previous sentence.
Negative: "airstrike", "withdrawal", "arrival" — action only, no agent.
Negative: "causing flight operations at Riyadh airport to be affected" — result clause.

The event text must carry its own agent. Do not leave the agent outside and reconnect it with a relation:
Negative: event "deployed long-range missiles", agent attached separately. Write "Japan deployed long-range missiles".
Negative: event "is ready for a new round of strikes". Write "US military is ready for a new round of strikes".

Conditional and speculative clauses are not events:
Negative: "if the US and Israel launch a new round of strikes" is a condition.
Negative: "Trump may order the operation after the midterms" is acceptable only with kind 计划 and event_type 其他.
State only what happened, what is happening, or what is explicitly planned.

Every event carries two labels:
1. event_type, one of: {event_types}
   Judge what the event itself did. "US struck Iran" is 空袭; "Iran denied the US strike" is 声明.

   Cues for each type:
   - 空袭: aircraft dropping bombs or strafing. Ground shelling and missile strikes are 打击.
   - 打击: attack, assault, clash, combat, coup, regime change, casualties.
   - 拦截: shoot down, intercept, air defence.
   - 发射: launch, test-fire.
   - 部署: deploy, reinforce, reposition, sortie, patrol flight, deliver, hand over, stand up a unit, reorganize.
   - 撤离: withdraw, retreat, return to base.
   - 演习: drill, exercise, live-fire test, joint exercise, military survey, preparation and assessment.
   - 控制: occupy, capture, take over, blockade, encircle, isolate, curfew, control.
   - 签署: sign, conclude, reach agreement or consensus, join, exit, approve, cooperate.
   - 谈判: negotiate, peace talks, consult, dialogue, meeting, summit talk, mediate.
   - 会议: conference, summit, forum, plenary, ceremony, celebration, election, appointment, rally and protest.
   - 访问: visit, state visit, arrive, receive, inspect.
   - 逮捕: arrest, detain, prosecute, try, sentence.
   - 援助: military aid, support, relief, supply goods or funds.
   - 声明: statement, say, condemn, warn, deny, appeal, speech, address, press briefing.

   Two disambiguation rules:
   - When kind is 表态, set event_type to 声明. Actions quoted inside the statement belong to the quoted party, not to this event.
   - 签署 is the result reached; 谈判 is the process under way.

2. setting, one of: {settings}
   Judge the occasion. "spoke at a press briefing" is 记者会, not 声明.
   Use 其他 when neither fits. Never leave blank.

kind is 发生 (happened), 表态 (verbal stance), or 计划 (planned).
Negative: "forces must withdraw" is 表态, not 发生.

2. Entities
Five types only: 机构, 人物, 装备, 地点, 作品.
Events are not entities: write them in the events section, never in entities.
Do not extract platforms (outlet names, sites, accounts); they come from document metadata.
Do not extract claims, stances, or settings as entities.
Reporters, spokespeople, and sources are not agents; never use them as a relation endpoint.

Job titles and honorifics belong to the person, not to an organization: "美国总统" (US president), "俄军总参谋长", "国防部长", "联合国秘书长", "以色列总理" are 人物. Only forms carrying an institutional suffix are 机构, e.g. "总统府", "总统办公室", "国防部".
One person, one entity: when the text says "美国总统特朗普", extract 特朗普 as 人物 and do not also extract "美国总统".

3. Relations
Both endpoints must appear in entities with matching types.

Role relations, starting from an event:
施事 event→org/person (who initiated or carried out)
受事 event→org/person/place (who was struck, controlled, or affected)
到场 event→person (attended but did not act)
使用 event→equipment (equipment employed in the event)
涉及 event→work (document referenced by the event)
表态 event→org/person (who took a stance; carries stance)
包含 event→event (composite contains atom, summary layer only)

Structural relations, entity to entity, stable across single events:
隶属 org→org (parent body)
辖属 org→org (national affiliation, target is a state)
驻地 org→place (station)
部署方 equipment→org (who deployed it)
去向 equipment→place (where it moved)
任职 person→org (the text states the post and the organization, e.g. "Defense Secretary Hegseth")
会晤 person→person (direct meeting or call, e.g. "Trump met el-Sisi")
搭载 equipment→equipment (carrier and payload, e.g. "Typhoon carries Meteor missiles")
位于 place→place (administrative or geographic containment, e.g. "Taiz is in Yemen")
敌对 org→org (clashes, declared hostility, e.g. "the IDF fought Hamas")

stance is one of: {stances}. Omit the edge when the text states no explicit attitude.
Planned actions produce no 使用, 部署方, or 去向 edges.

Boundaries for the five structural relations:
任职 requires an explicit post-to-organization statement; "once worked there" does not count.
会晤 requires both ends to be persons in a direct meeting or call; if one side is an organization, use an event instead.
搭载 requires both ends to be equipment; personnel riding along does not count.
位于 requires both ends to be places and a stable administrative or geographic relation; a unit's location is 驻地.
敌对 requires both ends to be organizations and a concrete confrontation; verbal exchanges alone belong to 表态.

4. Easy mistakes
The difference between 空袭 and 打击 lives in event_type, not in two relation names.
"The UK navy rendezvoused with HMS Duncan" is not 施事; the ship is 受事 or 到场.
Destroying 380 vehicles is not 拦截; event_type is 打击.
Equipment being destroyed or intercepted is not 使用.

5. Examples
Positive: "Israel struck Gaza" → event type 空袭, 施事 Israel, 受事 Gaza.
Positive: "the two sides signed the Jeddah ceasefire agreement" → event type 签署, 涉及 the agreement.
Positive: "Iran's foreign ministry spokesman called the US claim false" → event type 声明, kind 表态, 表态 spokesman with stance 否认.
Negative: do not write "spokesman 施事 called the claim false".

Person attribute names must be: {attrs}. The person's name is the 人物 entity text.

JSON:
{{
  "entities": [{{"type": "机构|人物|装备|地点|作品", "text": "source span"}}],
  "events": [{{"text": "event span", "event_type": "one of the enum values", "setting": "one of the enum values", "kind": "发生|表态|计划"}}],
  "relations": [{{"name": "relation", "head": "source span", "tail": "source span", "evidence": "supporting sentence", "stance": "for 表态 only"}}],
  "persons": [{{"name": "name span", "attrs": {{"别名": "", "性别": ""}}}}]
}}

News block:
{block}
"""


# ---------------- 判定函数 ----------------


def is_non_actor(name: str) -> bool:
    """报道者或转述者，不是事件施动者。"""
    t = (name or "").strip()
    if not t or not ROLE_TAIL.search(t):
        return False
    prefix = t[: -2] if len(t) > 2 else ""
    if not prefix:
        return True
    probe = PREFIX_OK_LEAD.sub("", prefix)
    if PREFIX_BAD.search(probe):
        return False
    if len(prefix) > 16:
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9·]+", prefix))


# ---------------- 职务串：指人，不指机关 ----------------

# 官职与军衔。命中后按人物处理，不再算机构。
TITLE_WORDS = (
    "副总统", "总统", "副总理", "总理", "首相", "副主席", "主席",
    "总书记", "第一书记", "书记", "副部长", "部长", "次长",
    "外交部长", "外长", "国防部长", "防长", "总参谋长", "参谋长",
    "总司令", "司令", "指挥官", "司令官",
    "大使", "公使", "领事", "参赞", "武官", "议员", "议长",
    "州长", "省长", "市长", "县长", "区长", "镇长",
    "局长", "署长", "厅长", "司长", "处长", "科长",
    "主任", "检察长", "总检察长", "院长", "审判长",
    "秘书长", "副秘书长", "秘书", "助理", "发言人", "特使",
    "代表", "专员", "行长", "总裁", "董事长",
    "领导人", "元首", "领袖", "头目",
)

# 职务词之后接这些后缀，说明指的是机关而不是人：总统府、总理办公室、司令部
ORG_TAIL = re.compile(
    r"(?:办公室|办公厅|委员会|主席团|秘书处|指挥部|司令部|会议|大会|"
    r"府|部|局|院|署|厅|处|司|科|中心|大楼|大厦|机关|机构|总部|馆|所|站|团|组)$"
)

# 职务词之后接这些，说明指的是文书：总统令、总统法
WORK_TAIL = re.compile(r"^(?:令|法|案|条例|法案|命令|公告|声明|决议|文件|草案)")


def _title_end(name: str) -> int:
    """最后一个职务词的结束位置；无职务词返回 0。"""
    end = 0
    for w in TITLE_WORDS:
        i = name.rfind(w)
        if i >= 0:
            end = max(end, i + len(w))
    return end


def title_span_kind(name: str) -> str:
    """职务串判定：'' 不是；'title' 纯职务；'title_name' 职务加姓名。"""
    t = (name or "").strip()
    if not t or len(t) > 24:
        return ""
    if not re.fullmatch(r"[\u4e00-\u9fff·A-Za-z ]+", t):
        return ""
    end = _title_end(t)
    if not end:
        return ""
    rest = t[end:].strip()
    if rest and ORG_TAIL.search(rest):
        return ""
    if rest and WORK_TAIL.match(rest):
        return ""
    if not rest:
        return "title"
    if len(rest) <= 6:
        return "title_name"
    return ""


def title_name_part(name: str) -> str:
    """职务串里跟在职务后面的姓名；没有返回空串。"""
    t = (name or "").strip()
    end = _title_end(t)
    if not end or end >= len(t):
        return ""
    rest = t[end:].strip()
    if not rest or ORG_TAIL.search(rest) or WORK_TAIL.match(rest):
        return ""
    return rest if len(rest) <= 6 else ""


def guess_event_type(text: str) -> str:
    for name, rx in TYPE_CUES:
        if rx.search(text or ""):
            return name
    return "其他"


def guess_setting(text: str) -> str:
    for name, rx in SETTING_CUES:
        if rx.search(text or ""):
            return name
    return "其他"


def guess_stance(text: str) -> str:
    for name, rx in STANCE_CUES:
        if rx.search(text or ""):
            return name
    return ""


# 已完成时态标记
DONE_MARK = re.compile(r"已(?!经?将)|了|当天|当日|完成|成功|结束|正在")
# 转述标记：这些词一旦出现，说明句子在引述他人言行
REPORT_CUE = re.compile(
    r"称|表示|声明|指出|强调|否认|承认|警告|谴责|呼吁|敦促|"
    r"援引|报道称|发文说|撰文|接受采访时|在.{0,6}上说|发布消息"
)


# 推测与传闻：不是已发生的事实
UNCERTAIN = re.compile(r"很可能|可能由|疑为|疑似|据称|据悉|据传|传闻|有待证实|尚不确定|推测")
# 宣布类动作：宣布一项决定，属于表态
DECLARE_ACT = re.compile(r"^(?:[^，。]{0,16})?(?:宣布|宣告|通告|公布|发布)(?!.*(?:已|了).*(?:实施|执行))")


def is_reported_speech(text: str) -> bool:
    """判断句子是否为转述他人言行或不确定信息。

    转述的特征：出现「称/表示/声明」等标记，且标记前有转述主体。
    宣布类动作（宣布实施禁运）也归表态。
    推测与传闻归表态。
    """
    t = (text or "").strip()
    if not t:
        return False
    if UNCERTAIN.search(t):
        return True
    if DECLARE_ACT.search(t):
        return True
    m = REPORT_CUE.search(t)
    if not m:
        return False
    if m.start() <= 2:
        return True
    lead = t[: m.start()]
    if re.match(r"^(?:当天|当日|近日|日前|当地时间|昨日|今天|随后|此前|稍早)[，,]?", lead):
        lead = re.sub(r"^(?:当天|当日|近日|日前|当地时间|昨日|今天|随后|此前|稍早)[，,]?", "", lead)
    return len(lead.strip()) >= 2


def guess_kind(text: str) -> str:
    """判定话语种类。

    优先级：
    1. 转述他人言行 → 表态
    2. 明确的计划标记且无完成标记 → 计划
    3. 规范句（必须、应当）→ 表态
    4. 其余 → 发生
    """
    t = text or ""
    if is_reported_speech(t):
        return "表态"
    planned = bool(PLAN_CUE.search(t))
    done = bool(DONE_MARK.search(t))
    if planned and not done:
        return "计划"
    if NORMATIVE.search(t):
        return "表态"
    return "发生"


def planned_evidence(evidence: str) -> bool:
    ev = evidence or ""
    if not ev:
        return False
    return bool(PLAN_CUE.search(ev)) and not DONE_CUE.search(ev)


# 事件文本开头的时间状语与介词短语，判断施动者前先剥掉
LEAD_NOISE = re.compile(
    r"^(?:"
    r"\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|\d{1,2}日"
    r"|当天|当日|近日|日前|近期|目前|同日|随后|此前|稍早|今晨|昨晨"
    r"|当地时间|\d{1,2}时\d{1,2}分|上午|下午|晚間|晚间|清晨|凌晨|中午"
    r"|今|昨|今早|昨晚|今年以来|自\d{4}年"
    r")(?:[至到以]?[来后]?)?\s*[，,、]?\s*"
)


def has_agent(text: str, entities: set[str]) -> bool:
    """事件文本里是否出现施动者（已知实体名）。

    先剥掉开头的时间状语，再在句首一段内查找实体名。
    """
    t = (text or "").strip()
    if not t:
        return False
    for _ in range(2):
        t2 = LEAD_NOISE.sub("", t)
        if t2 == t:
            break
        t = t2
    for name in entities:
        n = (name or "").strip()
        if len(n) < 2:
            continue
        if t.startswith(n):
            return True
        if len(n) >= 3 and n in t[: len(n) + 12]:
            return True
    return False


def is_atom_event(text: str) -> tuple[bool, str]:
    """判断是否为合格原子事件，返回（是否合格, 原因）。

    计划类事件同样成立：「联军将于近期撤离该基地」是有效的原子事件，
    只是 kind 标为计划。不应因时态被丢弃。
    """
    t = (text or "").strip()
    if not t:
        return False, "empty"
    if len(t) < ATOM_MIN:
        return False, "too_short"
    if len(t) > ATOM_MAX:
        return False, "too_long"
    if CONDITIONAL.search(t):
        return False, "conditional"
    if re.search(r"的$", t):
        return False, "noun_phrase"
    if not PREDICATE.search(t):
        return False, "no_verb"
    return True, ""


# ---------------- 切块与提示组装 ----------------


def pieces(text: str, limit: int = 3500) -> list[str]:
    """按句切块，长稿不截断。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    parts = [p.strip() for p in re.split(r"(?<=[。！？.!?])\s+", text) if p.strip()]
    if len(parts) <= 1:
        return [text[i : i + limit] for i in range(0, len(text), limit)]
    groups: list[str] = []
    buf: list[str] = []
    n = 0
    for part in parts:
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


def build_prompt(doc: NewsDoc, chunk: str) -> tuple[str, str]:
    block = f"【标题】{doc.title}\n【发稿时间】{doc.publish_time}\n【正文】\n{chunk}"
    attrs = "、".join(PERSON_ATTRS)
    if (doc.lang or "").startswith("en"):
        return SYSTEM_EN, USER_EN.format(
            event_types=EVENT_TYPES_STR, settings=SETTINGS_STR,
            stances=STANCES_STR, attrs=attrs, block=block,
        )
    return SYSTEM_ZH, USER_ZH.format(
        event_types=EVENT_TYPES_STR, settings=SETTINGS_STR,
        stances=STANCES_STR, attrs=attrs, block=block,
    )


def _keep_span(text: str, hay: str) -> str:
    t = (text or "").strip()
    if t and locate_span(t, hay):
        return t
    return ""


# ---------------- 校验 ----------------


def check_block(parsed: dict, hay: str) -> dict:
    """块内校验：片段、类型、枚举、角色约束。"""
    parsed = parsed if isinstance(parsed, dict) else {}
    dropped = Counter()

    # 事件：先建表，因为角色关系的起点必须是事件
    events: dict[str, dict] = {}
    raw_events = parsed.get("events") if isinstance(parsed.get("events"), list) else []
    for item in raw_events:
        if not isinstance(item, dict):
            dropped["event"] += 1
            continue
        text = _keep_span(str(item.get("text") or ""), hay)
        if not text:
            dropped["event_span"] += 1
            continue
        ok, why = is_atom_event(text)
        if not ok:
            dropped[f"event_{why}"] += 1
            continue
        etype = str(item.get("event_type") or "").strip()
        setting = str(item.get("setting") or "").strip()
        kind = str(item.get("kind") or "").strip()
        if kind not in EVENT_KINDS:
            kind = guess_kind(text)
            dropped["kind_guessed"] += 1
        # 表态类事件的动作类型固定为声明：转述内容里的动作属于被转述方
        if kind == "表态":
            etype = "声明"
        elif etype not in EVENT_TYPES or etype == "其他":
            guess = guess_event_type(text)
            if guess != "其他":
                etype = guess
                dropped["event_type_guessed"] += 1
            elif etype not in EVENT_TYPES:
                etype = "其他"
                dropped["event_type_default"] += 1
        if setting not in SETTINGS or setting == "其他":
            guess = guess_setting(text)
            if guess != "其他":
                setting = guess
                dropped["setting_guessed"] += 1
            elif setting not in SETTINGS:
                setting = "其他"
                dropped["setting_default"] += 1
        events[text] = {
            "text": text, "event_type": etype, "setting": setting, "kind": kind,
        }

    # 实体：职务串按人物处理，带姓名的职务串并到人名
    raw_ents: dict[str, set[str]] = {}
    for ent in (parsed.get("entities") if isinstance(parsed.get("entities"), list) else []):
        if not isinstance(ent, dict):
            dropped["entity"] += 1
            continue
        typ = str(ent.get("type") or "").strip()
        text = _keep_span(str(ent.get("text") or ""), hay)
        if not text:
            dropped["entity"] += 1
            continue
        if typ == "事件":
            # 事件不作为实体收进 entities；以 events 段为正本
            if text not in events:
                dropped["entity_event_mismatch"] += 1
            continue
        if typ not in ENTITY_TYPES:
            dropped["entity"] += 1
            continue
        # 职务、官衔指人：总统、总参谋长、国防部长等都是人物
        if typ == "机构" and title_span_kind(text):
            typ = "人物"
            dropped["entity_title_to_person"] += 1
        raw_ents.setdefault(text, set()).add(typ)

    # 职务串里的姓名已单独成为人物时，职务串并入该人物，关系端点一并改写
    alias: dict[str, str] = {}
    for text in list(raw_ents):
        if title_span_kind(text) != "title_name":
            continue
        name = title_name_part(text)
        if len(name) < 2 or name == text:
            continue
        if "人物" not in raw_ents.get(name, set()):
            # 姓名本身没被抽出来时补一条，保证一人一节点
            if locate_span(name, hay):
                raw_ents.setdefault(name, set()).add("人物")
                dropped["entity_name_filled"] += 1
            else:
                continue
        raw_ents[name] |= raw_ents.pop(text)
        alias[text] = name
        dropped["entity_title_alias"] += 1

    # 类型优先级：机构 > 地点 > 人物 > 装备 > 作品
    TYPE_PRIORITY = {"机构": 5, "地点": 4, "人物": 3, "装备": 2, "作品": 1}
    entities: list[dict] = []
    for text, types in raw_ents.items():
        if len(types) > 1:
            dropped["entity_multi_type"] += 1
        typ = max(types, key=lambda x: TYPE_PRIORITY.get(x, 0))
        entities.append({"type": typ, "text": text})

    # 事件文本必须自带施动者（在实体名里找得到）
    # 职务串并入姓名后，原文里出现的仍是职务串，所以别名也算施动者
    agent_pool = set(raw_ents) | set(alias)
    filtered_events: dict[str, dict] = {}
    for text, ev in events.items():
        if has_agent(text, agent_pool):
            filtered_events[text] = ev
        else:
            dropped["event_no_agent"] += 1
    events = filtered_events

    types_of: dict[str, set[str]] = {}
    for ent in entities:
        types_of.setdefault(ent["text"], set()).add(ent["type"])
    for text in events:
        types_of.setdefault(text, set()).add("事件")

    # 被过滤掉的事件：模型可能在关系里引用它们，这些关系一并丢弃
    rejected_events: set[str] = set()
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if text and text not in events:
            rejected_events.add(text)

    # 关系
    relations: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for rel in (parsed.get("relations") if isinstance(parsed.get("relations"), list) else []):
        if not isinstance(rel, dict):
            dropped["relation"] += 1
            continue
        name = str(rel.get("name") or "").strip()
        head = _keep_span(str(rel.get("head") or ""), hay)
        tail = _keep_span(str(rel.get("tail") or ""), hay)
        evidence = _keep_span(str(rel.get("evidence") or ""), hay)
        # 职务串已并入姓名时，端点跟着换成姓名
        head = alias.get(head, head)
        tail = alias.get(tail, tail)
        if name not in DOMAIN or not head or not tail or not evidence:
            dropped["relation"] += 1
            continue
        if head in rejected_events or tail in rejected_events:
            dropped["relation_rejected_event"] += 1
            continue
        if head not in types_of or tail not in types_of:
            dropped["relation_untyped_end"] += 1
            continue
        want_head, want_tail = DOMAIN[name]
        if not (types_of[head] & want_head) or not (types_of[tail] & want_tail):
            dropped["relation_type_mismatch"] += 1
            continue
        # 非施动者不能作为施事、受事
        if name in ("施事", "受事") and is_non_actor(tail if name == "受事" else head):
            dropped["relation_non_actor"] += 1
            continue
        if name == "表态" and is_non_actor(tail):
            dropped["stance_non_actor"] += 1
            continue
        # 计划动作不产生位置与使用类边
        if name in PLANNED_FORBIDDEN:
            kind = events.get(head, {}).get("kind") or events.get(tail, {}).get("kind") or ""
            if kind == "计划" or planned_evidence(evidence):
                dropped["relation_planned"] += 1
                continue
        rec = {"name": name, "head": head, "tail": tail, "evidence": evidence}
        if name == "表态":
            stance = str(rel.get("stance") or "").strip()
            if stance not in STANCES:
                stance = guess_stance(evidence) or guess_stance(tail)
            if stance:
                rec["stance"] = stance
            else:
                dropped["stance_missing"] += 1
        key = (name, head, tail)
        if key in seen:
            dropped["relation_dup"] += 1
            continue
        seen.add(key)
        relations.append(rec)

    # 隶属与辖属不双向
    for a, b in (("隶属", "隶属"), ("辖属", "辖属")):
        pairs: dict[tuple[str, str], list[dict]] = {}
        for rel in relations:
            if rel["name"] == a:
                pairs.setdefault((rel["head"], rel["tail"]), []).append(rel)
        drop_ids: set[int] = set()
        for (h, t), fwd in pairs.items():
            rev = pairs.get((t, h))
            if not rev:
                continue
            best = max(fwd + rev, key=lambda r: len(r.get("evidence") or ""))
            for rel in fwd + rev:
                if rel is not best:
                    drop_ids.add(id(rel))
        if drop_ids:
            dropped["struct_bidirectional"] += len(drop_ids)
            relations = [r for r in relations if id(r) not in drop_ids]

    # 人物
    persons: list[dict] = []
    for p in (parsed.get("persons") if isinstance(parsed.get("persons"), list) else []):
        if not isinstance(p, dict):
            dropped["person"] += 1
            continue
        name = _keep_span(str(p.get("name") or ""), hay)
        if not name:
            dropped["person"] += 1
            continue
        attrs_in = p.get("attrs") if isinstance(p.get("attrs"), dict) else {}
        attrs = {k: _keep_span(str(attrs_in.get(k) or ""), hay) for k in PERSON_ATTRS}
        persons.append({"name": name, "attrs": attrs})

    # 孤立实体：既没进关系也没作为事件端点的，丢弃
    used: set[str] = set()
    for rel in relations:
        used.add(rel["head"])
        used.add(rel["tail"])
    kept_entities = [e for e in entities if e["text"] in used]
    dropped["entity_isolated"] += len(entities) - len(kept_entities)

    return {
        "events": list(events.values()),
        "entities": kept_entities,
        "relations": relations,
        "persons": persons,
        "dropped": dict(dropped),
    }



# ---------------- 稿件级抽取 ----------------


def merge_blocks(blocks: list[dict]) -> dict:
    """合并多块结果，按事件文本与三元组去重。"""
    events: dict[str, dict] = {}
    entities: dict[tuple[str, str], dict] = {}
    relations: set[tuple[str, str, str, str]] = set()
    persons: dict[str, dict] = {}
    dropped = Counter()
    for b in blocks:
        dropped.update(b.get("dropped") or {})
        for ev in b.get("events") or []:
            events.setdefault(ev["text"], ev)
        for ent in b.get("entities") or []:
            entities.setdefault((ent["type"], ent["text"]), ent)
        for rel in b.get("relations") or []:
            relations.add((rel["name"], rel["head"], rel["tail"], rel.get("stance") or ""))
        for p in b.get("persons") or []:
            slot = persons.get(p["name"])
            if slot is None:
                persons[p["name"]] = p
            else:
                for k, v in p["attrs"].items():
                    if v and not slot["attrs"].get(k):
                        slot["attrs"][k] = v
    rel_list = [
        {"name": n, "head": h, "tail": t, **({"stance": s} if s else {})}
        for n, h, t, s in relations
    ]
    # 关系补回 evidence：从块里找第一条带该三元组的
    ev_map: dict[tuple[str, str, str], str] = {}
    for b in blocks:
        for rel in b.get("relations") or []:
            ev_map.setdefault((rel["name"], rel["head"], rel["tail"]), rel.get("evidence") or "")
    for rel in rel_list:
        rel["evidence"] = ev_map.get((rel["name"], rel["head"], rel["tail"]), "")
    return {
        "events": list(events.values()),
        "entities": list(entities.values()),
        "relations": rel_list,
        "persons": list(persons.values()),
        "dropped": dict(dropped),
    }


def extract_doc(doc: NewsDoc) -> dict:
    """单篇抽取：块内联合抽取 + 合并。复合事件另由 composite 环节处理。"""
    chunks = pieces(doc.body)
    blocks = []
    covered = 0
    errors = []
    for i, chunk in enumerate(chunks):
        covered += len(chunk)
        hay = f"{doc.title}\n{chunk}"
        system, user = build_prompt(doc, chunk)
        try:
            raw = chat_json(system, user)
            parsed = parse_llm_json(raw)
            blocks.append(check_block(parsed, hay))
        except Exception as exc:
            errors.append(f"chunk{i}: {type(exc).__name__}: {exc}")
    merged = merge_blocks(blocks) if blocks else {
        "events": [], "entities": [], "relations": [], "persons": [], "dropped": {},
    }
    extra = doc.extra or {}
    return {
        "doc_id": doc.doc_id,
        "source": doc.source,
        "source_name": doc.source_name,
        "outlet": extra.get("outlet") or doc.source_name or "",
        "domain": extra.get("domain") or "",
        "origin_source": extra.get("origin_source") or "",
        "title": doc.title,
        "publish_time": doc.publish_time,
        "url": doc.url,
        "lang": doc.lang,
        "body_chars": len(doc.body or ""),
        "covered_chars": covered,
        "n_chunks": len(chunks),
        "errors": errors,
        **merged,
    }
