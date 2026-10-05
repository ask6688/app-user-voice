"""Build actionable display scenes from existing issues and routed evidence.

No source records, module assignments or clustering results are rewritten.
Unknown scenes are audited instead of being merged by title similarity.
"""
import hashlib
import math
import re
from collections import Counter, defaultdict


SERVICE = re.compile(
    r"帮您(?:排查|反馈|处理)|请(?:问|您|提供|描述|补充).{0,25}"
    r"(?:提示|截图|录屏|账号|信息|问题)|感谢您的(?:理解|等待)|"
    r"您的问题需升级|专员会|正在查询中|请您耐心等待|"
    r"您好.{0,10}已接入人工|需转接至专员|请稍等.{0,15}验证|"
    r"您是.{0,30}(?:还是|吗|呢)|请保持对话|祝您生活愉快|"
    r"建议您|我们.{0,12}优化|希望.{0,8}给您带来"
)
PC = re.compile(r"电脑端|电脑版|Windows|Mac端|Mac版|\bPC\b", re.I)
MOBILE = re.compile(r"手机|移动端|安卓|Android|iOS|iPhone|iPad|鸿蒙", re.I)
DOMAIN = re.compile(r"视频|影片|电影|播放|播放器|看剧|短剧|字幕|投屏|画质|清晰度|HDR", re.I)
FAILURE = re.compile(r"无法|不能(?!可以)|不了|失败|异常|打不开|不见|消失|缺失|没有了|卡住|灰色|强制|不支持|不让|关不|搜不到|找不到|加载不出|卡顿|黑屏|一直缓冲|一卡一卡|很卡|会卡", re.I)
REQUEST = re.compile(r"希望|建议|能否|能不能|增加|新增|支持.*功能|可以.*(?:增加|支持|关闭)")
PAID = re.compile(r"(?:充值|开通|买|充|冲).{0,8}(?:会员|VIP)|(?:会员|VIP).{0,8}(?:买了|充值|付费)|花钱买|已开会员|(?:充|冲)了之后", re.I)
NEGATED_PAID = re.compile(r"不买|没买|没有买|未开通|没开|不开|让我买|需要买|非会员|得充")

# Specific product objects precede generic playback. Each row declares the
# user goal, handling path and experience phase, not merely a synonym title.
SCENES = [
    ("cast", r"投屏", "投屏观看", "投屏连接", "连接设备", "投屏异常", "设备搜索失败或无法连接。", 2),
    ("subtitle", r"字幕", "读取字幕", "字幕服务", "字幕显示", "字幕异常", "字幕缺失、加载失败或显示异常。", 2),
    ("quality", r"画质|原画|清晰度|HDR|色彩", "选择画质", "画质渲染", "画质设置", "画质异常", "清晰度选择、HDR显示或画质设置异常。", 2),
    ("controls", r"倍速|快进|快退|进度条|全屏|控件", "控制播放", "播放器交互", "播放操作", "播放控件及进度操作异常", "播放进度、倍速或控件无法正常操作。", 2),
    ("capture", r"视频截图|截图功能|截取视频画面", "保存视频画面", "播放器截图", "播放操作", "视频截图功能异常", "视频截图功能无法正常使用。", 2),
    ("music", r"音乐播放器", "播放音乐", "音乐播放功能", "功能供给", "音乐播放器", "用户希望增加音乐播放能力。", 1),
]
SCENES = [(key, re.compile(pattern, re.I), *rest) for key, pattern, *rest in SCENES]
STALL = re.compile(r"卡顿|黑屏|卡死|卡画面|一卡一卡|一直缓冲|缓冲.{0,6}(?:不动|很久)|播放.{0,8}(?:很卡|会卡)")
PLAY_FAILURE = r"(?:无法|不能|没法)(?:观看|播放|看)|(?:观看|播放|看|播)不了|播放失败"
OPEN_FAIL = re.compile(PLAY_FAILURE + r"|打不开|视频.*无法打开")

