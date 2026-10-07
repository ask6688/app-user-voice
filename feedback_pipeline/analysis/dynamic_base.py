"""V3 dynamic issue discovery.

原则：
1. 只固定 P/W/M/D/N 五个一级模块。
2. 不预设二级 issue。
3. 每一期根据当期真实反馈动态聚类、动态命名。
4. 同一条反馈如果包含两个独立问题，可以进入两个模块。
"""

from __future__ import annotations

import re

from ..semantics import concept_name


MODULES = {
    "playback": {
        "code": "P",
        "name": concept_name("playback"),
        "module_title": "播放相关",
        "order": 1,
    },
    "web_search": {
        "code": "W",
        "name": concept_name("web_search"),
        "module_title": "网页浏览＆搜索相关",
        "order": 2,
    },
    "member": {
        "code": "M",
        "name": concept_name("member"),
        "module_title": "会员相关",
        "order": 3,
    },
    "download": {
        "code": "D",
        "name": concept_name("download"),
        "module_title": "下载相关",
        "order": 4,
    },
    "update": {
        "code": "N",
        "name": "新版本适配与问题",
        "module_title": "新版本更新后的体验问题",
        "order": 5,
    },
}


SUPPORT_NOISE = re.compile(
    r"根据您|您好[,， ]|请问还有什么可以帮|"
    r"为您升级反馈|升级反馈专员|"
    r"问题已收到|需查询|约等.*分钟|"
    r"请您提供|麻烦您提供|感谢您的|"
    r"专员进一步处理"
)

LONG_NUMBER = re.compile(r"\d{6,}")

PROBLEM_OR_REQUEST = re.compile(
    r"不能|无法|不了|失败|异常|卡顿|卡死|卡住|"
    r"黑屏|白屏|闪退|崩溃|缓冲|很慢|太慢|限速|"
    r"扣款|扣费|退款|续费|不见|找不到|没了|"
    r"不好用|难用|失效|不支持|不相关|"
    r"希望|建议|能不能|可不可以|增加|恢复|优化|"
    r"太贵|诱导|强制|没生效|未生效|不到账"
)

UPDATE_RELATION = re.compile(
    r"更新后|更新以后|更新之后|更新完|"
    r"升级后|升级以后|升级之后|升级完|"
    r"自从更新|自从升级|"
    r"更新到.*后|升级到.*后|"
    r"新版.*后|新版本.*后|"
    r"换了新版|换成新版|"
    r"下个版本|后续版本|"
    r"回退.*旧版|退回.*旧版|"
    r"以前.*现在|旧版.*新版|新版.*旧版"
)

PLAY_STRONG = re.compile(
    r"无法播放|不能播放|播放不了|播不了|播放失败|"
    r"视频打不开|打不开视频|"
    r"播放.*卡|视频.*卡|看剧.*卡|"
    r"缓冲|黑屏|花屏|"
    r"字幕|倍速|进度条|快进|快退|"
    r"画质|清晰度|hdr|4k|"
    r"播放器|投屏|片头|片尾"
)

PLAY_CONTEXT = re.compile(
    r"播放|视频|播放器|看剧|观看|看片|投屏"
)

WEB_STRONG = re.compile(
    r"搜不到|搜索不到|搜索不了|搜不出|"
    r"搜索结果|搜索框|搜索引擎|关键词|联想词|"
    r"网页打不开|浏览器打不开|网站打不开|"
    r"网页.*加载|浏览器.*加载|"
    r"链接.*解析|网址.*打不开|"
    r"地址栏|标签页"
)

WEB_CONTEXT = re.compile(
    r"网页|浏览器|网站|网址|搜索|链接|站点"
)

MEMBER_STRONG = re.compile(
    r"自动续费|自动续期|自动扣|扣款|扣费|被扣|"
    r"连续包月|代扣|退款|退费|"
    r"会员权益|权益.*(?:没|无|失效|到账)|"
    r"会员.*(?:没生效|未生效|状态异常)|"
    r"不充.*会员|开会员才能|充会员才能|"
    r"(?:开了|开通了|充了|购买了|买了).{0,5}(?:会员|vip|svip).{0,15}(?:看不了|播放不了|无法播放|解压不了|不能用|用不了)|"
    r"(?:会员|vip|svip).{0,15}(?:看不了|播放不了|无法播放|解压不了|不能用|用不了)|"
    r"必须.*会员|要.*会员|"
    r"诱导消费|强制消费|"
    r"会员.*(?:太贵|涨价|价格)"
)

MEMBER_CONTEXT = re.compile(
    r"会员|vip|svip|超级会员|白金会员"
)

DOWNLOAD_STRONG = re.compile(
    r"下载失败|无法下载|不能下载|下载不了|下不了|"
    r"下载.*慢|下载速度|下载.*限速|"
    r"限速.*(?:下载|下)|"
    r"下载任务|下载列表|"
    r"下载.*不见|下载.*找不到|"
    r"转存|取回|云添加|"
    r"上传.*(?:失败|无法|不能|限速)|"
    r"磁力|bt种子|种子下载|"
    r"有效连接"
)

DOWNLOAD_CONTEXT = re.compile(
    r"下载|取回|转存|上传|云添加|磁力|bt|种子"
)

MEMBER_CAUSAL = re.compile(
    r"(?:权益|会员).{0,15}(?:没生效|未生效|不到账|异常|不能用|用不了)"
    r".{0,15}(?:导致|所以|因此|造成)"
)

PAYWALL = re.compile(
    r"(?:下载|播放|解压|使用).{0,15}"
    r"(?:要钱|付费|收费|要.*会员|充.*会员|开.*会员)"
)

DOWNLOAD_SYMPTOM = re.compile(
    r"下载.{0,20}(?:慢|失败|不了|不动|限速|降速)"
)

PLAY_SYMPTOM = re.compile(
    r"(?:播放|视频|看剧).{0,20}"
    r"(?:卡|失败|不了|黑屏|缓冲|打不开)"
)

