"""合作方 RSS 可入库信源白名单（2026-10-02 普查）。

rss: RSS 已带全文；crawl: 需抓 HTML；rss_partial: 两者混用。
拦爬 / 空页 / 抽取失败的源不在此列。
"""

from __future__ import annotations

# RSS contentText 覆盖率 ≥ 80%
RSS_DOMAINS = frozenset(
    {
        "adf-magazine.com",
        "aerotime.aero",
        "airandspaceforces.com",
        "analisidifesa.it",
        "armytimes.com",
        "asiapacificdefencereporter.com",
        "augengeradeaus.net",
        "c4isrnet.com",
        "canadiandefencereview.com",
        "defence-industry.eu",
        "defensenews.com",
        "defenseone.com",
        "defensescoop.com",
        "euromaidanpress.com",
        "forte.jor.br",
        "galaxiamilitar.es",
        "greekcitytimes.com",
        "marinecorpstimes.com",
        "militarytimes.com",
        "militarywatchmagazine.com",
        "naval.com.br",
        "navalnews.com",
        "navytimes.com",
        "reportdifesa.it",
        "savunmasanayist.com",
        "seapowermagazine.org",
        "soldat-und-technik.de",
        "taskandpurpose.com",
        "theaviationist.com",
        "thesentry.org",
        "twz.com",
        "ukdefencejournal.org.uk",
    }
)

# RSS 无全文，HTML 抽样可爬
CRAWL_DOMAINS = frozenset(
    {
        "airdatanews.com",
        "armscontrol.org",
        "asianmilitaryreview.com",
        "bundeswehr-journal.de",
        "correctiv.org",
        "defenceturkey.com",
        "defence24.com",
        "defence24.pl",
        "defenceweb.co.za",
        "defensa.com",
        "defensedaily.com",
        "defensemirror.com",
        "digital-commons.usnwc.edu",
        "en.interfax.com.ua",
        "en.mehrnews.com",
        "esut.de",
        "euro-sd.com",
        "fao.org",
        "forcesoperations.com",
        "gcaptain.com",
        "gov.uk",
        "hartpunkt.de",
        "indiastrategic.in",
        "infodefensa.com",
        "intelnews.org",
        "jpost.com",
        "krebsonsecurity.com",
        "kyivindependent.com",
        "kyivpost.com",
        "livefistdefence.com",
        "militaryembedded.com",
        "mwi.westpoint.edu",
        "navaltoday.com",
        "navylookout.com",
        "nzdf.mil.nz",
        "opex360.com",
        "responsiblestatecraft.org",
        "rusi.org",
        "spacenews.com",
        "strategypage.com",
        "suasnews.com",
        "tass.com",
        "ukrinform.net",
        "ynetnews.com",
    }
)

# RSS 部分有全文，缺的可补爬
PARTIAL_DOMAINS = frozenset(
    {
        "airforcetimes.com",
        "cimsec.org",
        "defesanet.com.br",
        "military.africa",
        "nordicdefencereview.com",
        "swp-berlin.org",
    }
)

# 403 / robots / JS 空页 / 抽取失败，不入库
BLOCKED_DOMAINS = frozenset(
    {
        "defence-blog.com",
        "en.topwar.ru",
        "english.nv.ua",
        "globalinitiative.net",
        "insidedefense.com",
        "sputnikglobe.com",
    }
)

ALLOW_DOMAINS = RSS_DOMAINS | CRAWL_DOMAINS | PARTIAL_DOMAINS

# 普查多数语言为英语的源（--lang en 时只收这些域名）
EN_DOMAINS = frozenset(
    {
        "adf-magazine.com",
        "aerotime.aero",
        "airandspaceforces.com",
        "airdatanews.com",
        "airforcetimes.com",
        "armscontrol.org",
        "armytimes.com",
        "asianmilitaryreview.com",
        "asiapacificdefencereporter.com",
        "c4isrnet.com",
        "canadiandefencereview.com",
        "cimsec.org",
        "defence-industry.eu",
        "defenceturkey.com",
        "defence24.com",
        "defenceweb.co.za",
        "defensedaily.com",
        "defensemirror.com",
        "defensenews.com",
        "defenseone.com",
        "defensescoop.com",
        "digital-commons.usnwc.edu",
        "en.interfax.com.ua",
        "en.mehrnews.com",
        "euromaidanpress.com",
        "euro-sd.com",
        "fao.org",
        "gcaptain.com",
        "gov.uk",
        "greekcitytimes.com",
        "indiastrategic.in",
        "intelnews.org",
        "jpost.com",
        "krebsonsecurity.com",
        "kyivindependent.com",
        "kyivpost.com",
        "livefistdefence.com",
        "marinecorpstimes.com",
        "military.africa",
        "militaryembedded.com",
        "militarytimes.com",
        "militarywatchmagazine.com",
        "mwi.westpoint.edu",
        "navalnews.com",
        "navaltoday.com",
        "navytimes.com",
        "navylookout.com",
        "nordicdefencereview.com",
        "nzdf.mil.nz",
        "responsiblestatecraft.org",
        "rusi.org",
        "seapowermagazine.org",
        "spacenews.com",
        "strategypage.com",
        "suasnews.com",
        "tass.com",
        "taskandpurpose.com",
        "theaviationist.com",
        "thesentry.org",
        "twz.com",
        "ukdefencejournal.org.uk",
        "ukrinform.net",
        "ynetnews.com",
    }
)


def domain_mode(domain: str) -> str | None:
    if domain in RSS_DOMAINS:
        return "rss"
    if domain in CRAWL_DOMAINS:
        return "crawl"
    if domain in PARTIAL_DOMAINS:
        return "rss_partial"
    if domain in BLOCKED_DOMAINS:
        return "blocked"
    return None