# Conjunction of feature and symptom, ordered from specific handling paths to
# broad failures. A matching module is supplied by analysis, never inferred here.
MODULE_SCENES = {
    "member": [
        (r"自动续费|自动扣|连续包月", r"取消|关闭|退订", r"找不到|无法|不能|不了|没有.*入口", "管理续费", "续费取消", "取消", "自动续费取消失败或入口难找", 3),
        (r"自动续费|自动扣|连续包月|未申请续费", r"不知情|未|忘|扣|退|取消|没有.*提醒", "管理续费", "续费授权", "扣款", "自动续费及扣款争议", 3),
        (r"会员|VIP|充值|付费", r"解压", r"无法|不能|不了|失败|异常|需要密码", "使用解压", "会员解压权益", "权益兑现", "付费后解压功能仍无法使用", 3),
        (r"会员|VIP|充值|付费|充了钱", r"无法播放|不能播放|看不了|播不了|观看不了|打不开视频", "观看视频", "会员权益", "权益兑现", "会员权益无法播放", 3),
        (r"(?:会员|VIP|充值|付款)(?:(?!退款|退费|退钱).){0,12}(?:未到账|没到账|不到账|未生效|没生效|不生效|没有开通|用不了|不能用)", "获得会员权益", "订购到账", "开通", "会员订购未生效或权益无法使用", 3),
        (r"退款|退费|退钱", r"没到账|未到账|不到账|迟迟|无法|不能|不了|失败|拒绝", "退回费用", "退款处理", "退款", "会员退款失败或迟迟未到账", 3),
        (r"诱导|误导|虚假宣传|误触|强制消费", r"会员|付费|充值|消费|广告|退款|退费", "购买会员", "付费引导", "购买", "会员付费引导争议", 2),
        (r"解压|基础功能", r"需要会员|要会员|必须.*会员|付费|收费|会员门槛", "使用基础功能", "付费门槛", "使用前", "解压等基础功能存在会员门槛", 2),
    ],
    "download": [
        (r"下载|取回", r"后台|锁屏|熄屏", r"停止|暂停|断|失败|无法|不能|慢", "后台下载", "后台任务保活", "传输中", "后台或锁屏后下载中断", 3),
        (r"下载|取回", r"不能暂停|无法暂停|不能删除|无法删除|任务卡死|任务卡住", "管理下载任务", "任务控制", "任务操作", "下载任务卡死或无法暂停删除", 3),
        (r"下载|取回", r"限速|降速|锁死|速度.*(?:为零|是0|为0)|0[Bb]/[Ss]", "获取文件", "传输速度", "传输中", "下载限速或中途降速", 3),
        (r"下载|取回", r"慢|龟速|速度.*(?:低|不稳定|达不到|不满)|耗时|几天", "获取文件", "传输吞吐", "传输中", "下载速度过慢或不稳定", 2),
        (r"下载|取回", r"文件.*(?:损坏|不完整)|校验失败|缺少文件", "获取完整文件", "文件完整性", "下载完成", "下载文件损坏或不完整", 3),
        (r"下载|取回", r"找不到|路径.*(?:不见|消失)|文件.*不见", "查找已下载文件", "本地保存", "下载完成", "下载后文件或保存路径找不到", 2),
        (r"转存|云添加|取回", r"失败|不了|不能|无法|卡住|进度不动", "转存文件", "云端任务", "任务创建", "转存、云添加或取回失败", 3),
        (r"下载|磁力|种子", r"失败|无法下载|不能下载|下载不了|下不了|无法识别|不识别|无法开始", "获取文件", "下载任务", "任务创建", "下载失败或无法下载", 3),
    ],
    "web_search": [
        (r"关键词|搜索词|搜索框", r"重复", "输入搜索词", "搜索输入", "输入", "搜索关键词重复异常", 2),
        (r"搜索|搜不到|搜不出", r"搜不到|搜不出|搜索不到|失败|打不开|没有结果|不了|无结果", "查找资源", "搜索服务", "获取结果", "搜索异常或搜不到资源", 3),
        (r"网页|网站|网址|浏览器|链接", r"打不开|无法打开|不能打开|加载失败|解析失败|一直.*加载|转圈", "浏览网页", "网页加载", "打开页面", "网页或链接无法正常打开", 3),
        (r"浏览器|网页", r"闪退|崩溃|卡死", "浏览网页", "浏览器运行", "浏览中", "网页浏览时闪退或卡死", 3),
    ],
    "update": [
        (r"字幕", r"改名|文件名.*变|名字.*改", "保存字幕", "版本回归/字幕", "更新后", "新版下载字幕后文件名被修改", 2),
        (r"更新|升级|新版", r"闪退|崩溃|卡顿|卡死|黑屏", "正常使用应用", "版本回归/稳定性", "更新后", "更新后卡顿、闪退或性能下降", 3),
        (r"下载|种子|取回", r"失败|不了|不能|无法|丢失", "获取文件", "版本回归/下载", "更新后", "更新后下载功能异常", 3),
        (r"视频|播放|字幕|投屏", r"失败|不了|不能|无法|消失|不见|缺失", "观看视频", "版本回归/播放", "更新后", "更新后播放相关功能异常", 3),
        (r"文件|记录|入口|功能|视频", r"找不到|不见|消失|丢失|缺失", "查找原有内容", "版本回归/内容入口", "更新后", "更新后原有文件、功能或入口缺失", 2),
    ],
}
MODULE_SCENES = {module: [(tuple(re.compile(p, re.I) for p in row[:-5]), *row[-5:]) for row in rows]
                 for module, rows in MODULE_SCENES.items()}

