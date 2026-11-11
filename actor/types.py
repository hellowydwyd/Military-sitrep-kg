"""表面名分类：规则先切，剩高频名可再问 LLM。"""

from __future__ import annotations

import json
import re

from actor.config import TYPES

COUNTRIES = {
    "中国",
    "美国",
    "俄罗斯",
    "乌克兰",
    "以色列",
    "伊朗",
    "叙利亚",
    "土耳其",
    "日本",
    "韩国",
    "朝鲜",
    "巴基斯坦",
    "沙特",
    "也门",
    "黎巴嫩",
    "巴勒斯坦",
    "埃及",
    "卡塔尔",
    "阿联酋",
    "阿曼",
    "巴林",
    "科威特",
    "伊拉克",
    "约旦",
    "阿富汗",
    "印度",
    "菲律宾",
    "越南",
    "印尼",
    "印度尼西亚",
    "澳大利亚",
    "英国",
    "法国",
    "德国",
    "波兰",
    "意大利",
    "西班牙",
    "加拿大",
    "巴西",
    "南非",
    "苏丹",
    "马里",
    "刚果",
    "尼日尔",
    "索马里",
    "利比亚",
    "希腊",
    "瑞典",
    "芬兰",
    "挪威",
    "丹麦",
    "荷兰",
    "比利时",
    "奥地利",
    "匈牙利",
    "罗马尼亚",
    "保加利亚",
    "捷克",
    "斯洛伐克",
    "白俄罗斯",
    "摩尔多瓦",
    "格鲁吉亚",
    "亚美尼亚",
    "阿塞拜疆",
    "哈萨克斯坦",
    "乌兹别克斯坦",
    "土库曼斯坦",
    "泰国",
    "马来西亚",
    "新加坡",
    "柬埔寨",
    "老挝",
    "缅甸",
    "孟加拉国",
    "尼泊尔",
    "斯里兰卡",
    "墨西哥",
    "阿根廷",
    "智利",
    "新西兰",
    "瑞士",
    "葡萄牙",
    "爱尔兰",
    "爱沙尼亚",
    "拉脱维亚",
    "立陶宛",
    "克罗地亚",
    "塞尔维亚",
    "波黑",
    "阿尔巴尼亚",
    "北马其顿",
    "科索沃",
    "塞浦路斯",
    "马耳他",
    "冰岛",
    "卢森堡",
    "摩纳哥",
    "梵蒂冈",
    "突尼斯",
    "阿尔及利亚",
    "摩洛哥",
    "埃塞俄比亚",
    "肯尼亚",
    "坦桑尼亚",
    "乌干达",
    "卢旺达",
    "加纳",
    "尼日利亚",
    "安哥拉",
    "莫桑比克",
    "津巴布韦",
    "赞比亚",
    "博茨瓦纳",
    "纳米比亚",
    "马达加斯加",
    "毛里求斯",
    "塞舌尔",
    "吉布提",
    "厄立特里亚",
    "乍得",
    "中非",
    "喀麦隆",
    "加蓬",
    "刚果（金）",
    "刚果（布）",
    "南苏丹",
    "赤道几内亚",
    "几内亚",
    "塞内加尔",
    "科特迪瓦",
    "布基纳法索",
    "贝宁",
    "多哥",
    "塞拉利昂",
    "利比里亚",
    "冈比亚",
    "几内亚比绍",
    "佛得角",
    "圣多美和普林西比",
    "科摩罗",
    "莱索托",
    "斯威士兰",
    "马拉维",
    "布隆迪",
    "阿曼苏丹国",
    "沙特阿拉伯",
    "阿拉伯联合酋长国",
}

COUNTRY_ALIAS = {
    "中方": "中国",
    "美方": "美国",
    "俄方": "俄罗斯",
    "乌方": "乌克兰",
    "以方": "以色列",
    "日方": "日本",
    "韩方": "韩国",
    "伊方": "伊朗",
    "叙方": "叙利亚",
    "土方": "土耳其",
    "英方": "英国",
    "法方": "法国",
    "德方": "德国",
    "朝方": "朝鲜",
    "US": "美国",
    "USA": "美国",
    "U.S": "美国",
    "U.S.": "美国",
    "America": "美国",
    "Russia": "俄罗斯",
    "Ukraine": "乌克兰",
    "Iran": "伊朗",
    "Israel": "以色列",
    "China": "中国",
    "Japan": "日本",
    "Iraq": "伊拉克",
    "Yemen": "也门",
    "Singapore": "新加坡",
    "Argentina": "阿根廷",
}

