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
# 论元与结构关系统一取自 extract/slots.py，本文件不再维护第二份定义
from extract.slots import (
    ACTOR_SLOTS,
    DOMAIN,
    LITERAL_SLOTS,
    PLANNED_FORBIDDEN,
    ROLE_NAMES,
    EVENT_TYPES as _EVENT_TYPES,
    SUBTYPES as _SUBTYPES,
    prompt_block,
    slot_names,
    slots_of,
)
from extract.validate import evidence_sentence, locate_span
from ingest.envelope import NewsDoc

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DATA = ROOT / "data"

# ---------------- 类型与枚举 ----------------

COUNTRY = frozenset(
    """
    中国 美国 俄罗斯 乌克兰 以色列 伊朗 伊拉克 叙利亚 也门 沙特阿拉伯 沙特
    土耳其 英国 法国 德国 日本 韩国 朝鲜 印度 巴基斯坦 阿富汗 澳大利亚 加拿大
    菲律宾 越南 泰国 印度尼西亚 马来西亚 新加坡 缅甸 孟加拉国 斯里兰卡 尼泊尔
    埃及 约旦 黎巴嫩 卡塔尔 阿联酋 阿曼 巴林 科威特 利比亚 苏丹 阿尔及利亚
    摩洛哥 突尼斯 埃塞俄比亚 肯尼亚 索马里 尼日利亚 加纳 南非 尼日尔 马里
    乍得 喀麦隆 刚果民主共和国 乌干达 坦桑尼亚 津巴布韦 莫桑比克 安哥拉
    巴西 阿根廷 智利 秘鲁 哥伦比亚 委内瑞拉 墨西哥 古巴 厄瓜多尔 玻利维亚
    巴拉圭 乌拉圭 意大利 西班牙 葡萄牙 荷兰 比利时 瑞士 瑞典 挪威 丹麦
    芬兰 波兰 捷克 斯洛伐克 匈牙利 罗马尼亚 保加利亚 希腊 塞尔维亚 克罗地亚
    奥地利 爱尔兰 白俄罗斯 格鲁吉亚 亚美尼亚 阿塞拜疆 哈萨克斯坦 乌兹别克斯坦
    土库曼斯坦 塔吉克斯坦 吉尔吉斯斯坦 蒙古 新西兰 巴勒斯坦 台湾 香港 澳门
    科索沃 摩尔多瓦 立陶宛 拉脱维亚 爱沙尼亚 斯洛文尼亚 波黑 北马其顿 阿尔巴尼亚
    """.split()
)

# 国家名落到「国家」类型，不再并入机构
def is_country(name: str) -> bool:
    return (name or "").strip() in COUNTRY

ENTITY_TYPES = ("国家", "机构", "人物", "装备", "地点", "作品")
NAME_TYPES = ("国家", "机构", "人物", "装备", "地点", "作品")
# 事件不是实体，但仍是图节点；单独统计，实体 F1 不含事件
NODE_TYPES = ENTITY_TYPES + ("事件",)

# 事件一级/二级类型取自 extract/slots.py，本文件不再定义第二份
EVENT_TYPES = _EVENT_TYPES
EVENT_TYPES_STR = "、".join(EVENT_TYPES)
SUBTYPES = _SUBTYPES
SUBTYPES_STR = "；".join(
    f"{k}：{'、'.join(v)}" for k, v in SUBTYPES.items() if v
)

# 兼容旧数据：把现有 event_type 映射到 (一级, 二级)
LEGACY_EVENT_MAP = {
    "空袭": ("对抗", "空袭"),
    "打击": ("对抗", "打击"),
    "拦截": ("对抗", "拦截"),
    "发射": ("对抗", "发射"),
    "控制": ("对抗", "控制"),
    "逮捕": ("对抗", "控制"),
    "部署": ("部署", "投送"),
    "演习": ("部署", "演习"),
    "撤离": ("部署", "撤离"),
    "签署": ("合作", "签署"),
    "谈判": ("合作", "谈判"),
    "会议": ("合作", "会议"),
    "访问": ("合作", "访问"),
    "援助": ("合作", "援助"),
    "声明": ("表态", "声明"),
    "其他": ("其他", None),   # 需按线索判断，见 guess_subtype
}

# 立场：表态边上的受控取值
STANCES = (
    "支持", "反对", "谴责", "警告", "否认",
    "承认", "呼吁", "要求", "关切", "中立",
)
STANCES_STR = "、".join(STANCES)

EVENT_KINDS = ("发生", "计划")

# 论元与结构关系统一取自 extract/slots.py，本文件不再维护第二份定义。
# 提示词、校验、标注前端都从那里取，避免口径漂移。

# 关系名表由槽位表派生，保留旧名字供下游引用
RELATIONS = ROLE_NAMES + (
    "包含",
    # 实体间结构关系，归 RE
    "隶属", "辖属", "驻地", "部署方", "去向",
    "任职", "会晤", "搭载", "位于", "敌对",
)
# 表态边的立场，挂在「表态方」边或事件节点上。
STANCE_REL = "表态方"

# 提示词里的槽位表，由 extract/slots.py 生成，避免与校验表漂移
SLOT_TABLE_STR = prompt_block()


# 原子事件长度边界
ATOM_MIN = 8
ATOM_MAX = 90

# 装备型号：字母数字组合，或「XX号」这类舰船名
EQUIP_PAT = re.compile(
    r"\b[A-Z]{1,4}[- ]?\d{1,3}[A-Z]?\b"      # F-35, MQ-9, S-400, CVN-81
    r"|[\u4e00-\u9fff]{2,6}号"                # 林肯号
)

# 这些不是装备：媒体名、机构缩写、经济指标
EQUIP_STOP = {
    "CNN", "BBC", "NHK", "NBC", "CNBC", "ABC", "CBS", "NPR", "AP", "AFP",
    "GDP", "CPI", "AI", "UN", "EU", "NATO", "AUKUS", "OPEC", "WHO",
    "ISO", "CEO", "CFO", "PM", "AM", "PM2", "USB", "PDF",
}

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
    r"视察|检查|走访|发表|表示|称|指出|强调|确认|"
    # 通用动词，覆盖减少、停止、延长、禁止、批准等非军事动作
    r"减少|增加|提高|降低|扩大|缩小|延长|推迟|取消|暂停|恢复|启动|"
    r"禁止|限制|允许|授予|提供|获得|失去|保持|维持|进入|退出|"
    r"使用|采用|装备|列装|引进|研发|研制|试验|测试|"
    r"巡航|执法|护疆|守卫|护航|驻守|驻扎|布防|集结|调动|"
    r"制裁|封锁|扣押|查获|没收|撤销|解除|免职|任命|辞职|"
    r"发生|出现|造成|导致|引发|触发|"
    r"抗议|批评|指责|抨击|敦促|倡议|号召|"
    r"结束|完成|开始|继续|推进|落实|执行|"
    r"打击|摧毁|攻克|夺取|收复|撤军|增兵|"
    r"爆炸|起火|塌陷|倾覆|沉没|失联|"
    r"发布|通报|公告|披露|透露|公布|"
    r"膨胀|扩张|增长|削减|停止|终止|中断|"
    r"击中|命中|打中|波及|涉及|牵连|"
    r"否认|驳斥|证实|核实|回应|"
    r"派出|调度|出动|前往|赶赴|驰援|"
    r"处决|处以|绞刑|枪决|判处|宣判|定罪|认定|"
    r"搜捕|抓捕|抓获|清剿|驱散|驱逐|引渡|起诉"
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
反例：事件写「强行部署远程导弹」，另用「部署方」连到日本。应当写成「日本强行部署远程导弹」。
反例：事件写「已准备好对伊朗进行新一轮大规模打击」，应当写成「美国军方已准备好对伊朗进行新一轮大规模打击」。

事件只能有一个主谓结构，即一句话只讲一个动作。以下情形必须处理：

（1）地名、国家名、机构名、装备名的枚举不算多个动作，仍是一个事件，照原样写。
正例：「俄防空系统在布良斯克州、别尔哥罗德州、莫斯科及莫斯科州、卡卢加州等多个地区拦截253架无人机」是一个事件
正例：「对丹麦、挪威、瑞典、法国、德国等多个国家的商品加征关税」是一个事件
反例：不要因为地名多就拆成多个事件，也不要因为地名多就放弃这个事件

（2）一句话里有三个以上分句、每个分句一个动作，必须拆成多个事件。
反例：「出动两架F-16、两架F-35，对上述飞机进行拦截、识别并伴飞，直至其离开」是三个动作，应拆成「出动战机」「拦截识别」「伴飞」三个事件
反例：「打击了某基地、某港口、某机场，摧毁大量装备，造成多人伤亡」应拆成打击事件与灾伤事件

（3）「X 表示/称/说/宣布，Y」这类转述结构，真正的事件是 Y，X 只作施动者或表态方。
正例：「伊拉克总理表示，国际联盟的任务将于9月结束」→ 事件是「国际联盟的任务将于9月结束」，不是整个句子
反例：不要把「某人表示」这层引述一并写进事件文本
例外：如果 Y 本身是完整转述（「X 称 Y 说 Z」），事件取最深一层的 Z