# Concrete proposals have their own action key; they never merge with failures.
SUGGESTIONS = {
    "member": [(r"续费|扣款", r"提醒|通知", "续费提醒"), (r"会员", r"家庭共享|家庭套餐", "会员家庭共享")],
    "download": [(r"下载", r"批量", "批量下载"), (r"下载", r"定时|计划", "定时下载"),
                 (r"下载", r"保存路径|选择路径|自选目录", "自选下载保存路径")],
    "web_search": [(r"标签页", r"新建|新标签|后台打开", "在新标签页打开链接"),
                   (r"浏览器", r"工作区|标签页.*归档", "浏览器工作区管理"),
                   (r"书签", r"导出|同步", "书签导出或同步")],
    "update": [(r"旧版本|旧版", r"退回|切换|回退", "回退旧版本"),
               (r"更新|版本", r"关闭自动更新|手动更新", "手动控制版本更新")],
}

RENEWAL = re.compile(r"自动续费|自动扣(?:费|款)|连续包月|续费提醒|续费通知")
UNRELATED = re.compile(r"另外|另一个|无关|不是自动|单次购买|单笔购买|一次性购买")

# Titles are composed from observed feature/symptom pairs, with supporting IDs.
# These describe product capabilities, not issue names or period-specific text.
OBSERVATIONS = [
    (r"原画|原始画质", r"不能|无法|不了|不开", "原画无法播放"),
    (r"2160[Pp]", r"不能|无法|不了|不开", "2160P视频无法播放"),
    (r"清晰度|画质", r"无法选择|不能选择|无法切换|不能切换|调节不了|灰色", "清晰度无法选择"),
    (r"HDR", r"异常|偏色|发白|发灰|不正常", "HDR显示异常"),
    (r"色彩", r"异常|偏色|发白|发灰|不正常", "色彩显示异常"),
    (r"臻彩|真彩", r"强制|关不|不能关|无法关", "臻彩画质强制开启"),
    (r"字幕", r"加载失败|加载不出|添加不了|不能使用", "字幕加载或添加失败"),
    (r"字幕", r"没有了|消失|不见|缺失", "字幕缺失"),
    (r"字幕", r"卡住|不同步|错位", "字幕卡住或不同步"),
    (r"字幕", r"关闭", r"找不到|无法|不能", "字幕无法关闭"),
    (r"投屏", r"搜索不到|搜不到|找不到|无法收到", "投屏设备搜索失败"),
    (r"投屏", r"连接不上|无法连接|不能连接", "投屏无法连接"),
    (r"投屏", r"不能|无法|失败", "投屏无法使用"),
    (r"下载.{0,12}文件|下载.{0,12}视频|本地文件|保存路径", r"找不到|不见|消失|丢失", "下载文件或保存路径缺失"),
    (r"播放.{0,8}入口|播放按钮", r"没有|缺失|消失|不见|找不到", "播放入口缺失"),
    (r"播放|视频", PLAY_FAILURE, "视频无法播放"),
    (r"下载|种子|取回", r"失败|下不了|下载不了|不能下载|无法下载", "下载失败"),
    (r"下载框|下载窗口|下载按钮", r"无法|不能|不弹|没有|不出现|灰色", "下载入口无法正常唤起"),
    (r"种子|下载任务", r"丢失|遗漏|漏下", "批量下载任务遗漏"),
    (r"历史记录|播放记录", r"找不到|不见|消失|缺失", "播放历史记录缺失"),
    (r"收藏|书签", r"找不到|不见|消失|缺失", "收藏或书签入口缺失"),
]
OBSERVATIONS = [(tuple(re.compile(p, re.I) for p in row[:-1]), row[-1]) for row in OBSERVATIONS]
DETAILED_PATHS = {"画质渲染", "默认画质设置", "字幕服务", "投屏连接", "版本回归/播放", "版本回归/下载", "版本回归/内容入口"}