ORG_SUFFIX = (
    "国防军",
    "武装部队",
    "革命卫队",
    "自卫队",
    "司令部",
    "参谋部",
    "防卫省",
    "国防部",
    "外交部",
    "国防大学",
    "联合参谋本部",
    "过渡政府",
    "通信局",
    "委员会",
    "理事会",
    "办公室",
    "总参谋部",
    "舰队",
    "空军",
    "海军",
    "陆军",
    "海警",
    "边防",
    "卫队",
    "部队",
    "军方",
    "武装",
    "政党",
    "联盟",
    "组织",
    "公司",
    "集团",
    "电视台",
    "通讯社",
    "报社",
    "研究所",
    "大学",
    "政府",
    "议会",
    "国会",
    "白宫",
    "克里姆林宫",
    "五角大楼",
    "北约",
)
ORG_SEED = {
    "北约",
    "真主党",
    "哈马斯",
    "胡塞武装",
    "胡塞",
    "联黎部队",
    "联黎",
    "联合国",
    "安理会",
    "欧盟",
    "非盟",
    "阿盟",
    "上合组织",
    "东盟",
    "国际原子能机构",
    "红十字会",
    "红新月会",
    "美军",
    "俄军",
    "乌军",
    "以军",
    "日军",
    "韩军",
    "伊军",
    "叙军",
    "黎军",
    "德军",
    "科军",
    "朝军",
    "土军",
    "自卫队",
    "日本自卫队",
    "沙特军队",
    "英军",
    "法军",
    "解放军",
    "中国海警",
    "日本防卫省",
    "防卫省",
    "韩军联参",
    "联参",
}
PLACE_ENDS = (
    "国际机场",
    "机场",
    "航站楼",
    "空军基地",
    "海军基地",
    "基地",
    "港口",
    "码头",
    "海峡",
    "附近地区",
    "地区",
    "海域",
    "空域",
    "居住区",
    "油田",
    "炼油厂",
    "大桥",
    "桥梁",
    "阵地",
    "设施",
)
ORG_ENDS = (
    "大使馆",
    "领事馆",
    "办公室",
    "委员会",
    "大学",
    "学院",
    "司令部",
    "总部",
    "参谋部",
    "卫队",
    "防卫省",
    "国防部",
    "外交部",
    "联合参谋本部",
)
PERSON_HINT = (
    "总统",
    "总理",
    "首相",
    "防长",
    "外长",
    "大臣",
    "部长",
    "国王",
    "王储",
    "领袖",
    "主席",
    "书记",
    "司令",
    "参谋长",
    "舰长",
    "大使",
    "特使",
    "发言人",
    "议员",
    "议员",
    "上校",
    "将军",
    "元帅",
    "陛下",
)
PERSON_SEED = {
    "特朗普",
    "泽连斯基",
    "内塔尼亚胡",
    "普京",
    "拜登",
    "马斯克",
    "高市早苗",
    "李在明",
    "尹锡悦",
    "金正恩",
    "董军",
    "巴加埃",
    "阿拉格齐",
    "哈梅内伊",
    "佩泽希齐扬",
    "埃尔多安",
    "马克龙",
    "斯塔默",
    "朔尔茨",
    "冯德莱恩",
    "泽连斯基",
}
GENERIC_MIL = {
    "无人机",
    "导弹",
    "战机",
    "飞机",
    "防空系统",
    "防空警报",
    "雷达",
    "弹药",
    "武器",
    "军舰",
    "坦克",
    "火炮",
}
MIL_HINT = (
    "航母",
    "驱逐舰",
    "护卫舰",
    "潜艇",
    "爱国者",
    "萨德",
    "铁穹",
    "铁束",
    "F-35",
    "F-16",
    "苏-35",
    "歼-20",
    "轰-6",
    "运-20",
    "MQ-9",
    "阿帕奇",
    "核潜艇",
)


def country_of(text: str) -> str | None:
    t = (text or "").strip()
    if t in COUNTRIES:
        return t
    if t in COUNTRY_ALIAS:
        mapped = COUNTRY_ALIAS[t]
        if mapped in COUNTRIES:
            return mapped
    return None


def rule_type(text: str, role: str = "") -> str | None:
    t = (text or "").strip()
    if not t:
        return None
    if role == "装备":
        if t in GENERIC_MIL:
            return None
        return "MilitaryEntity"
    if any(x in t for x in ("号航母", "号驱逐舰", "号护卫舰", "号潜艇")) or t.endswith("号"):
        return "MilitaryEntity"
    if country_of(t) and t not in ORG_SEED and not t.endswith("军"):
        return "Country"
    if t.endswith("令") and not t.endswith("司令") or t.endswith(("协议", "条约", "协定", "备忘录", "法案", "决议", "命令", "声明", "文件")):
        if "·" not in t:
            return "Document"
    if t.endswith(("会议", "会谈", "演习")) and "·" not in t:
        return None
    if any(t.endswith(s) for s in PLACE_ENDS):
        return "Location"
    if any(t.endswith(s) for s in ORG_ENDS):
        return "Org"
    if any(h in t for h in PERSON_HINT) and len(t) <= 28:
        return "Person"
    if t in PERSON_SEED or (("·" in t or "•" in t) and "号" not in t):
        return "Person"
    if t in ORG_SEED or any(t.endswith(s) for s in ORG_SUFFIX):
        return "Org"
    if t.endswith("军") and len(t) <= 8:
        return "Org"
    if any(h in t for h in MIL_HINT) and role != "主体":
        return "MilitaryEntity"
    if re.search(r"[A-Z]{2,}|[A-Z]\-\d", t) and any(h in t for h in MIL_HINT):
        return "MilitaryEntity"
    return None


def llm_types(items: list[dict]) -> dict[str, str]:
    """items: name, titles。返回 name→type。失败抛错。"""
    from extract.llm import chat_json

    system = (
        "你给军事新闻里的名称分类。只输出 JSON。"
        "类型只能是 Person、Org、Country、MilitaryEntity。"
        "Country 是国家/政治实体；Org 是军队、部委、政党、公司；"
        "Person 是公开人物；MilitaryEntity 是具体装备、舰艇、番号。"
        "国家与该国军队不要都标成 Country：美军是 Org。"
        '{"labels":[{"name":"...","type":"Org"}]}'
    )
    payload = {
        "names": [
            {"name": x["name"], "titles": (x.get("titles") or [])[:3]} for x in items
        ]
    }
    raw = chat_json(system, json.dumps(payload, ensure_ascii=False), temperature=0.0, timeout=120)
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).removesuffix("```").strip()
    data = json.loads(text)
    rows = data.get("labels") if isinstance(data, dict) else data
    out: dict[str, str] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        typ = str(row.get("type") or "").strip()
        if name and typ in TYPES:
            out[name] = typ
    return out