（4）数据引用与报告陈述不算事件，除非报告本身是发布行为。
反例：「某中心发布报告称，2022年至2025年乌军伤亡50万至60万」中的伤亡数字不是事件
正例：如果事件是「某中心发布报告」，则写成该发布行为，伤亡数字作为战果

条件句与推测句不算事件：
反例：「如果美国和以色列发动新一轮打击」不是事件，它是条件。
反例：「特朗普可能在11月中期选举后下达指令」可以保留，但 kind 必须是计划，且 event_type 是其他。
只陈述已发生、正在发生、或明确计划的动作。

**记者会、发布会、通气会、媒体吹风会本身不是事件。** 它们是表态发生的场景，
真正的事件是会上说了什么。不要把场景抽成事件，也不要把记者当成参会方。
正例：「国防部新闻发言人蒋斌在例行记者会上表示，菲方不能任由一小撮人肆意妄为」
  → 事件=菲方不能任由一小撮人肆意妄为（表态·声明），表态方=蒋斌，表态内容=菲方不能任由一小撮人肆意妄为
反例：不要另外抽一条「国防部举行例行记者会」的事件。
反例：不要把「记者会」「新闻发布会」归为合作·会议 —— 会议指多方就议题协商（会谈、峰会、磋商），
      记者会是单向发布信息，与会者不参与协商。
反例：不要因为文本里出现「记者」二字，就把记者或媒体写成参会方、到场人。
      到场只能写实际出席的相关人物（如发言人本人）。

每条事件除了类型标签，还要给两个字段：

trigger 触发词。事件片段里最能代表该事件动作的那个词或短语，必须逐字取自原文。
  正例：事件「以军战机当天对黎巴嫩南部发动空袭」→ trigger=空袭
  正例：事件「胡塞武装与美国达成停火协议」→ trigger=达成停火协议
  正例：事件「俄国防部称已控制苏梅州一居民点」→ trigger=控制
  反例：不要用整个事件文本当触发词，trigger 应当很短，一到六个字
  反例：不要用动词以外的成分，如施动者、时间、地点

time 事件发生时间。原文里写的时间表述，必须逐字取自原文。
  正例：「3日凌晨」「2026年1月3日」「上周三」「当地时间8日」
  正例：事件里没有时间表述，但同句或上句有（「当天」「随后」），就取那个
  反例：不要把发稿时间当作事件时间，只有原文没写任何时间时才留空
  反例：不要自己换算成日期，照原文写即可，程序会做归一

每条事件必须给两个标签，先判一级再判二级：

1. event_type 一级，从这五类里选一个：{event_types}

   **判定依据是被陈述的事实本身，不看有没有「称」「表示」「宣布」这类引述动词。**
   新闻报道几乎每句话都来自某方口径，引述动词不改变事实的性质。
   - 转述一个已发生的动作 → 按该动作归类。
     正例：「美方称空袭了伊朗」→ 对抗·打击（事实是空袭）
     正例：「科威特方面报告称遭到无人机袭击」→ 对抗·打击（事实是遭袭）
     正例：「美国中央司令部表示正执行海上封锁」→ 对抗·控制（事实是封锁）
   - 转述一个将要发生的动作 → 按该动作归类，kind=计划。
     正例：「美方宣布将增兵」→ 部署·部署，kind=计划
   - 表达态度、评价、立场，且没有陈述任何具体动作 → 表态。
     正例：「美方谴责伊朗空袭」→ 表态·谴责
     正例：「伊方否认参与袭击」→ 表态·否认
     正例：「中方呼吁各方克制」→ 表态·呼吁
   - 五类判据：

   - 对抗：双方或多方发生武力冲突、控制争夺、强制拘押。有明确的敌对方，或单方施加武力。
   - 部署：己方兵力的移动、集结、演练、撤收，无直接交火。
   - 合作：多方参与，指向共识或互助，如签约、磋商、会晤、支援。
   - 表态：只表达态度、评价或立场，不陈述具体动作。
   - 灾伤：人员伤亡、设施损毁、事故灾害。重点在后果，不在动作。
   - 治理：立法、司法、人事任免等建制内程序性行为。
   - 其他：明确不属于上述六类的。

2. subtype 二级，必须属于该一级：{subtypes}
   对抗下：打击（袭击、攻击、交火、造成伤亡）、空袭（航空器投弹或扫射）、
   拦截（击落、防空作战）、发射（发射、试射）、控制（占领、接管、封锁、拘押）
   部署下：投送（增派、派遣、进驻、集结、组建）、机动（推进、开进、调动、转场、起飞）、
   战备（进入战备等级、动员、戒备、警巡）、演习（演练、训练、联演、试射）、
   撤离（撤出、撤退、返航）
   合作下：签署（缔结、达成协议、加入、退出、批准）、谈判（磋商、对话、斡旋）、
   会议（峰会、论坛、例会、仪式）、访问（出访、到访、接见、视察、观摩、考察）、援助（军援、支援、救济）
   表态下：声明（一般性陈述、通告、宣布决定、人事任免）、呼吁（要求、敦促、倡议）、警告（威胁、划红线）、
   否认（驳斥、拒绝承认）、承认（证实、确认）、谴责（批评、抗议）
   灾伤下：伤亡（人员死伤、战果统计）、事故（坠机、爆炸、失火、泄漏、断电等非交战损毁）
   治理下：立法（法案通过、决议、批准条约）、司法（立案、起诉、审判、判决）、人事（任命、免职、辞职、提名）、
   行政（审批、禁令、制裁、查扣、接管、应急预案）

   「其他」只收技术进展、装备研制等明确不属于上述六类的。
   判不出归属时宁可归入「表态」，不要随手丢进「其他」。
   其他一级不设二级，留空。

   「美国助理国务卿」这类不是事件，不要抽。

消歧规则：
- 签署是已达成的结果，谈判是正在进行的交涉过程。
- 表态只收纯态度表达。「谴责某国空袭」是表态；「报道某国空袭」是对抗·打击。
- 部署一级的二级看动作阶段：把兵力送到某地是投送；已在移动途中是机动；
  改变戒备状态是战备；按计划演练是演习；撤走是撤离。
- 灾伤与对抗的分界：重点在「打死了多少人」这类后果统计，归灾伤·伤亡；
  重点在「谁打了谁」这个动作，归对抗。交战过程中的伤亡数字挂在对抗事件的「战果」槽位上，
  单独成条的伤亡通报归灾伤。
- 治理与表态的分界：宣布人事变动是表态·声明；议会表决通过、正式任命、立案侦查是治理。
- 判不出二级时留空，不要硬套。

事件还要标 kind：
- 发生：已发生或正在发生
- 计划：将、拟、考虑、讨论、准备，尚未发生

kind 与 event_type 正交：「宣布将增兵」是 部署·部署 + 计划；
「已经增兵」是 部署·部署 + 发生；「反对增兵」是 表态 + 发生。
反例：「美军必须撤离该地区」「各方应保持克制」是表态，不是发生。
反例：「考虑派出第84师」是 部署·部署 + 计划，不是发生。

二、实体
只抽这六类，且每个实体都要通过关系或事件挂到图上：
- 国家：主权国家与地区，如中国、美国、俄罗斯、乌克兰、以色列、伊朗、阿联酋、日本。
  国家是独立的一类，不要标成机构，也不要标成地点。
  正例：「以色列在阿联酋部署铁穹」→ 抽「以色列」「阿联酋」两个实体，类型都是国家
  正例：「美国国防部」是机构，「美国」是国家，两者不同
  反例：不要把「美国」标成机构或地点
- 机构：政府、军队、部门、国际组织。不含国家本身。
  正例：美国国防部、以色列国防军、联合国、北约、哈马斯、真主党
- 人物：有姓名的公开身份
- 装备：武器、系统、舰船、飞机
- 地点：地名、海域、基地、具体设施。军事设施要单独抽成实体，不要只抽它所在的行政区。
  正例：「加沙地带的哈马斯指挥所」→ 抽「加沙地带」「哈马斯指挥所」两个实体
  正例：「库尔斯克州的无人机工厂」→ 抽「库尔斯克州」「无人机工厂」
  正例：「贝伊克公路桥」「哈里尔空军基地」「谢里夫理工大学的燃气站」都单独抽
  反例：不要把「某州的军事设施」并成一个实体，设施与行政区是两回事
  反例：不要因为设施名很具体就不抽，打击对象与战果都依赖它
- 作品：有正式题名的协议、条约、法案、报告、影像

装备的实体名必须取自原文，不补字。分三种情况：
1. 原文写了「型号 + 通名」，取完整片段。
   正例：「F-35 战斗机」抽「F-35 战斗机」；「A-10 攻击机」抽「A-10 攻击机」
   正例：「S-400 系统」抽「S-400 系统」；「C-130 运输机」抽「C-130 运输机」
2. 原文只写型号、后面没有通名，只取型号。
   正例：「乌军 F-16 已交付」抽「F-16」
   反例：不要补成「F-16 战斗机」，实体必须是原文里连续的一段
3. 全篇没有任何型号，才抽通名。
   正例：「俄军使用无人机袭击」抽「无人机」
   反例：全篇有型号时，不要用「无人机」「导弹」代替具体型号