def observations(fragment, path):
    labels = []
    for patterns, label in OBSERVATIONS:
        for clause in re.split(r"[；;。！？!?\n]", fragment):
            matches = [list(p.finditer(clause)) for p in patterns]
            if all(matches) and any(all(any(abs(m.start() - anchor.start()) <= 60 for m in options)
                                       for options in matches[1:]) for anchor in matches[0]):
                labels.append(label)
                break
    # The same text may mention several capabilities; a label must describe
    # this group's path rather than an unrelated part of the conversation.
    domains = {"画质渲染": r"原画|2160P|清晰度|HDR|色彩|臻彩", "字幕服务": r"字幕",
               "默认画质设置": r"臻彩",
               "投屏连接": r"投屏", "版本回归/播放": r"播放|字幕|投屏",
               "版本回归/下载": r"下载", "版本回归/内容入口": r"缺失"}
    labels = [label for label in labels if re.search(domains.get(path, r"."), label)]
    if len(labels) > 1 and "投屏无法使用" in labels:
        labels.remove("投屏无法使用")
    if "HDR显示异常" in labels and "色彩显示异常" in labels:
        labels.remove("色彩显示异常")
    return labels


def renewal_context(fragment, record):
    if UNRELATED.search(fragment):
        return False
    if RENEWAL.search(fragment):
        return True
    context = display_text(record)
    if RENEWAL.search(context) and not UNRELATED.search(context):
        return True
    parts = re.split(r"[；;\n]+", context)
    for index, part in enumerate(parts):
        if fragment in part:
            nearby = "；".join(parts[max(0, index - 1):index + 2])
            return bool(RENEWAL.search(nearby) and not UNRELATED.search(nearby))
    return False


def business_scene(scene, fragment, record, module):
    goal, path, phase, kind, title, description, severity = scene
    if module == "member" and (path in {"续费取消", "续费授权", "续费提醒"} or
                               path in {"退款处理", "付费引导"} and renewal_context(fragment, record)):
        return ("管理订阅费用", "自动续费", "订阅生命周期", "争议", "自动续费及扣款争议", "", 3)
    if module == "playback" and path == "画质渲染" and re.search(r"臻彩|真彩", fragment) and re.search(r"强制|关不|不能关|无法关", fragment):
        return ("设置默认画质", "默认画质设置", "画质设置", kind, title, description, severity)
    if module == "download" and path in {"传输速度", "传输吞吐"}:
        return ("获取文件", "下载速度", "传输中", kind, "下载速度过慢、限速或中途降速", "", 3)
    if module == "download" and path == "云端任务" and re.search(r"(?:转存|云添加|取回).{0,20}(?:导致|所以|因此|后).{0,12}(?:下载失败|无法下载|下载不了)", fragment):
        return ("获取文件", "下载任务", "任务创建", kind, "下载失败或无法下载", "", 3)
    if module == "update" and "下载文件或保存路径缺失" in observations(fragment, "版本回归/内容入口"):
        return ("查找已下载文件", "版本回归/内容入口", "更新后", kind, title, description, severity)
    return scene