型号带后缀的照原文写，「F-15E」「CVN-81」「MQ-9B」。
舰船名的处理：「林肯号航母」抽「林肯号航母」；「辽宁舰」抽「辽宁舰」。
型号只要在正文里出现就抽，不必判断它是不是事件主体。
后处理会自动丢弃没有挂到任何关系或事件上的孤立实体，所以多抽不会污染结果，漏抽才是损失。
正例：「以军出动 F-35 空袭」抽「F-35」
正例：「该厂负责维修 F-16 战机」抽「F-16战机」
正例：「跑道可供 B-52、B-2 等大型军机起降」抽「B-52」「B-2」，是否保留由后处理决定
同一型号在一篇里只记一次，不管出现几次。

人物只收有姓名的自然人。职务、官衔、职业称呼不是实体，不要抽：「美国总统」「俄军总参谋长」「国防部长」「联合国秘书长」「以色列总理」「发言人」都不是实体条目。
原文写「美国总统特朗普」时，只抽「特朗普」，职务写进人物属性的「身份/职业」。
原文只写职务、没有姓名时（如「伊朗军方发言人表示」），不要因为它是发言人就把职务抽成实体；这条信息留给关系证据，不产生实体。
带机关后缀的是机构，如「总统府」「总统办公室」「国防部」「联合国秘书处」；带文书后缀的是作品，如「总统令」「总统法」。

事件不是实体，写在上面的 events 段里，不要放进 entities。
不要抽平台（媒体名、站点、账号），它由文档元数据提供。
不要抽观点、立场、场景，它们不是实体。

记者、通讯员、发言人、消息人士、观察员不是事件施动者，不要作为角色关系的一端。

三、关系
起点终点都必须出现在 entities 中且类型相符，方向必须按下面来。

角色关系（以事件为起点）。**每个二级类型只有下表列出的槽位，表外角色一律不许用。**
没有「通用槽位」这一层，也不许自造角色名。带 * 的是原文有就必须填的。

{slot_table}

关系起点必须**逐字照抄 events 里那条事件的 text 全文**，不能写事件名称、简称或缩写。
反例：事件 text 是「"海湾安全-4"联合安全演习在卡塔尔举行」，关系 head 却写「海湾安全-4」，
      与 events 对不上，整组边被丢弃。
正例：head 写「"海湾安全-4"联合安全演习在卡塔尔举行」，tail 写「海湾阿拉伯国家合作委员会」。

建边的基本要求：事件文本里出现的施动者、被作用对象、地点、装备，都要建成本类型的对应槽位边。
只有文本、没有边的事件视为残缺。
反例：事件「阿拉伯联合酋长国军队当天开始从也门撤离」，只写事件不建边。
正例：同上 → 部署方=阿联酋，兵力=阿联酋军队，起点=也门

受击方 / 打击对象 / 事发地点 三者的分工是这一层最容易混的地方，按「谁」「什么」「哪」来分：

- **受击方** = 谁被打了。国家、机构、武装组织。**地点不进受击方**。
- **打击对象** = 打了什么。被打的那个**具体设施或装备**：基地、机场、指挥所、核电站、
  工厂、雷达站、防空系统、弹药库、油库。必须是能被摧毁的具体目标。
- **事发地点** = 在哪打的。打击所在的**地理范围**：省份、州、城市、地区、地带、海峡。
  它回答的是「地点」，不是「目标」。

正例：「以军空袭加沙地带北部的哈马斯指挥所」
  打击方=以军　受击方=哈马斯　事发地点=加沙地带北部　打击对象=哈马斯指挥所
正例：「俄军导弹袭击基辅的发电站」
  打击方=俄军　事发地点=基辅　打击对象=发电站
正例：「以色列空袭黎巴嫩南部多个真主党目标」
  打击方=以色列　受击方=黎巴嫩真主党　事发地点=黎巴嫩南部　打击对象=真主党目标

反例：打击对象=加沙地带、打击对象=布什尔省、打击对象=黎巴嫩南部
      —— 这些是区域，应进**事发地点**，不是打击对象。
反例：事发地点=哈马斯指挥所 —— 指挥所是目标，应进**打击对象**。
反例：不要把「加沙地带」写进受击方，受击方只放组织或国家。
反例：不要把「哈马斯指挥所」整个写进受击方，受击方只放组织名。

一句话判据：**问「炸的是什么东西」→ 打击对象；问「在哪个地方炸的」→ 事发地点。**
原文只写了区域没写具体目标时，只填事发地点，打击对象留空，不要拿区域来凑。

「发射平台」与「发射物」必须分清：
正例：「以军无人机发射两枚导弹」→ 发射平台=无人机，发射物=导弹
正例：「俄罗斯发射370架无人机」→ 发射物=无人机（没有平台，无人机是被打出去的）
反例：不要把无人机同时写成发射平台和发射物，看它在句中承担哪个角色。

「战果」只记这次事件造成的后果，不记来袭规模，且必须写清对象：
正例：「袭击造成19名平民死亡」→ 战果=19名平民死亡
正例：「击落23架无人机」→ 战果=23架无人机
反例：不要只写数字，如战果=13、战果=数十个，必须带上是什么
反例：不要只写动词或状态，如战果=炸死、战果=受损
反例：「遭到44枚导弹和8架无人机袭击」→ 44枚和8架是来袭数量，属于发射物
判断标准：战果是「打完之后的损失或收获」，且必须能回答「损失的是什么」。

部署类的部署方必须是军队、部队、国防部这类实体，不能是任何个人。
发言人、官员、总统、防长、大使、常驻代表、司令 —— 这些人只是发布消息或下达命令，
真正执行部署的是他们所代表的机构。
正例：「美国国防部宣布向中东增派航母」→ 部署方=美国国防部，兵力=航母，终点=中东
正例：「美国常驻联合国代表证实以色列已在阿联酋部署铁穹」→ 部署方=以色列，兵力=铁穹，终点=阿联酋
反例：不要写部署方=美国防长、部署方=某发言人、部署方=某大使
如果原文只提到个人，部署方填他所代表的机构；机构也无法确定时，这条事件不抽。

原文出现地点的，起点终点必须填，不能因为「不是重点」就省略。
部署到某地、在某地部署，都算终点；从某地撤出、调离，算起点。
正例：「美国在中东已经部署至少10艘军舰」→ 兵力=军舰，终点=中东
正例：「相关装备已开始运抵乌克兰」→ 兵力=装备，终点=乌克兰
正例：「从德国撤出部分驻军」→ 兵力=驻军，起点=德国
反例：不要因为地点是「阿联酋」「日本」这种国家名就跳过，国家名也是地点。

合作类双边用甲方乙方，不要用两个同类角色。会议用主办方/参会方，访问用访问方/受访方，
援助用援助方/受援方。签署的协议写在「协议」槽位，不是「议题」。
正例：「胡塞武装与美国达成停火协议」→ 甲方=胡塞武装，乙方=美国，协议=停火协议
反例：不要写甲方=胡塞武装、乙方=美国、议题=停火协议（议题是谈判内容，不是已达成的协议）

表态类的核心是「谁说了什么」，表态内容几乎总该有。
事件文本里出现的机构或人物，必须作为表态方建成边，不许只写在事件文本里不建边：
反例：事件「以色列国防军发布通告」，只写文本、不建「表态方=以色列国防军」的边。
正例：事件「以色列国防军发布通告」→ 表态方=以色列国防军
正例：「巴加埃表示，伊朗在外交与谈判上真诚而认真」
  表态方=巴加埃，表态内容=伊朗在外交与谈判上真诚而认真
正例：「美方谴责伊朗空袭是严重挑衅」→ 表态方=美方，对象=伊朗，表态内容=严重挑衅
反例：不要只填表态方，把说话内容丢掉；原文有引语或转述内容就必须填表态内容。
表态里的「对象」指针对谁或针对什么事，不是说话人自己。

灾伤类的受灾对象是**伤亡方或受损的具体设施**，事发地点是**地理范围**。两者按同一判据分开：
问「谁受伤 / 什么被毁」→ 受灾对象；问「在哪个地方」→ 事发地点。

**受灾对象几乎总该有，不要因为写了事发地点就不填受灾对象。**
下列情况都必须填受灾对象，这是最容易漏的地方：
- 人员伤亡 → 受灾对象 = 平民 / 士兵 / 居民（原文说「造成X人死亡」时，X是谁就是受灾对象）
- 设施被毁 → 受灾对象 = 被毁的那个设施（「中学遭破坏」→ 受灾对象=中学）
- 装备受损 → 受灾对象 = 受损装备
- 只有「遭破坏」「被击中」「受损」而没有明说人时，把**被破坏的东西**填进受灾对象。

正例：「沃罗涅日市的民用设施，包括十余栋住宅楼、一所中学及数座行政建筑遭破坏，造成一人死亡」
  事发地点=沃罗涅日市　受灾对象=民用设施 / 中学 / 住宅楼 / 行政建筑　
  伤亡数字=一人死亡，至少三人受伤
  （注意：这里既要填设施为受灾对象，也要填伤亡数字，两者不冲突）
反例：只写事发地点=沃罗涅日市，受灾对象留空 —— 漏了真正受损的东西。
正例：「沙贾雷·塔耶贝小学遭袭，至少165人遇难」
  事发地点=米纳卜市　受灾对象=沙贾雷·塔耶贝小学　伤亡数字=至少165人遇难
正例：「卡杜格利遭遇无人机袭击，造成8名平民死亡」
  事发地点=卡杜格利　受灾对象=平民　伤亡数字=8名平民死亡
正例：「该国7条架空输电线路被弹片击中受损」
  受灾对象=架空输电线路　（没有伤亡数字也要填受灾对象）

反例：受灾对象=沃罗涅日市 —— 城市是地点，应进事发地点。
      真正的受灾对象是城里的设施或人员。
反例：受灾对象=加沙地带 —— 同上，这是区域不是受灾者。

伤亡数字是原文的伤亡表述。「原因」优先连到另一条事件上，连不上才用短语，
不要拿整句话当原因。

治理类：主体是机关，立法用「法案」，司法用「涉案对象」，人事用「任免对象」，
行政用「行政对象」。处置结果是「通过/否决/判处/任命」这类结论，不是对象本身。

结构关系（实体与实体之间，只收稳定关系，不随单次事件变化）：
隶属 机构/国家→机构/国家（上级机关，组织层级）
辖属 机构→国家（国别归属）
驻地 机构→地点（常驻位置）
部署方 装备→机构（装备由谁部署）
去向 装备→地点（装备实际移动到该地）
任职 人物→机构（原文写明职务与机构，如「国防部长赫格塞思」）
会晤 人物→人物（面对面或通话，如「特朗普与塞西举行会谈」）
搭载 装备→装备（载体与载荷，如「台风战机搭载流星导弹」）
位于 地点→地点（行政或地理包含，如「塔伊兹省位于也门」）
敌对 机构/国家→机构/国家（交火、宣战、明确对抗，如「以军与哈马斯交火」）

**「辖属」是必须尽量补全的一条，判据和别的关系不同。**它回答「这个机构属于哪个国家」，
是图谱归类的依据。满足下列任一条件就必须建这条边，不要因为正文没写「属于」就跳过：

1. 机构名本身含国名或国别指称 —— 伊朗国防部→伊朗、俄罗斯空天军→俄罗斯、中国海军→中国、
   以军→以色列、美军→美国、乌军→乌克兰、俄军→俄罗斯。
   这类只要机构名出现，就建辖属边，不需要正文另外说明。
2. 正文提到该机构所属的国家 —— 例如正文写「以色列国防军…以色列北部」，就以以色列为终点。
3. 机构名含该国独有标识 —— 五角大楼→美国、白宫→美国、克里姆林宫→俄罗斯、
   伊斯兰革命卫队→伊朗、真主党→黎巴嫩、哈马斯→巴勒斯坦、胡塞武装→也门。

反例：正文只出现「美军驻地」，没有「美国」二字 —— 仍然必须建 美军→美国，
      因为「美」就是国别指称，这是第 1 条的适用情形，不是编造。
反例：不要因为机构是武装组织就跳过。真主党、哈马斯、胡塞武装、伊斯兰革命卫队都是机构，
      都有明确的国别归属，都要建辖属边。

下列机构**不建辖属边**（跨国，或名称不完整无法确定）：
北约、联合国、欧盟、欧佩克、国际原子能机构、红十字会、伊斯兰国、基地组织、
库尔德武装（跨土耳其/叙利亚/伊拉克/伊朗）、仅写「国防部」「外交部」「军方」而无国别限定的。

**双边或多边共管机构也不建辖属**，它们同时属于多个国家，归到任一国都是错的：
「泰柬边界委员会」「中印边界问题特别代表会晤机制」「朝美首脑会谈筹备组」这类，
名称里出现的国家只是参与方，不是归属方。判断依据：机构名含两个以上国家/政权名的，
一律不建辖属边，改由「隶属」或事件表达。

辖属的起点只能是机构，终点只能是国家。不要把「伊朗辖属伊朗」这类国家到国家的边写出来。

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
「日本强行部署远程导弹」应写成：事件=强行部署远程导弹，部署方=日本。不要把主语重复写进事件。
「英国海军会合了邓肯号」不是实施，是事件的到场或受控方，视语境定。
「美军向基地发射导弹」：事件类型是发射，不是打击。
「摧毁380辆军用车辆」不是拦截，事件类型是打击。
装备被摧毁、被拦截不是「使用」；装备只是被提及、统计、列装也不算。

五、正反例
正例：「以军在加沙地带空袭」→ 事件=以军空袭加沙（type=对抗，subtype=空袭），打击方=以军，受击方=加沙
正例：「以军称空袭了黎巴嫩南部」→ 事件=以军空袭黎巴嫩南部（type=对抗，subtype=空袭）
   转述动词不改变事实性质，仍归对抗。
反例：不要把「加沙地带」标成事件。
正例：「双方签署《吉达停火协议》」→ 事件=签署《吉达停火协议》（type=合作，subtype=签署），涉及=《吉达停火协议》
反例：不要把「停火」「协议」单独标成作品。
正例：「伊朗外交部发言人称美方指控不实」→ 事件=伊朗外交部发言人称美方指控不实
   （type=表态，subtype=否认），表态边=伊朗外交部发言人，stance=否认
   这一句没有陈述具体动作，只是态度，所以归表态。
反例：不要写成「伊朗外交部发言人 表态方 称美方指控不实」。
正例：「美方谴责伊朗空袭」→ 事件=美方谴责伊朗空袭（type=表态，subtype=谴责）
   这里被转述的是态度，不是动作，所以归表态；被谴责的空袭本身若要抽，另起一条（type=对抗，subtype=空袭）。

人物要素名只能是：{attrs}。姓名用 entities 里的人物 text。