def score_issue(issue, severity=None, information=None):
    count = issue.get("record_count", 0)
    if severity is None:
        title = issue.get("name", "")
        severity = 1 if title.startswith("建议") else 3 if re.search(r"无法|失败|权益|安全|账号|卡顿|黑屏", title) else 2
    if information is None:
        evidence = issue.get("evidence_samples") or [q["quote"] for q in issue.get("quotes", [])]
        information = sum(min(1, len(t.strip()) / 24) for t in evidence) / max(1, len(evidence))
    score = round(min(40, 10 * math.log2(1 + count)) + severity * 15 + information * 15, 1)
    issue.update(quality_score=score, priority="高" if severity == 3 else "中" if severity == 2 else "低",
                 display_reason=f"{count}条去重反馈；{issue.get('feedback_kind', '既有报告准入')}；信息明确度{information:.0%}")
    return issue


def display_text(record):
    text = str(record.get("text") or "")
    if record.get("source") != "customer_service":
        return text
    # A report-only view: retain every non-template user segment, while the
    # complete original text/dialogue remains untouched in analysis.records.
    return "；".join(s for s in re.split(r"[；;\n]+", text) if s.strip() and not SERVICE.search(s))


class IssueReportProcessor:
    """Shared selection, evidence, merging and ranking for five report modules."""

    def __init__(self, records, routing=(), max_issues=10, negative_ids=None):
        self.records = records
        self.routing = routing
        self.max_issues = max_issues
        self.negative_ids = set(negative_ids) if negative_ids is not None else {rid for rid, r in records.items() if r["source"] in ("ios", "android") and r.get("rating") in (1, 2, 3)}
        self.audit = {"merged": [], "filtered": [], "deferred": []}

    def _scene(self, fragment, record, module):
        text = str(record.get("text") or "")
        if re.search(r"(?:没有|并无|未发现).{0,3}(?:问题|异常|故障)|已经恢复正常", fragment) and not re.search(r"但|仍|还是", fragment):
            return None, "仅描述正常或已恢复状态，无当前具体问题"
        if SERVICE.search(fragment):
            return None, "客服模板或角色不明确，不用于摘要和原文展示"
        if (PC.search(fragment) or PC.search(text)) and not (MOBILE.search(text) or record.get("source") in ("ios", "android")):
            return None, "PC端或多端混杂，无法确认该条原文为纯移动端问题"
        if module != "playback":
            return self._module_scene(fragment, module)
        if re.search(r"电视端|游戏|读取照片|上传文件", fragment) and not re.search(r"播放|播放器", fragment):
            return None, "不是移动端播放场景"
        if "电视端" in text and not MOBILE.search(text):
            return None, "TV端反馈"
        if re.search(r"游戏|上传|读取照片", fragment) and not re.search(r"播放|播放器", fragment):
            return None, "不是播放场景"
        request = bool(REQUEST.search(fragment))
        fault = bool(FAILURE.search(REQUEST.sub("", fragment)))
        # Only bind a purchase to playback failure when both occur in the
        # same routed evidence; an unrelated membership mention is not a cause.
        paid = any(not NEGATED_PAID.search(fragment[max(0, m.start()-4):m.end()])
                   for m in PAID.finditer(fragment))
        has_domain = bool(DOMAIN.search(fragment) or DOMAIN.search(text))
        if OPEN_FAIL.search(fragment) and paid and has_domain:
            return ("观看视频", "会员权益", "权益兑现", "故障", "会员权益无法播放", "用户反馈付费开通会员后仍无法观看视频。", 3), None
        specific = [scene for scene in SCENES if scene[1].search(fragment)]
        # Stuttering while seeking is a playback interruption, not a generic
        # controls issue. Subtitle/cast/quality failures keep their own path.
        if STALL.search(fragment) and DOMAIN.search(fragment) and not any(s[0] in ("cast", "subtitle", "quality") for s in specific):
            return ("观看视频", "播放渲染", "播放中", "故障", "播放卡顿/黑屏", "播放过程中出现卡顿、黑屏或缓冲异常。", 3), None
        if specific:
            key, _, goal, path, phase, title, description, severity = specific[0]
            if request and (not fault or re.search(r"增加|新增|永久关闭|导出|提取|保存", fragment)):
                # Requests with different actions must not collapse into one
                # feature bucket (e.g. subtitle export vs subtitle translation).
                actions = re.findall(r"音乐播放器|热度曲线|从头播放|永久关闭.{0,5}画质|(?:字幕|画质).{0,6}(?:下载|提取|保存|翻译)|(?:下载|提取|保存|翻译).{0,6}字幕", fragment)
                if not actions:
                    return None, "建议缺少明确的功能动作，保留后台待审"
                action = actions[0]
                return (goal, path + ":" + action, "功能供给", "建议", "建议支持" + action, "用户提出明确的功能需求，与故障分开展示。", 1), None
            if fault and key != "music":
                return (goal, path, phase, "故障", title, description, severity), None
        if OPEN_FAIL.search(fragment) and has_domain:
            return ("观看视频", "播放器/内容", "开始播放", "故障", "视频无法播放", "用户反馈视频无法打开、播放失败或无法观看。", 3), None
        return None, "缺少明确播放故障或可行动建议，保留后台"

    def _module_scene(self, fragment, module):
        if module == "member" and re.search(r"登录|换绑|验证码|注册", fragment) and not re.search(r"会员|退款|付费|充值", fragment):
            return None, "账号操作不属于会员模块"
        if module == "download" and re.search(r"违规|违禁|解封", fragment):
            return None, "风控申诉不是下载故障"
        if module == "update" and (not re.search(r"更新|升级|新版|版本", fragment) or re.search(r"电视剧.*更新|更新.*剧集", fragment)):
            return None, "缺少应用版本变更证据"
        if REQUEST.search(fragment):
            for feature, action, title in SUGGESTIONS[module]:
                if re.search(feature, fragment) and re.search(action, fragment):
                    return (title, title, "功能供给", "建议", "建议支持" + title, "", 1), None
        for patterns, goal, path, phase, title, severity in MODULE_SCENES[module]:
            if all(pattern.search(fragment) for pattern in patterns):
                return (goal, path, phase, "故障", title, "", severity), None
        return None, "缺少明确问题场景或具体建议，保留后台待审"

    def process(self, issues, module="playback"):
        origin = defaultdict(list)
        for issue in issues:
            for rid in issue.get("record_ids", []):
                origin[rid].append(issue)
        evidence = defaultdict(list)
        for row in self.routing:
            if row["module"] == module:
                evidence[row["record_id"]].append(row["fragment"])
        # Standalone callers may not have routing; only use exact source text,
        # never trust a generated title as evidence.
        for rid in origin:
            if not evidence[rid] and rid in self.records:
                evidence[rid] = [q["quote"].rstrip("…") for i in origin[rid] for q in i.get("quotes", []) if q["record_id"] == rid]
        groups = {}
        for rid, fragments in evidence.items():
            record = self.records.get(rid)
            if record is None:
                continue
            source_text = (str(record.get("title") or "") + " " + str(record.get("text") or "")).strip()
            for fragment in dict.fromkeys(fragments):
                if fragment not in source_text:
                    self.audit["filtered"].append({"record_id": rid, "fragment": fragment, "reason": "证据不是原文子串"})
                    continue
                scene, reason = self._scene(fragment, record, module)
                if scene is None:
                    self.audit["filtered"].append({"module": module, "record_id": rid, "fragment": fragment, "reason": reason})
                    continue
                original_scene = dict(zip(("goal", "path", "phase", "kind"), scene[:4]))
                scene = business_scene(scene, fragment, record, module)
                goal, path, phase, kind, title, description, severity = scene
                facets = observations(fragment, path) if path in DETAILED_PATHS else []
                if path in DETAILED_PATHS and not facets:
                    self.audit["filtered"].append({"module": module, "record_id": rid, "fragment": fragment, "reason": "缺少可定位的功能对象和异常表现"})
                    continue
                key = (goal, path, phase, kind)
                group = groups.setdefault(key, {"name": title, "description": description, "severity": severity,
                                               "evidence": {}, "origins": {}, "facets": defaultdict(set), "scene_evidence": []})
                group["scene_evidence"].append({"record_id": rid, "fragment": fragment, "scene": original_scene, "facets": facets})
                for facet in facets:
                    group["facets"][facet].add(rid)
                previous = group["evidence"].get(rid, "")
                rank = lambda s: (len(observations(s, path)), 8 <= len(s) <= 160, min(len(s), 100))
                if not previous or rank(fragment) > rank(previous):
                    group["evidence"][rid] = fragment
                for issue in origin[rid]:
                    group["origins"][issue["issue_key"]] = issue["name"]
        rights = set().union(*(set(g["evidence"]) for k, g in groups.items() if k[1] == "会员权益"))
        for key, group in groups.items():
            if key[1] == "播放器/内容":
                for rid in rights:
                    group["evidence"].pop(rid, None)
        result = []
        for key, group in groups.items():
            ids = list(group["evidence"])
            if not ids:
                continue
            sources = Counter(self.records[rid]["source"] for rid in ids)
            negative = len(set(ids) & self.negative_ids)
            facets = sorted(group["facets"], key=lambda f: (-len(group["facets"][f]), f))
            if facets:
                group["name"] = ("更新后" if module == "update" else "") + "、".join(facets[:4]) + ("等问题" if len(facets) > 4 else "")
            evidence_kinds = {e["scene"]["kind"] for e in group["scene_evidence"]}
            if evidence_kinds == {"建议"}:
                group["severity"] = 1
                if not group["name"].startswith("建议"):
                    group["name"] = "建议完善" + group["scene_evidence"][0]["scene"]["goal"]
            issue = {"issue_key": "report_" + module + "_" + hashlib.sha256("|".join(key).encode()).hexdigest()[:12],
                     "category_key": module, "name": group["name"], "raw_name": group["name"],
                     "problem_type": key[3], "feedback_kind": "建议" if evidence_kinds == {"建议"} else key[3], "report_scene": dict(zip(("goal", "path", "phase", "kind"), key)),
                     "description": group["description"], "record_ids": ids, "record_count": len(ids),
                     "negative_count": negative, "sources": sorted(sources), "source_counts": dict(sources),
                     "origin_issue_keys": list(group["origins"]), "origin_issue_names": list(group["origins"].values()),
                     "title_evidence": {facet: sorted(group["facets"][facet]) for facet in facets},
                     "scene_evidence": group["scene_evidence"],
                     "audit_texts": {rid: display_text(self.records[rid]) for rid in ids if self.records[rid]["source"] == "customer_service"},
                     "evidence_samples": list(group["evidence"].values())}
            # Cover distinct observed symptoms and business stages before
            # filling with more near-identical quotes. All quotes remain exact.
            covered, selected_sources, picked = set(), set(), []
            tags = {rid: set(observations(group["evidence"][rid], key[1])) |
                    {e["scene"]["path"] + ":" + e["scene"]["kind"] for e in group["scene_evidence"] if e["record_id"] == rid}
                    for rid in ids}
            remaining = set(ids)
            while remaining and len(picked) < 5:
                rid = max(sorted(remaining), key=lambda r: (
                    len(tags[r] - covered), self.records[r]["source"] not in selected_sources,
                    8 <= len(group["evidence"][r]) <= 160, min(len(group["evidence"][r]), 100)))
                remaining.remove(rid)
                if group["evidence"][rid] in [group["evidence"][r] for r in picked]:
                    continue
                picked.append(rid)
                covered.update(tags[rid])
                selected_sources.add(self.records[rid]["source"])
            issue["representative_record_ids"] = picked
            issue["quotes"] = []
            for rid in picked[:2]:
                record = self.records[rid]
                quote = group["evidence"][rid]
                if quote not in record["text"] and not (record["source"] == "ios" and quote in (record.get("title") or "")):
                    title = str(record.get("title") or "")
                    quote = quote.removeprefix(title).strip() if title else quote
                    if quote not in record["text"]:
                        quote = record["text"]
                issue["quotes"].append({"record_id": rid, "quote": quote, **{k: record.get(k) for k in ("source", "platform", "date", "rating", "file", "sheet", "row")}})
            score_issue(issue, group["severity"])
            result.append(issue)
            self.audit["merged"].append({"module": module, "title": issue["name"], "scene": issue["report_scene"], "from": group["origins"], "record_count": len(ids)})
        result.sort(key=lambda i: (-i["quality_score"], -i["record_count"], i["issue_key"]))
        self.audit["deferred"].extend({"module": module, "issue_key": i["issue_key"], "name": i["name"], "reason": f"超过本模块{self.max_issues}个展示主题，完整证据保留在原始分析"} for i in result[self.max_issues:])
        return result[:self.max_issues]