JSON：
{{
  "entities": [{{"type": "国家|机构|人物|装备|地点|作品", "text": "原文片段"}}],
  "events": [{{"text": "事件原文片段", "event_type": "对抗|部署|合作|表态|灾伤|治理|其他", "subtype": "该一级下的二级类型，可留空", "kind": "发生|计划", "trigger": "触发词（原文中的一个词或短语）", "time": "原文的时间表述，没有就留空"}}],
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

Every event carries two labels. Judge the first level first, then the second.

1. event_type, first level, one of: {event_types}

   **Judge the fact being reported, not whether reporting verbs are present.**
   News copy almost always attributes its claims ("said", "announced", "reported").
   The reporting verb does not change the nature of the fact.
   - Reporting a completed action → classify by that action.
     "The US said it struck Iran" → 对抗·打击 (the fact is a strike)
     "Kuwait reported it was hit by drones" → 对抗·打击 (the fact is an attack)
     "CENTCOM said it is enforcing a naval blockade" → 对抗·控制 (the fact is a blockade)
   - Reporting a planned action → classify by that action, kind is 计划.
     "The US announced it will send reinforcements" → 部署·部署, kind 计划
   - Expressing an attitude, evaluation, or position without stating any concrete action → 表态.
     "The US condemned the Iranian strike" → 表态·谴责
     "Iran denied involvement" → 表态·否认
     "China called for restraint" → 表态·呼吁

   Definitions:
   - 对抗: armed clash, contest for control, or coercion. There is a hostile party, or force is applied.
   - 部署: movement, massing, drill, or withdrawal of one's own forces, without direct fighting.
   - 合作: multiple parties reaching consensus or helping each other.
   - 表态: attitude, evaluation, or position only, with no concrete action stated.
   - 其他: accident, legislation, personnel, investigation, none of the above.

2. subtype, second level, must belong to that first level: {subtypes}
   Under 对抗: 打击 (attack, clash, casualties), 空袭 (aircraft bombing or strafing),
   拦截 (shoot down, air defence), 发射 (launch, test-fire),
   控制 (occupy, take over, blockade, detain)
   Under 部署: 投送 (reinforce, dispatch, station, mass, stand up),
   机动 (advance, redeploy, transit, sortie),
   战备 (raise alert level, mobilise, patrol alert),
   演习 (drill, exercise, test-fire),
   撤离 (withdraw, retreat, return to base)
   Under 合作: 签署 (sign, conclude, join, approve), 谈判 (negotiate, mediate),
   会议 (summit, forum, ceremony), 访问 (visit, arrive, receive, inspect),
   援助 (military aid, relief, supply)
   Under 表态: 声明 (statement, announcement), 呼吁 (appeal, urge),
   警告 (warn, threaten), 否认 (deny, refute), 承认 (acknowledge, confirm),
   谴责 (condemn, protest)
   Under 其他 there is no second level; leave it blank.

   Disambiguation:
   - 签署 is the result reached; 谈判 is the process under way.
   - 表态 collects pure attitude only. "Condemned the strike" is 表态; "reported the strike" is 对抗·打击.
   - Leave subtype blank when it cannot be determined; do not force a value.

kind is 发生 (happened) or 计划 (planned), independent of event_type:
"The US announced it will send troops" is 部署·部署 + 计划.
"The US has already sent troops" is 部署·部署 + 发生.
"The US opposes sending troops" is 表态 + 发生.
Negative: "forces must withdraw" is 表态, not 发生.

2. Entities
Six types only: 国家, 机构, 人物, 装备, 地点, 作品.
A sovereign state is 国家, never 机构 or 地点. "美国" is 国家; "美国国防部" is 机构.
Positive: "Israel deployed Iron Dome in the UAE" → both 以色列 and 阿联酋 are 国家.
Events are not entities: write them in the events section, never in entities.
Do not extract platforms (outlet names, sites, accounts); they come from document metadata.
Do not extract claims or stances as entities.
Reporters, spokespeople, and sources are not agents; never use them as a relation endpoint.

Equipment entity names must be copied from the source text verbatim. Never add or infer words. Three cases:
1. The text writes designation + class word: take the whole span.
   Positive: "F-35 fighter jet" → "F-35 fighter jet". "A-10 attack aircraft" → "A-10 attack aircraft".
   Positive: "S-400 system" → "S-400 system". "C-130 transport" → "C-130 transport".
2. The text gives only a designation with no class word: take the designation alone.
   Positive: "Ukraine's F-16 has been delivered" → "F-16".
   Negative: do not expand it to "F-16 fighter jet"; the entity must be a contiguous span of the source.
3. Only when the whole text has no designation at all, extract the generic term.
   Positive: "Russian forces used drones" → "drone".
   Negative: when a designation appears anywhere, never substitute "drone", "missile", or "carrier".

Keep suffixes as written: "F-15E", "CVN-81", "MQ-9B".
Ship names: "USS Abraham Lincoln carrier" → "USS Abraham Lincoln carrier"; "Liaoning" → "Liaoning".
Extract any designation that appears in the text; do not try to judge whether it is the subject of an event.
Post-processing drops entities that are not attached to any relation or event, so over-extraction is harmless and under-extraction is the real loss.
Positive: "Israeli F-35s struck the site" → "F-35".
Positive: "the plant overhauls F-16 jets" → "F-16 jets".
Positive: "the runway can handle B-52s and B-2s" → "B-52", "B-2"; post-processing decides whether to keep them.
One entity per name per document, regardless of how many times it appears.

People are natural persons with a name. Job titles and honorifics are not entities and must not be extracted: "美国总统", "俄军总参谋长", "国防部长", "联合国秘书长", "以色列总理", "发言人" are not entity entries.
When the text says "美国总统特朗普", extract only 特朗普 and put the title into the person attribute 身份/职业.
When the text gives only a title and no name (e.g. "伊朗军方发言人表示"), do not turn the title into an entity; leave that to relation evidence.
Forms carrying an institutional suffix are 机构, e.g. "总统府", "总统办公室", "国防部". Forms carrying a document suffix are 作品, e.g. "总统令".

3. Relations
Both endpoints must appear in entities with matching types.

Role relations, starting from an event. **Each subtype has only the slots listed below;
no universal slots, and never invent a slot name.** Slots marked * are mandatory when the
text provides them; ~ means fill only when the text names it.

{slot_table}

The agent slot (打击方/拦截方/发射方/控制方/部署方/甲方/主办方/访问方/援助方/表态方/主体/施动方)
is mandatory for every event: the actor appears at the start of the event text, extract it
into an edge instead of leaving it only in the text.

受击方 vs 打击对象 vs 事发地点 — split by "who / what / where":

- 受击方 = who was hit (state, organization, armed group — never a place).
- 打击对象 = what was hit. The concrete **facility or equipment** destroyed or targeted:
  base, airfield, command post, nuclear plant, factory, radar site, air-defence system,
  ammunition depot. It must be a destructible concrete target.
- 事发地点 = where it happened. The **geographic scope**: province, state, city, district,
  strip, strait. It answers "where", not "what".

Positive: "Israeli jets struck a Hamas command post in northern Gaza"
  打击方=Israel　受击方=Hamas　事发地点=northern Gaza　打击对象=Hamas command post
Positive: "Russian missiles hit a power plant in Kyiv"
  打击方=Russia　事发地点=Kyiv　打击对象=power plant

Negative: 打击对象=Gaza, 打击对象=Bushir Province, 打击对象=southern Lebanon
      — these are regions and belong to 事发地点.
Negative: 事发地点=Hamas command post — a command post is a target, use 打击对象.

One-line test: **"what was destroyed?" → 打击对象; "where did it happen?" → 事发地点.**
If the text names only a region and no concrete target, fill 事发地点 and leave 打击对象 empty.

发射平台 vs 发射物 is the most common confusion:
Positive: "Israeli drones fired two missiles" → 发射平台=drone, 发射物=missile
Positive: "Russia launched 370 drones" → 发射物=drones (no platform; the drones are fired)
Negative: never put the same equipment under both slots.

战果 records only the outcome, never the incoming scale:
Positive: "the strike killed 19 people" → 战果=19 killed
Negative: "came under attack by 44 missiles" → 44 is 发射物, not 战果
Negative: "the plant was paralysed" is acceptable, bare numbers are not.

Structural relations, entity to entity, stable across single events:
隶属 org/state→org/state (parent body, organizational hierarchy)
辖属 org→state (national affiliation)
驻地 org→place (station)
部署方 equipment→org (who deployed it)
去向 equipment→place (where it moved)
任职 person→org (the text states the post and the organization, e.g. "Defense Secretary Hegseth")
会晤 person→person (direct meeting or call, e.g. "Trump met el-Sisi")
搭载 equipment→equipment (carrier and payload, e.g. "Typhoon carries Meteor missiles")
位于 place→place (administrative or geographic containment, e.g. "Taiz is in Yemen")
敌对 org/state→org/state (clashes, declared hostility, e.g. "the IDF fought Hamas")

**辖属 must be filled wherever possible — its test differs from the other relations.** It answers
"which state does this organization belong to" and drives how the graph is grouped. Create this
edge if any of the following holds; do not skip it just because the text never says "belongs to":

1. The organization name itself contains a state name or demonym — 伊朗国防部→伊朗,
   俄罗斯空天军→俄罗斯, 中国海军→中国, 以军→以色列, 美军→美国, 乌军→乌克兰.
   Whenever such an entity appears, emit the 辖属 edge; no further textual support is needed.
2. The text mentions the state the organization belongs to.
3. The name carries a state-specific marker — 五角大楼→美国, 白宫→美国, 克里姆林宫→俄罗斯,
   伊斯兰革命卫队→伊朗, 真主党→黎巴嫩, 哈马斯→巴勒斯坦, 胡塞武装→也门.

Negative: the text says only "美军驻地" with no "美国". You must still emit 美军→美国 —
      "美" is a demonym, which is case 1, not fabrication.
Negative: do not skip armed groups. 真主党, 哈马斯, 胡塞武装, 伊斯兰革命卫队 are all
      organizations with a definite state affiliation and need this edge.

Do NOT emit 辖属 for transnational or ambiguous bodies: 北约, 联合国, 欧盟, 欧佩克,
国际原子能机构, 红十字会, 伊斯兰国, 基地组织, 库尔德武装, or a bare 国防部/外交部/军方
with no state qualifier.

Head must be an organization; tail must be a state. Never emit state-to-state 辖属.

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
  "entities": [{{"type": "国家|机构|人物|装备|地点|作品", "text": "source span"}}],
  "events": [{{"text": "event span", "event_type": "对抗|部署|合作|表态|其他", "subtype": "subtype under that level, may be blank", "kind": "发生|计划"}}],
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
    "主任", "检察长", "总检察长", "院长", "审判长", "国务卿",
    "秘书长", "副秘书长", "秘书", "助理", "发言人", "特使",
    "专员", "行长", "总裁", "董事长",
    "领导人", "元首", "领袖", "头目",
)

# 职务词之后接这些后缀，说明指的是机关而不是人：总统府、总理办公室、司令部
ORG_TAIL = re.compile(
    r"(?:办公室|办公厅|委员会|主席团|秘书处|指挥部|司令部|会议|大会|"
    r"府|部|局|院|署|厅|处|司|科|中心|大楼|大厦|机关|机构|总部|馆|所|站|团|组)$"
)

# 职务词之后接这些，说明指的是文书：总统令、总统法
WORK_TAIL = re.compile(r"^(?:令|法|案|条例|法案|命令|公告|声明|决议|文件|草案)")

# 称谓里不该出现的标点与实义动词。出现即说明是句子片段。
# 单字虚词不作为判据：国名里的「和」、机构名里的「中」都会误伤
HEAD_NOISE = re.compile(
    r"[，。；、！？：（）()「」《》“”\"']|"
    r"表示|宣布|指出|强调|要求|呼吁|决定|准备|计划|实施|发动|进行|"
    r"指令|指示|命令|消息|报道|采访|声明|对此|就此|回应|表态"
)

# 人名形态：2 至 6 个汉字，可带间隔号
PERSON_NAME = re.compile(r"^[\u4e00-\u9fff][\u4e00-\u9fff·]{1,5}$")

# 姓名部分通常是 2 至 5 个汉字（外族人名偏长）
NAME_IN_TITLE = re.compile(r"^[\u4e00-\u9fff][\u4e00-\u9fff·]{1,4}$")

# 职务词之后跟这些，指的是场所或建制，整体是机构：总统官邸、总理府邸
PLACE_TAIL = re.compile(r"^(?:官邸|府邸|住所|宅|大楼|大厦|总部|基地|医院|学校|学院|大学|银行)")
PLACE_WORD = re.compile(r"官邸|府邸|住所|大楼|大厦|总部|基地|机构|部门|系统|集团|公司")

# 并列连词。出现在「职务+姓名」之间说明是句子片段，如「大使馆及领事馆」
CONJ = re.compile(r"及|与|或|、")

# 句子特征：助动词、否定、趋向、方位等。称谓里不会出现
SENTENCE_LIKE = re.compile(
    r"将|已|正|曾|正在|可以|能够|应该|必须|需要|负责|面临|陷入|成为|作为|"
    r"认为|提出|参加|出席|会见|举行|前往|抵达|位于|属于|包括|拥有|"
    r"被|把|让|使|导致|造成|引发|影响|显示|暴露|表明|证明|意味着|"
    r"若|如果|虽然|但是|因为|所以|而且|并且|因此|否则"
)

# 动词开头。姓名不会以这些词打头
VERB_LEAD = re.compile(
    r"^(?:聚集|强行|会见|举行|前往|抵达|访问|发表|表示|指出|强调|宣布|"
    r"回应|揭牌|出任|担任|负责|参加|出席|视察|考察|签署|发布|接受|"
    r"否认|承认|呼吁|要求|警告|谴责|支持|反对|决定|批准|通过|就任|"
    r"会晤|会面|会见|接见|接待|批评|指责|致信|转达|解除|杀害|指责|施压)"
)

# 姓名不会以这些字结尾：动词、副词、虚词、量词
NAME_TAIL_BAD = re.compile(
    r"[称说指讲谈道问想愿要会将已正曾在把被让使的了着过"
    r"和与及或等之对就也都还又更最很太"
    r"一十二三四五六七八九]$"
)

# 姓名里不会出现的成分：机构、场所、地理、抽象名词
NON_NAME = re.compile(
    r"辖区|责任区|部队|官网|网站|门前|附近|周边|所在|地区|区域|境内|"
    r"会馆|理事|委员会|司令部|总部|基地|大楼|大厦|府邸|官邸|"
    r"共识|报告|备忘录|职务|周年|流程|决策|支持|安理会|国会|议会|"
    r"青瓦台|爱丽舍|克里姆林|白宫|唐宁街|"
    r"警告|介入|置评|置评|记者|访东|访华|访美|访俄|长治久安|久安|"
    r"[省市县镇乡村州郡]"
)

# 姓名不能以这些动词或双字词结尾
NAME_TAIL_VERB = re.compile(
    r"(?:介入|警告|置评|拒绝|回应|表态|报道|记者|访东|访华|访美|访俄|会晤|会面)$"
)

# 集体名词与泛称：既不是机构也不是人名，整串丢弃
GENERIC = re.compile(
    r"^(?:西方|多国|各国|相关|有关|当地|外媒|外国|国际|"
    r"俄美乌|俄乌|美伊|以伊|双方|各方|两国|三国|多个)"
)


def _title_end(name: str) -> int:
    """最后一个职务词的结束位置；无职务词返回 0。"""
    end = 0
    for w in TITLE_WORDS:
        i = name.rfind(w)
        if i >= 0:
            end = max(end, i + len(w))
    return end


def title_span_kind(name: str) -> str:
    """职务串粗判：'' 不是；'title' 纯职务；'title_name' 职务加姓名；'generic' 泛称。

    这里只做形态筛查。是否真的拆出姓名，由 check_block 用原文再验一次：
    姓名必须在同一段原文里独立出现，否则整串视为称谓丢弃。
    """
    t = (name or "").strip()
    if not t or len(t) > 24:
        return ""
    if not re.fullmatch(r"[\u4e00-\u9fff·A-Za-z ]+", t):
        return ""
    if SENTENCE_LIKE.search(t) or HEAD_NOISE.search(t):
        return ""
    if GENERIC.match(t):
        return "generic"
    end = _title_end(t)
    if not end:
        return ""
    head = _title_head_start(t, end)
    if not _prefix_is_ok(t[:head]):
        return ""
    rest = t[end:].strip()
    if rest and (ORG_TAIL.search(rest) or WORK_TAIL.match(rest) or PLACE_TAIL.match(rest)):
        return ""
    if not rest:
        return "title" if len(t) <= 22 else ""
    if len(rest) <= 6 and PERSON_NAME.match(rest) and not PLACE_WORD.search(rest):
        if CONJ.search(t) or len(t) > 16 or VERB_LEAD.match(rest):
            return ""
        if not NAME_IN_TITLE.match(rest):
            return ""
        # 姓名里不能再出现职务词、机构词或场所词
        if any(w in rest for w in TITLE_WORDS):
            return ""
        if ORG_TAIL.search(rest) or PLACE_TAIL.match(rest) or NON_NAME.search(rest):
            return ""
        # 姓名不能以动词、虚词、量词结尾
        if NAME_TAIL_BAD.search(rest) or NAME_TAIL_VERB.search(rest):
            return ""
        return "title_name"
    return ""


def _title_head_start(t: str, end: int) -> int:
    """职务词本身的起点：在 end 之前找最长命中的职务词。"""
    best = end
    for w in TITLE_WORDS:
        i = t.rfind(w, 0, end)
        if i >= 0:
            best = min(best, i)
    return best


# 职务词之前允许出现的修饰：国别、军种、政党、建制等短前缀
PREFIX_OK = re.compile(
    r"^(?:[\u4e00-\u9fff]{1,4}(?:国|军|部|党|集团|组织|委员会|司令部|总部|卫队|武装|部队|政府|总统府|"
    r"外交部|国防部|国务院|州|省|市|县))*$"
)


def _prefix_is_ok(prefix: str) -> bool:
    """职务词前面的修饰是否属于「国别+军种」这类正常前缀。

    超过 8 字、或含句子成分，就说明这是一句话被切了一段。
    """
    if not prefix:
        return True
    if len(prefix) > 20:
        return False
    if SENTENCE_LIKE.search(prefix) or HEAD_NOISE.search(prefix):
        return False
    # 前缀里不能出现动词性结尾，如「聚集在」「控制和转移委」「新领事」
    if re.search(r"在$|了$|着$|过$|和$|把$|被$|及$|与$|新$|该$|此$", prefix):
        return False
    # 前缀不能是动词短语：以常见动词开头
    if VERB_LEAD.match(prefix):
        return False
    # 前缀里不能夹动作词（连续指责德国）
    if re.search(r"指责|批评|谴责|警告|要求|呼吁|宣布|表示|决定|解除|杀害|接待|会晤|"
                 r"致电|致信|会见|通话|拜访|接见|转达|递交|打死|打伤|击杀|抓获", prefix):
        return False
    return True


def title_name_part(name: str) -> str:
    """职务串里跟在职务后面的姓名；没有返回空串。"""
    if title_span_kind(name) != "title_name":
        return ""
    t = (name or "").strip()
    end = _title_end(t)
    return t[end:].strip() if end < len(t) else ""


def guess_subtype(text: str) -> tuple[str, str]:
    """按线索词猜 (一级, 二级)。

    先按旧 16 类线索定一级；一级为「部署」时再用细分线索定二级。
    灾伤与治理是新加的一级，旧线索表里没有，靠独立线索判断。
    """
    t = text or ""
    # 灾伤与治理优先：这两类的线索更具体，避免被「打击」「声明」等泛线索吃掉
    for name, rx in CASUALTY_CUES:
        if rx.search(t):
            return ("灾伤", name)
    for name, rx in GOVERN_CUES:
        if rx.search(t):
            return ("治理", name)
    for name, rx in TYPE_CUES:
        if not rx.search(t):
            continue
        pair = LEGACY_EVENT_MAP.get(name)
        if not pair:
            continue
        et, sub = pair
        if et == "部署":
            sub = deploy_subtype(t) or sub
        return (et, sub)
    return ("其他", "")


def deploy_subtype(text: str) -> str:
    """把「部署」一级细分为投送 / 机动 / 战备 / 演习 / 撤离。"""
    t = text or ""
    for name, rx in DEPLOY_CUES:
        if rx.search(t):
            return name
    return ""


# 部署一级的二级线索，按优先级：先看撤离与演习，再看战备，最后默认投送
DEPLOY_CUES = (
    ("撤离", re.compile(r"撤离|撤出|撤退|回撤|撤回|返航|撤走")),
    ("演习", re.compile(r"演习|演练|军演|实弹|训练|联演|试射|模拟起降")),
    ("战备", re.compile(r"战备|戒备|动员|警巡|进入.{0,4}状态|提高.{0,4}级别")),
    ("机动", re.compile(r"推进|开进|调往|调动|转场|起飞|飞行|巡航|奔赴|驶向|抵近")),
    ("投送", re.compile(r"部署|增兵|增派|派遣|派驻|进驻|驻扎|集结|运送|投送|调集|组建|成立|布防")),
)

# 灾伤一级的二级线索
CASUALTY_CUES = (
    ("伤亡", re.compile(r"死亡|受伤|丧生|遇难|伤亡|牺牲|打死|击毙|受伤人数|平民伤亡")),
    ("事故", re.compile(r"坠毁|失火|泄漏|倒塌|崩塌|停电|断电|发生事故|损毁|起火|脱轨|沉没")),
)

# 治理一级的二级线索
GOVERN_CUES = (
    ("立法", re.compile(r"法案|议会通过|决议草案|批准.{0,8}条约|修正案|表决通过|议会批准")),
    ("司法", re.compile(r"立案|侦查|起诉|审判|判决|判处|开庭|拘留|拘押|提起诉讼")),
    ("人事", re.compile(r"任命|免职|撤职|辞职|提名|就任|撤销.{0,8}资格|换人")),
)


def guess_event_type(text: str) -> str:
    """只猜一级。"""
    return guess_subtype(text)[0]


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


def guess_kind(text: str) -> str:
    """判定发生 / 计划。

    表态已上移为一级类型，不再出现在 kind 里；
    是否表态由 event_type 表达，与 kind 正交。
    """
    t = text or ""
    planned = bool(PLAN_CUE.search(t))
    done = bool(DONE_MARK.search(t))
    if planned and not done:
        return "计划"
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

# 句中出现的时间短语，用于把「模型给的事件文本」与「校验后的事件文本」对齐
TIME_PHRASE = re.compile(
    r"(?:\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|当地时间|当天|当日|同日|"
    r"近日|日前|近期|随后|此前|稍早|\d{1,2}时\d{1,2}分)"
)


# 记者会/发布会这类单向信息发布是表态的场景，不是事件本身。
# 模型偶尔会把「某部门举行例行记者会」抽成事件，这里做兜底过滤。
PRESS_EVENT = re.compile(
    r"(?:举行|召开|举办|召开例行|举行例行).{0,8}"
    r"(?:记者会|新闻发布会|新闻通气会|媒体吹风会|通气会|发布会)"
    r"|(?:记者会|新闻发布会|通气会|吹风会)$"
)


def is_press_event(text: str) -> bool:
    """是否为「记者会」这类场景事件，不应单独成事件。"""
    t = (text or "").strip()
    if not t:
        return False
    # 只有场景、没有具体内容的长句才过滤；带「说/表示/称」的是表态，保留
    if re.search(r"(说|表示|称|宣布|指出|回应|强调)[，,]?", t):
        return False
    return bool(PRESS_EVENT.search(t))


def _has_actor_by_slot(ev: dict, text: str, ents: dict[str, set[str]]) -> bool:
    """省略施动者的句子是否仍成立：文本里出现本类型某个非施动槽位能挂的实体。

    只对容易省略主语的类型放宽。对抗日照与表态必须有明确施动者，
    否则「遭袭」「被击落」这类无主句会产出无法归属的事件。
    """
    et = ev.get("event_type") or ""
    if et not in ("部署", "灾伤", "治理"):
        return False
    slots = slots_of(et, ev.get("subtype") or "")
    if not slots:
        return False
    # 收集本类型非施动槽位允许的端点类型
    want: set[str] = set()
    for s in slots:
        if s.name in ACTOR_SLOTS:
            continue
        want |= set(s.types)
    want.discard("事件")
    want.discard("字面值")
    if not want:
        return False
    for name, types in ents.items():
        if len(name) < 2 or name not in text:
            continue
        if types & want:
            return True
    return False


def has_agent(text: str, entities: set[str]) -> bool:
    """事件文本里是否出现施动者（已知实体名）。

    中文新闻的施动者不一定在句首：被动句（「机场因威胁实行管制」）、
    主谓宾倒装（「在多地击落 253 架无人机」的施动者在前面分句）都常见。
    这里放宽为：剥掉时间状语后，句首 20 字内出现实体名就算有施动者。
    """
    t = (text or "").strip()
    if not t:
        return False
    for _ in range(2):
        t2 = LEAD_NOISE.sub("", t)
        if t2 == t:
            break
        t = t2
    head = t[:28]
    for name in entities:
        n = (name or "").strip()
        if len(n) < 2:
            continue
        if t.startswith(n):
            return True
        if n in head:
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
            event_types=EVENT_TYPES_STR, subtypes=SUBTYPES_STR,
            stances=STANCES_STR, attrs=attrs, block=block,
            slot_table=SLOT_TABLE_STR,
        )
    return SYSTEM_ZH, USER_ZH.format(
        event_types=EVENT_TYPES_STR, subtypes=SUBTYPES_STR,
        stances=STANCES_STR, attrs=attrs, block=block,
        slot_table=SLOT_TABLE_STR,
    )


def _keep_span(text: str, hay: str) -> str:
    t = (text or "").strip()
    if t and locate_span(t, hay):
        return t
    return ""


def _keep_event_span(text: str, hay: str) -> str:
    """事件片段的宽松匹配。

    模型常会改写一两个字（漏「重要」、把「进行了打击」写成「打击」），
    严格子串校验会让整篇作废。这里在完全匹配失败时，
    取原文中与模型输出最接近的一段，前提是重合度足够高。

    返回原文里的实际片段，保证下游拿到的仍是原文子串。
    """
    t = (text or "").strip()
    if not t:
        return ""
    if locate_span(t, hay):
        return t
    # 找最长公共连续子串作为锚点，向两侧扩展到句读边界
    anchor = _longest_common(t, hay)
    if len(anchor) < max(8, len(t) // 3):
        return ""
    i = hay.find(anchor)
    if i < 0:
        return ""
    # 从锚点向左扩到上一个句读，向右扩到下一个句读
    left = i
    while left > 0 and hay[left - 1] not in "。！？\n":
        left -= 1
    right = i + len(anchor)
    while right < len(hay) and hay[right] not in "。！？\n":
        right += 1
    seg = hay[left:right].strip(" ，、；：")
    seg = strip_dateline(seg)
    # 扩展后的片段不能过长，也不应与原文相差太远
    if not seg or len(seg) > max(len(t) * 2, 100):
        return ""
    return seg


# 电头：新华社某地某月某日电（记者某某）
DATELINE = re.compile(
    r"^(?:[\u4e00-\u9fff]{2,8}\d{1,2}月\d{1,2}日电(?:（[^）]{0,30}）)?"
    r"|(?:新华社|中新社|央视新闻|总台)[^。，]{0,30}电(?:（[^）]{0,30}）?)"
    r"|据[^。，]{0,12}(?:报道|消息)[，,]\s*)[\s，,]*"
)


def strip_dateline(text: str) -> str:
    """去掉电头与「据某媒体报道」这类引导语。"""
    t = (text or "").strip()
    for _ in range(2):
        t2 = DATELINE.sub("", t)
        if t2 == t:
            break
        t = t2.strip()
    return t


def _longest_common(a: str, b: str) -> str:
    """最长公共连续子串，仅用于短文本对齐。"""
    if not a or not b:
        return ""
    best, best_len = "", 0
    # 动态规划表，只保留上一行
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best_len:
                    best_len = cur[j]
                    best = b[j - best_len : j]
        prev = cur
    return best


# ---------------- 校验 ----------------


def check_block(parsed: dict, hay: str) -> dict:
    """块内校验：片段、类型、枚举、角色约束。"""
    parsed = parsed if isinstance(parsed, dict) else {}
    dropped = Counter()

    # 事件：先建表，因为角色关系的起点必须是事件
    events: dict[str, dict] = {}
    # 模型原始事件文本 -> 校验后的文本，关系端点据此改写
    event_alias: dict[str, str] = {}
    raw_events = parsed.get("events") if isinstance(parsed.get("events"), list) else []
    for item in raw_events:
        if not isinstance(item, dict):
            dropped["event"] += 1
            continue
        raw_text = str(item.get("text") or "").strip()
        text = _keep_event_span(raw_text, hay)
        if not text:
            dropped["event_span"] += 1
            continue
        if raw_text and raw_text != text:
            event_alias[raw_text] = text
        ok, why = is_atom_event(text)
        if not ok:
            dropped[f"event_{why}"] += 1
            continue
        etype = str(item.get("event_type") or "").strip()
        stype = str(item.get("subtype") or "").strip()
        kind = str(item.get("kind") or "").strip()

        # 一级类型校验。表态类由文本线索判定，不由模型指定 kind
        if etype not in EVENT_TYPES:
            pair = guess_subtype(text)
            etype = pair[0]
            if not stype:
                stype = pair[1]
            if etype != "其他":
                dropped["event_type_guessed"] += 1
            else:
                dropped["event_type_default"] += 1

        # 二级类型必须属于该一级；不属于则按线索补，补不上就留空
        allowed = SUBTYPES.get(etype, ())
        if allowed:
            if stype not in allowed:
                guess = guess_subtype(text)
                if guess[0] == etype and guess[1] in allowed:
                    stype = guess[1]
                    dropped["subtype_guessed"] += 1
                else:
                    stype = ""
                    dropped["subtype_missing"] += 1
        else:
            stype = ""

        # kind 只表发生/计划；表态由一级类型表达，不再进入 kind
        if kind not in EVENT_KINDS:
            kind = guess_kind(text)
            if kind == "表态":
                # 旧规则把表态写进 kind，这里转成一级类型
                etype, stype = "表态", (stype if stype in SUBTYPES["表态"] else "声明")
            dropped["kind_guessed"] += 1

        # 触发词：必须是事件片段里的连续子串，且短
        trig = _keep_span(str(item.get("trigger") or "").strip(), text)
        if not trig or len(trig) > 12 or trig == text:
            dropped["trigger_missing"] += 1
            trig = ""
        # 时间短语：必须是原文子串，解析在后续用 graph.time 做
        raw_time = _keep_span(str(item.get("time") or "").strip(), hay)

        events[text] = {
            "text": text, "event_type": etype, "subtype": stype, "kind": kind,
            "trigger": trig, "raw_time": raw_time,
        }

    # 实体：职务串不进实体表。带姓名的职务串只保留姓名，纯职务直接丢弃
    raw_ents: dict[str, set[str]] = {}
    alias: dict[str, str] = {}
    title_spans: set[str] = set()
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
        # 职务、官衔只是称谓，不是实体。带姓名的取出姓名，纯职务丢弃
        if title_span_kind(text):
            title_spans.add(text)
            name = title_name_part(text)
            # 姓名必须在这段原文里能找到（宽松策略：职务+姓名形态即认定姓名存在）
            if len(name) >= 2 and name != text and name in hay:
                raw_ents.setdefault(name, set()).add("人物")
                alias[text] = name
                dropped["entity_title_to_name"] += 1
            else:
                dropped["entity_title_dropped"] += 1
            continue
        # 国家名固定为国家类型，不看模型给的类型
        if is_country(text):
            if typ != "国家":
                dropped["entity_country_fixed"] += 1
            raw_ents.setdefault(text, set()).add("国家")
            continue
        raw_ents.setdefault(text, set()).add(typ)

    # 类型优先级：国家 > 机构 > 地点 > 人物 > 装备 > 作品
    TYPE_PRIORITY = {"国家": 6, "机构": 5, "地点": 4, "人物": 3, "装备": 2, "作品": 1}
    entities: list[dict] = []
    for text, types in raw_ents.items():
        if len(types) > 1:
            dropped["entity_multi_type"] += 1
        typ = max(types, key=lambda x: TYPE_PRIORITY.get(x, 0))
        entities.append({"type": typ, "text": text})

    # 事件文本必须自带施动者（在实体名里找得到）
    # 职务串不进实体表，但它确实指出了施动者，所以仍算事件成立的依据；
    # 这类事件保留，只是缺一个施动方论元
    agent_pool = set(raw_ents) | set(alias) | title_spans
    filtered_events: dict[str, dict] = {}
    for text, ev in events.items():
        # 记者会、发布会这类场景不是事件，先剔掉，免得它再去挂参会方论元
        if is_press_event(text):
            dropped["event_press_conference"] += 1
            continue
        if has_agent(text, agent_pool):
            filtered_events[text] = ev
        elif _has_actor_by_slot(ev, text, raw_ents):
            # 部署、灾伤这类句子常省略施动者：「演习在卡塔尔举行」「该厂发生爆炸」。
            # 只要文本里有本类型槽位能挂的实体（地点、装备、受灾对象），事件就成立。
            filtered_events[text] = ev
            dropped["event_agent_by_slot"] += 1
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
        mapped = event_alias.get(text, text)
        if mapped and mapped not in events:
            rejected_events.add(text)
            rejected_events.add(mapped)

    # 事件简称兜底：模型常把 head 写成事件里的专名（如演习代号「海湾安全-4」），
    # 或省掉句首的时间状语。这里为每个事件建多种等价形式，全部指向事件全文。
    span_to_event: dict[str, str] = {}
    for text in events:
        # 引号内的专名、书名号内的作品名
        for m in re.finditer(r"[「『“\"《]([^」』”\"》]{2,20})[」』”\"》]", text):
            span_to_event.setdefault(m.group(1), text)
        # 去掉句首时间/地点状语后的形式
        stripped = LEAD_NOISE.sub("", text).strip()
        if stripped and stripped != text and len(stripped) >= 8:
            span_to_event.setdefault(stripped, text)
        # 去掉所有时间词后的形式
        no_time = TIME_PHRASE.sub("", text).strip("，, ")
        if no_time and no_time != text and len(no_time) >= 8:
            span_to_event.setdefault(no_time, text)
    # 最长匹配优先，避免短串误配
    _span_keys = sorted(span_to_event, key=len, reverse=True)

    # 关系
    relations: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for rel in (parsed.get("relations") if isinstance(parsed.get("relations"), list) else []):
        if not isinstance(rel, dict):
            dropped["relation"] += 1
            continue
        name = str(rel.get("name") or "").strip()
        raw_head = str(rel.get("head") or "").strip()
        raw_tail = str(rel.get("tail") or "").strip()
        # 事件文本被扩展到原文句时，关系端点跟着改，否则会因对不上而丢弃
        raw_head = event_alias.get(raw_head, raw_head)
        raw_tail = event_alias.get(raw_tail, raw_tail)
        # 模型用事件专名代替事件全文时，改回全文
        if raw_head not in events and raw_head in span_to_event:
            dropped["relation_head_alias"] += 1
            raw_head = span_to_event[raw_head]
        elif raw_head not in events:
            for span in _span_keys:
                if span in raw_head and len(span) >= 4:
                    dropped["relation_head_alias"] += 1
                    raw_head = span_to_event[span]
                    break
        # 挂到被丢弃事件上的边先判掉。此处必须用原始文本比对：
        # 超长事件被 ATOM_MAX 裁成空串后，_keep_span 会返回空，
        # 与 rejected_events 里的原文对不上，会误落入类型不匹配分支。
        if raw_head in rejected_events or raw_tail in rejected_events:
            dropped["relation_rejected_event"] += 1
            continue
        head = _keep_span(raw_head, hay)
        tail = _keep_span(raw_tail, hay)
        evidence = _keep_span(str(rel.get("evidence") or ""), hay)
        # 职务串已并入姓名时，端点跟着换成姓名
        head = alias.get(head, head)
        tail = alias.get(tail, tail)
        if name not in DOMAIN or not head or not tail or not evidence:
            dropped["relation"] += 1
            continue
        # 端点仍是职务串（纯职务或泛称），不是实体，这条边丢弃
        if head in title_spans or tail in title_spans or title_span_kind(head) or title_span_kind(tail):
            dropped["relation_title_endpoint"] += 1
            continue
        if head in rejected_events or tail in rejected_events:
            dropped["relation_rejected_event"] += 1
            continue
        # head 不在 types_of 里说明它既不是保留事件、也不是保留实体，这条边无意义
        if head not in types_of:
            dropped["relation_untyped_end"] += 1
            continue
        # 按头端点类型在允许多义里择一支。同名可多义：
        # 「部署方」作事件论元时是 事件→机构，作装备关系时是 装备→机构
        picked = None
        for want_head, want_tail in DOMAIN[name]:
            if types_of[head] & want_head:
                picked = (want_head, want_tail)
                break
        if picked is None:
            dropped["relation_type_mismatch"] += 1
            continue
        want_head, want_tail = picked
        # 字面值槽位（伤亡数字、议题、结果等）不要求端点是实体，只校验起点
        literal = "字面值" in want_tail
        if not literal and tail not in types_of:
            dropped["relation_untyped_end"] += 1
            continue
        if not literal and not (types_of[tail] & want_tail):
            dropped["relation_type_mismatch"] += 1
            continue
        # 槽位必须是该事件类型声明过的，不再有「通用角色」例外
        ev = events.get(head)
        if ev:
            allowed = set(slot_names(ev["event_type"], ev.get("subtype") or ""))
            if name not in allowed:
                dropped["relation_slot_not_allowed"] += 1
                continue
        # 施动方类槽位不能是记者、发言人这类转述者
        if name in ACTOR_SLOTS and is_non_actor(tail):
            dropped["relation_non_actor"] += 1
            continue
        # 计划动作不产生位置与发射类边
        if name in PLANNED_FORBIDDEN:
            kind = events.get(head, {}).get("kind") or events.get(tail, {}).get("kind") or ""
            if kind == "计划" or planned_evidence(evidence):
                dropped["relation_planned"] += 1
                continue
        rec = {"name": name, "head": head, "tail": tail, "evidence": evidence}
        if name == "表态方":
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

    # 装备漏抽检查：只在事件片段里找型号，背景介绍的提及不算漏抽
    names = {e["text"] for e in entities}
    ev_text = " ".join(ev.get("text") or "" for ev in events.values())
    for m in EQUIP_PAT.finditer(ev_text):
        s = m.group(0).strip()
        if len(s) < 3 or s in names or s.upper() in EQUIP_STOP:
            continue
        if any(s in n or n in s for n in names):
            continue
        dropped["equipment_missing"] += 1

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
    # 时间归一：由 time_raw + 发稿时间推出具体日期，解不开就留空
    try:
        from graph.time import event_meta, parse_pub_date
        pub = parse_pub_date(doc.publish_time)
        anchor = pub.isoformat() if pub else None
        for ev in merged.get("events") or []:
            meta = event_meta(ev, doc.publish_time, prev_abs_day=anchor)
            day = meta.get("day") or ""
            # 「当天」「随后」这类相对表述，用前一个已解析日期或发稿日兜底
            if not day and anchor and meta.get("tlink") in ("SAME_DAY", "AFTER"):
                day = anchor
                meta["precision"] = "day"
            ev["time_norm"] = day
            ev["time_precision"] = meta.get("precision") or "unknown"
            if day:
                anchor = day
    except Exception:
        for ev in merged.get("events") or []:
            ev.setdefault("time_norm", "")
            ev.setdefault("time_precision", "unknown")
    extra = doc.extra or {}
    out = {
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
    # 译文稿：把译文与原文一并带出，建集与标注界面都要用
    if getattr(doc, "translated", False):
        out["title_orig"] = doc.title_orig or ""
        out["body_orig"] = doc.body_orig or ""
        out["body"] = doc.body or ""
        out["translated"] = True
    return out
