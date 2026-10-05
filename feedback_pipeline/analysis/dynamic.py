"""V3.1 dynamic issue discovery.

固定：P/W/M/D/N 五个一级模块。
不固定：任何二级 issue。

V3.1：
- 不依赖 sklearn
- 先过滤没有问题/建议语义的中性片段
- 中文语义归一化
- 基于动态字符 ngram + containment similarity 聚类
- 相同/近似表达自动合并
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from . import dynamic_base as base


MODULES = base.MODULES


ISSUE_SIGNAL = re.compile(
    r"无法|不能|不了|失败|异常|"
    r"卡顿|卡死|卡住|很卡|太卡|"
    r"黑屏|白屏|闪退|崩溃|"
    r"缓冲|转圈|很慢|太慢|限速|降速|"
    r"打不开|连不上|用不了|看不了|下不了|"
    r"没反应|不生效|失效|"
    r"不见|找不到|丢失|消失|"
    r"不对|错误|重复|"
    r"扣款|扣费|被扣|续费|退款|退费|"
    r"太贵|诱导|强制|"
    r"搜不到|搜索不到|"
    r"希望|建议|能不能|可不可以|"
    r"增加|恢复|优化|改回|"
    r"不好用|难用"
)


FILLERS = re.compile(
    r"为什么|为何|请问一下|请问|"
    r"我想问一下|我想问|"
    r"麻烦帮我|麻烦|帮我|"
    r"怎么也|怎么|"
    r"就是|这个|那个|"
    r"现在|目前|一直|老是|总是|"
    r"很多|非常|特别|真的|太|很"
)


LEGAL_TEMPLATE = re.compile(
    r"网络交易监督管理|消费者权益|法律规定|"
    r"根据.*规定|消费者自行选择"
)


def split_fragments(text: str) -> list[str]:
    text = text or ""

    parts = re.split(
        r"[；;。\n！？!?]+|"
        r"(?:(?:，|,)?(?:而且|另外|同时|但是|不过|还有)(?:，|,)?)",
        text,
    )

    output = []

    for part in parts:
        part = part.strip(" ，,：:、\t")

        if len(part) < 4:
            continue

        if base.SUPPORT_NOISE.search(part):
            continue

        if re.fullmatch(r"[\d\s\-_.]+", part):
            continue

        output.append(part)

    return output


def _route_base_fragment(fragment: str):
    text = fragment.lower()

    # 核心修复：
    # 没有问题、异常、建议、期待语义的正常功能描述，不进入问题分析。
    # 上传类问题当前不纳入 P/W/M/D/N 分类
    if re.search(
        r"上传.*(?:失败|不了|异常|失效)",
        text,
    ):
        return None, 0

    if not ISSUE_SIGNAL.search(text):
        return None, 0

    if (
        base.UPDATE_RELATION.search(text)
        and base.PROBLEM_OR_REQUEST.search(text)
    ):
        return "update", 20

    scores = {
        "playback": 0,
        "web_search": 0,
        "member": 0,
        "download": 0,
    }

    if base.PLAY_STRONG.search(text):
        scores["playback"] += 8
    elif base.PLAY_CONTEXT.search(text):
        scores["playback"] += 2

    if base.WEB_STRONG.search(text):
        scores["web_search"] += 8
    elif base.WEB_CONTEXT.search(text):
        scores["web_search"] += 2

    if base.MEMBER_STRONG.search(text):
        scores["member"] += 9
    elif base.MEMBER_CONTEXT.search(text):
        scores["member"] += 2

    if base.DOWNLOAD_STRONG.search(text):
        scores["download"] += 8
    elif base.DOWNLOAD_CONTEXT.search(text):
        scores["download"] += 2

    if base.MEMBER_CAUSAL.search(text):
        scores["member"] += 10

    if base.PAYWALL.search(text):
        scores["member"] += 8

    # 会员只是背景，真正的问题是下载
    if (
        base.MEMBER_CONTEXT.search(text)
        and base.DOWNLOAD_SYMPTOM.search(text)
        and not base.MEMBER_CAUSAL.search(text)
        and not base.PAYWALL.search(text)
    ):
        scores["download"] += 6

    # 会员只是背景，真正的问题是播放
    if (
        base.MEMBER_CONTEXT.search(text)
        and base.PLAY_SYMPTOM.search(text)
        and not base.MEMBER_STRONG.search(text)
        and not base.MEMBER_CAUSAL.search(text)
        and not base.PAYWALL.search(text)
    ):
        scores["playback"] += 6

    # 浏览器里的视频播放问题仍优先播放
    if (
        base.WEB_CONTEXT.search(text)
        and base.PLAY_SYMPTOM.search(text)
    ):
        scores["playback"] += 5

    best = max(scores, key=scores.get)

    if scores[best] < 5:
        return None, 0

    tied = [
        key
        for key, value in scores.items()
        if value == scores[best]
    ]

    if len(tied) > 1:
        for preferred in (
            "playback",
            "download",
            "member",
            "web_search",
        ):
            if preferred in tied:
                best = preferred
                break

    return best, scores[best]


def route_record(record: dict) -> list[dict]:
    text = (
        f"{record.get('title') or ''} "
        f"{record.get('text') or ''}"
    ).strip()

    output = []
    seen = set()

    for fragment in split_fragments(text):
        module, score = route_fragment(fragment)

        if not module:
            continue

        key = (record["id"], module, fragment)

        if key in seen:
            continue

        seen.add(key)

        output.append({
            "record_id": record["id"],
            "module": module,
            "fragment": fragment,
            "route_score": score,
            "source": record.get("source"),
            "platform": record.get("platform"),
            "rating": record.get("rating"),
        })

    return output



MODULE_STOPWORDS = {
    "playback": (
        "视频", "播放器", "播放", "观看", "看剧"
    ),
    "web_search": (
        "浏览器", "网页", "网站", "站点"
    ),
    "member": (
        "会员", "vip", "svip", "超级会员", "白金会员"
    ),
    "download": (
        "下载任务", "下载"
    ),
    "update": (
        "更新以后", "更新之后", "更新后",
        "升级以后", "升级之后", "升级后",
        "新版本", "新版"
    ),
}


def semantic_normalize(text: str, module: str | None = None) -> str:
    value = (text or "").lower()

    value = re.sub(r"https?://\S+", "", value)
    value = base.LONG_NUMBER.sub("", value)

    # 保留业务原因，避免不同问题因为“看不了”被合并
    if (
        re.search(r"会员|vip|svip|充值|开通", value)
        and re.search(r"看不了|无法播放|播放不了|播不了", value)
    ):
        value = value.replace(
            "看不了",
            "会员观看失败"
        )
        value = value.replace(
            "无法播放",
            "会员观看失败"
        )

    if (
        re.search(r"解压", value)
        and re.search(r"看不了|无法播放|打不开", value)
    ):
        value = value.replace(
            "看不了",
            "解压播放失败"
        )
        value = value.replace(
            "无法播放",
            "解压播放失败"
        )

    if (
        re.search(r"avi|mkv|格式", value)
        and re.search(r"播放|看不了|打不开", value)
    ):
        value = value.replace(
            "无法播放",
            "格式播放失败"
        )

    # 播放业务原因隔离，避免不同问题因“看不了”合并
    if re.search(r"会员|vip|svip|充值|开通", value):
        value = "会员观看问题 " + value

    if re.search(r"解压", value):
        value = "解压播放问题 " + value

    if re.search(r"下载.*(视频|文件)|视频.*下载", value):
        value = "下载文件播放问题 " + value

    if re.search(r"avi|mkv|格式|编码", value):
        value = "格式兼容问题 " + value


    replacements = [
        # 会员
        ("自动续期", "自动续费"),
        ("自动扣费", "自动扣款"),
        ("自动扣钱", "自动扣款"),
        ("扣钱", "扣款"),

        ("退费", "退款"),
        ("退钱", "退款"),
        ("退会员", "退款"),
        ("申请退款", "退款"),
        ("申请退费", "退款"),
        ("帮我退款", "退款"),

        # 播放
        ("播放不了", "无法播放"),
        ("不能播放", "无法播放"),
        ("播不了", "无法播放"),
        ("播放失败", "无法播放"),
        ("观看不了", "无法播放"),
        ("视频无法播放", "无法播放"),


        # 下载
        ("下载不了", "无法下载"),
        ("不能下载", "无法下载"),
        ("下不了", "无法下载"),
        ("下载失败", "无法下载"),

        # 搜索
        ("搜不到", "搜索不到"),
        ("搜索不了", "搜索失败"),

        # 性能
        ("很卡", "卡顿"),
        ("太卡", "卡顿"),
        ("一卡一卡", "卡顿"),
    ]

    for old, new_text in replacements:
        value = value.replace(old, new_text)

    # “看不了”必须结合一级模块理解，避免一律判成播放问题
    if module == "playback":
        value = re.sub(
            r"(?:视频|影片|电影|剧集|电视剧)?(?:观看不了|看不了)",
            "无法播放",
            value,
        )
    elif module == "member":
        value = re.sub(
            r"(?:观看不了|看不了)",
            "无法观看",
            value,
        )

    # 继续统一常见句式
    value = re.sub(
        r"(?:无法|不能|没法)[^，。；]{0,4}下载",
        "无法下载",
        value,
    )

    value = re.sub(
        r"(?:无法|不能|没法)[^，。；]{0,4}播放",
        "无法播放",
        value,
    )

    value = re.sub(
        r"下载[^，。；]{0,8}(?:特别慢|非常慢|太慢|很慢|慢得)",
        "下载速度慢",
        value,
    )

    value = re.sub(
        r"(?:被|又被|莫名|突然)?自动[^，。；]{0,5}扣款",
        "自动扣款",
        value,
    )

    # 自动续费“已发生”的口语表达统一。
    # 但“关闭/取消自动续费”保留为独立意图，不强行合并。
    if not re.search(r"关闭.*自动续费|取消.*自动续费|停止.*自动续费", value):
        value = re.sub(
            r"(?:这个月|本月|又|已经|怎么|为什么|为何|我|这个)*"
            r"自动续费"
            r"(?:了|啦)?",
            "自动续费",
            value,
        )

    # 会员语义保护，避免清理口语时删除业务上下文
    if not re.search(r"会员|vip|svip|超级会员", value):
        value = FILLERS.sub("", value)
    else:
        value = re.sub(
            r"^(这个|那个|就是|现在|目前)+",
            "",
            value,
        )

    # -----------------------------------------
    # 保护一级模块中的核心动作语义。
    # 避免删除“下载/播放”停用词时，把“无法下载”
    # 错误变成只有“无法”。
    # -----------------------------------------
    protected = {
        "无法下载": "DFAILTOKEN",
        "下载速度慢": "DSLOWTOKEN",
        "无法播放": "PFAILTOKEN",
    }

    for phrase, token in protected.items():
        value = value.replace(phrase, token)

    if module:
        for word in MODULE_STOPWORDS.get(module, ()):
            if word in ("会员", "vip", "svip"):
                continue
            value = value.replace(word, "")

    for phrase, token in protected.items():
        value = value.replace(token, phrase)

    # 去掉不影响问题语义的口语尾词
    value = re.sub(
        r"(啊|呀|呢|吗|吧|啦|了)+$",
        "",
        value,
    )

    # 纯“退款请求”统一成退款；
    # 但如果还有“用不上/被骗/解压失败”等实质原因，则保留原因。
    compact = re.sub(
        r"[^\u4e00-\u9fffA-Za-z0-9]+",
        "",
        value,
    )

    refund_shell = re.compile(
        r"^(?:你好|您好|给我|我要|我想|想|"
        r"需要|现在|麻烦|可以|能|能不能|"
        r"请|直接|帮我|就是|请问)*"
        r"退款"
        r"(?:一下)?$"
    )

    if refund_shell.fullmatch(compact):
        value = "退款"
    else:
        value = compact

    return value

def features(text: str) -> set[str]:
    result = set()

    for n in (2, 3, 4):
        if len(text) < n:
            continue

        for i in range(len(text) - n + 1):
            result.add(text[i:i+n])

    return result


def cluster_context(text: str) -> str:
    """
    聚类业务场景隔离。
    避免相同症状词跨业务合并。
    """
    if re.search(r"会员|vip|svip|充值|开通", text):
        return "member"

    if re.search(r"解压|压缩|密码", text):
        return "extract"

    if re.search(r"avi|mkv|格式|编码", text):
        return "format"

    if re.search(r"下载.*视频|视频.*下载|下载完成", text):
        return "download_video"

    return "normal"


def pair_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    shorter = min(len(a), len(b))

    # “为何自动续费” vs “自动续费”
    if shorter >= 4 and (a in b or b in a):
        length_ratio = shorter / max(len(a), len(b))
        return 0.90 + 0.10 * length_ratio

    fa = features(a)
    fb = features(b)

    if not fa or not fb:
        return SequenceMatcher(None, a, b).ratio()

    intersection = len(fa & fb)
    union = len(fa | fb)

    jaccard = intersection / union if union else 0.0
    containment = intersection / min(len(fa), len(fb))

    sequence = SequenceMatcher(
        None,
        a,
        b,
        autojunk=False,
    ).ratio()

    return (
        0.45 * containment
        + 0.30 * sequence
        + 0.25 * jaccard
    )



def add_scene_prefix(text: str, module: str):
    """
    聚类前增加业务场景隔离。
    避免 playback 中：
    会员看不了 / 解压失败 / 格式问题 / 下载视频无法播放
    全部聚成“无法播放”。
    """
    if module != "playback":
        return text

    if re.search(r"会员|vip|svip|充值|开通|购买|续费", text, re.I):
        return "会员场景_" + text

    if re.search(r"解压|压缩包|密码", text):
        return "解压场景_" + text

    if re.search(r"avi|mkv|格式|编码", text, re.I):
        return "格式场景_" + text

    if re.search(
        r"下载.*(视频|文件)|视频.*下载|下载完成",
        text,
        re.I
    ):
        return "下载视频场景_" + text

    return "普通播放场景_" + text


def cluster_texts(texts: list[str], module: str | None = None):
    """
    Leader / representative clustering。

    与 V3.1 最大区别：
    新文本必须直接和“簇代表”相似，
    不允许 A≈B、B≈C 导致 A/B/C 无限链式合并。
    """

    if not texts:
        return [], "none"

    normalized = [
        semantic_normalize(text, module)
        for text in texts
    ]

    # 完全相同表达先统计，频次高的优先建立稳定 cluster
    frequency = Counter(
        x for x in normalized if x
    )

    order = sorted(
        range(len(texts)),
        key=lambda i: (
            -frequency.get(normalized[i], 0),
            len(normalized[i]) if normalized[i] else 9999,
            i,
        ),
    )

    clusters = []
    labels = [-1] * len(texts)

    for index in order:
        current = normalized[index]

        if not current:
            clusters.append({
                "representative": current,
                "members": [index],
            })
            labels[index] = len(clusters) - 1
            continue

        best_cluster = None
        best_score = 0.0

        for cluster_index, cluster in enumerate(clusters):
            representative = cluster["representative"]

            if not representative:
                continue

            if cluster_context(current) != cluster_context(representative):
                continue

            score = pair_similarity(
                current,
                representative,
            )

            if score > best_score:
                best_score = score
                best_cluster = cluster_index

        # 较短表达本身信息少，要求更严格。
        threshold = 0.64

        if len(current) <= 5:
            threshold = 0.72
        elif len(current) >= 16:
            threshold = 0.60

        if (
            best_cluster is not None
            and best_score >= threshold
        ):
            labels[index] = best_cluster
            cluster = clusters[best_cluster]
            cluster["members"].append(index)

            # 代表表达不跟着每条数据变化。
            # 只在出现更高频、且长度更适中的表达时替换。
            old = cluster["representative"]

            # 簇代表优先保持“高频 + 更短”的核心表达。
            # 避免：
            # 下载速度慢
            #   -> 会员下载速度慢
            #   -> 你们下载速度慢
            # 因代表句不断变长而被拆成多个簇。
            old_rank = (
                frequency.get(old, 0),
                -len(old),
            )

            new_rank = (
                frequency.get(current, 0),
                -len(current),
            )

            if new_rank > old_rank:
                cluster["representative"] = current

        else:
            clusters.append({
                "representative": current,
                "members": [index],
            })
            labels[index] = len(clusters) - 1

    return labels, "leader-char-semantic-v32"

def clean_title(text: str) -> str:
    value = (text or "").strip()

    value = base.LONG_NUMBER.sub("", value)

    value = re.sub(
        r"^(我想问一下|我想问|请问一下|请问|"
        r"为什么|为何|怎么也|怎么|"
        r"你们|我这边|我的|我|"
        r"就是|这个|那个|现在|目前)"
        r"[，,：:\s]*",
        "",
        value,
    )

    value = re.sub(r"\s+", "", value)

    parts = [
        p.strip()
        for p in re.split(
            r"[，,；;。！？!?]",
            value,
        )
        if len(p.strip()) >= 3
    ]

    if parts:
        issue_parts = [
            p for p in parts
            if ISSUE_SIGNAL.search(p)
        ]

        if issue_parts:
            value = min(
                issue_parts,
                key=lambda x: (
                    abs(len(x) - 12),
                    len(x),
                ),
            )
        else:
            value = parts[0]

    value = value.strip(
        "；;。，,.！？!? "
    )

    if len(value) > 20:
        value = (
            value[:20].rstrip()
            + "…"
        )

    return value or "其他问题"





def classify_problem_type(
    samples: list[str],
    module: str,
) -> str:

    rules = {

        "playback": [

            # 高优先级：业务原因优先，不要被“看不了”吞掉

            ("会员权益不可用",
             r"会员|vip|svip|充值|开通|购买|续费"),

            ("解压后无法播放",
             r"解压|压缩包|密码"),

            ("格式兼容问题",
             r"\\bavi\\b|\\bmkv\\b|格式|编码"),

            ("下载文件无法播放",
             r"下载.*(?:视频|文件).*播放|播放.*下载"),

            ("投屏问题",
             r"投屏|电视|设备"),

            ("字幕问题",
             r"字幕"),

            ("画质问题",
             r"hdr|清晰度|画质|原画"),

            ("播放卡顿问题",
             r"卡顿|卡死|黑屏|缓冲|一卡一卡"),

            ("播放失败问题",
             r"播放不了|无法播放|播不了|打不开|看不了"),
        ],

        "download": [
            ("下载失败问题",
             r"下载失败|下载不了|无法下载"),

            ("下载速度问题",
             r"速度|限速|降速|慢"),

            ("下载任务问题",
             r"任务|卡死|暂停"),

            ("转存取回问题",
             r"转存|取回|云添加"),
        ],

        "update": [
            ("入口变化问题",
             r"入口|找不到|不见|消失"),

            ("功能异常问题",
             r"无法|不能|异常"),

            ("性能问题",
             r"卡顿|闪退|打不开"),
        ],
    }


    matched = []

    for sample in samples:
        sample = sample.lower()

        for name, pattern in rules.get(module, []):

            if re.search(pattern, sample):
                matched.append(name)
                break


    if not matched:
        return "其他问题"


    from collections import Counter

    counts = Counter(matched)

    name, count = counts.most_common(1)[0]

    if count / len(samples) >= 0.5:
        return name

    return "其他问题"


def choose_title(items: list[dict]) -> str:
    """
    动态从当前 cluster 内寻找最有代表性的短表达。
    """

    candidates = []

    cluster_norms = [
        semantic_normalize(x["fragment"])
        for x in items
    ]

    for index, item in enumerate(items):
        fragment = item["fragment"]

        # 法律模板不适合作为周报问题标题
        if LEGAL_TEMPLATE.search(fragment):
            legal_penalty = 20
        else:
            legal_penalty = 0

        current = cluster_norms[index]

        if not current:
            continue

        similarity = 0

        for other in cluster_norms[:50]:
            if not other:
                continue

            if current in other or other in current:
                similarity += 2

            shared = (
                features(current)
                & features(other)
            )

            similarity += min(
                len(shared),
                10,
            )

        title = clean_title(fragment)

        length_penalty = abs(
            len(title) - 12
        )

        score = (
            similarity
            - length_penalty
            - legal_penalty
        )

        candidates.append(
            (score, title)
        )

    if not candidates:
        return "其他问题"

    candidates.sort(
        key=lambda x: (
            -x[0],
            len(x[1]),
        )
    )

    return candidates[0][1]


def pick_quotes(
    items: list[dict],
    negative_ids: set[str],
):
    ranked = []

    for item in items:
        fragment = item["fragment"].strip()

        if len(fragment) < 6:
            continue

        if base.LONG_NUMBER.search(fragment):
            continue

        if LEGAL_TEMPLATE.search(fragment):
            legal_penalty = 8
        else:
            legal_penalty = 0

        score = item["route_score"]

        if item["source"] in (
            "ios",
            "android",
        ):
            score += 6

        elif item["source"] == "external":
            score += 4

        else:
            score += 2

        if item["record_id"] in negative_ids:
            score += 5

        if 10 <= len(fragment) <= 60:
            score += 4

        if len(fragment) > 90:
            score -= 5

        score -= legal_penalty

        ranked.append({
            **item,
            "display": (
                fragment
                if len(fragment) <= 90
                else fragment[:90]
            ),
            "score": score,
            "norm": semantic_normalize(fragment),
        })

    ranked.sort(
        key=lambda x: (
            -x["score"],
            len(x["display"]),
        )
    )

    picked = []
    used_records = set()
    used_norms = set()
    used_sources = set()

    while ranked and len(picked) < 2:
        best = None
        best_value = None

        for item in ranked:
            if item["record_id"] in used_records:
                continue

            # 修复 V3 同一句被选两次
            if item["norm"] in used_norms:
                continue

            value = item["score"]

            if (
                used_sources
                and item["source"]
                not in used_sources
            ):
                value += 3

            if best is None or value > best_value:
                best = item
                best_value = value

        if best is None:
            break

        picked.append({
            "record_id": best["record_id"],
            "source": best["source"],
            "platform": best["platform"],
            "rating": best["rating"],
            "quote": best["display"],
        })

        used_records.add(
            best["record_id"]
        )

        used_norms.add(
            best["norm"]
        )

        used_sources.add(
            best["source"]
        )

        ranked = [
            x for x in ranked
            if x["record_id"]
            != best["record_id"]
        ]

    return picked


def _build_clustered_issues(
    assignments: list[dict],
    negative_ids: set[str],
):
    grouped = defaultdict(list)

    for item in assignments:
        grouped[item["module"]].append(
            item
        )

    all_issues = []
    backends = {}

    for module_key, items in grouped.items():
        texts = [
            add_scene_prefix(
                x["fragment"],
                module_key,
            )
            for x in items
        ]

        labels, backend = cluster_texts(
            texts,
            module_key,
        )

        backends[module_key] = backend

        clusters = defaultdict(list)

        for item, label in zip(
            items,
            labels,
        ):
            clusters[label].append(item)

        module_issues = []

        for members in clusters.values():
            record_ids = sorted({
                x["record_id"]
                for x in members
            })

            if not record_ids:
                continue

            title = choose_title(members)

            source_counts = Counter(
                x["source"]
                for x in members
            )

            neg = sum(
                1
                for rid in record_ids
                if rid in negative_ids
            )

            unique_samples = []
            seen_samples = set()

            for x in members:
                norm = semantic_normalize(
                    x["fragment"]
                )

                if norm in seen_samples:
                    continue

                seen_samples.add(norm)

                unique_samples.append(
                    x["fragment"]
                )

                if len(unique_samples) >= 8:
                    break

            issue_key = (
                "dyn_"
                + module_key
                + "_"
                + hashlib.sha1(
                    (
                        title
                        + "|"
                        + "|".join(
                            record_ids[:10]
                        )
                    ).encode("utf-8")
                ).hexdigest()[:10]
            )

            module_issues.append({
                "issue_key": issue_key,
                "problem_type": classify_problem_type(
                    unique_samples,
                    module_key,
                ),
                "name": title,
                "category_key": module_key,
                "record_ids": record_ids,
                "record_count": len(
                    record_ids
                ),
                "negative_count": neg,
                "sources": sorted(
                    source_counts
                ),
                "source_counts": dict(
                    source_counts
                ),
                "quotes": pick_quotes(
                    members,
                    negative_ids,
                ),
                "evidence_samples": (
                    unique_samples
                ),
            })

        module_issues.sort(
            key=lambda x: (
                -x["negative_count"],
                -x["record_count"],
                x["name"],
            )
        )

        prefix = MODULES[module_key][
            "code"
        ]

        for index, issue in enumerate(
            module_issues,
            1,
        ):
            issue["code"] = (
                f"{prefix}{index}"
            )

            all_issues.append(issue)

    return all_issues, backends


# ============================================================
# Legacy analysis-output compatibility (still called by the analyzer).
# Final document selection lives in report/issues.py. These transformations
# also mutate analysis issues; removing them requires a separate migration.
# 只影响：
# 1. 动态问题展示标题
# 2. 低频但有价值的问题精选
# 3. 新版本建议/期待进入 N
#
# 不改变动态聚类本身。
# ============================================================

REPORT_REQUEST_SIGNAL = re.compile(
    r"希望|建议|期待|"
    r"建议增加|希望增加|"
    r"希望支持|建议支持|"
    r"希望恢复|建议恢复|"
    r"希望优化|建议优化|"
    r"改回|能不能|可不可以|能否"
)

REPORT_HIGH_VALUE = re.compile(
    r"崩溃|闪退|黑屏|数据丢失|文件丢失|"
    r"自动扣|扣款|扣费|异常续费|"
    r"病毒|安全|封禁|永久封禁|"
    r"无法播放|无法下载|无法使用|"
    r"字幕|hdr|投屏|片头|片尾|"
    r"控件|进度条|深色模式|"
    r"捆绑|自动保存|历史记录"
)

UPDATE_REQUEST_RELATION = re.compile(
    r"(?:希望|建议|期待).{0,20}"
    r"(?:新版|新版本|下个版本|后续版本|以后版本)"
    r"|"
    r"(?:新版|新版本|下个版本|后续版本|以后版本)"
    r".{0,20}"
    r"(?:希望|建议|增加|支持|恢复|优化|改回)"
)

UPDATE_CONTEXT = re.compile(
    r"更新|升级|新版|新版本|版本更新|更新后|升级后"
)

UPDATE_PROBLEM_SIGNAL = re.compile(
    r"闪退|崩溃|卡顿|卡死|"
    r"找不到|不见了|消失|"
    r"失效|无法|不能|用不了|"
    r"异常|变成|没有了|缺少"
)


def route_fragment(fragment: str):
    """
    在原动态路由基础上补充：
    明确面向新版/后续版本的建议、期待，也进入 N。
    """
    text = (fragment or "").lower()

    if UPDATE_REQUEST_RELATION.search(text):
        return "update", 20

    return _route_base_fragment(fragment)


def _polish_legacy_title(
    raw_name: str,
    category_key: str,
    evidence_samples: list[str] | None = None,
) -> str:
    """
    只润色展示标题，不参与聚类。
    新问题如果没有命中任何表达规则，仍保留动态原始标题。
    """

    evidence = "；".join(evidence_samples or [])
    primary = (raw_name or "").lower()

    need_evidence = (
        not primary.strip()
        or "…" in primary
        or bool(re.search(
            r"无法无天|不好用|有问题|不对|不行|"
            r"怎么回事|它老是|这不是",
            primary,
        ))
    )

    text = (
        f"{primary}；{evidence.lower()}"
        if need_evidence
        else primary
    )

    # ---------- 播放 ----------
    if category_key == "playback":
        if "字幕" in text:
            if re.search(r"加载失败|加载不了|消失|不见|失效|无法", text):
                return "在线字幕加载或显示异常"
            if REPORT_REQUEST_SIGNAL.search(text):
                return "字幕功能优化建议"
            return "在线字幕体验异常"

        if re.search(r"片头|片尾", text):
            return "希望增加跳过片头片尾功能"

        if "投屏" in text:
            return "投屏失败或无法连接"

        if re.search(r"hdr|画质|清晰度|4k", text):
            return "HDR或画质显示异常"

        if re.search(r"倍速|进度条|快进|快退|控件|操作栏", text):
            return "播放控件及进度操作体验问题"

        if re.search(
            r"黑屏|卡顿|一卡一卡|缓冲|卡死|"
            r"视频.*卡|很卡|会卡|越来越卡",
            text,
        ):
            return "播放卡顿、黑屏或卡死"

        if re.search(r"无法播放|播放不了|视频打不开|打不开视频", text):
            return "视频无法播放或打开"

    # ---------- 网页 / 搜索 ----------
    if category_key == "web_search":
        if "收藏" in text and re.search(
            r"分组|搜索|检索|查找|管理",
            text,
        ):
            return "收藏链接分组及搜索能力不足"

        if re.search(r"跳转|广告站|恶意|色情|露骨", text):
            return "搜索结果存在异常跳转"

        if re.search(r"关键词.*重复|重复.*关键词", text):
            return "搜索关键词重复异常"

        if re.search(r"搜不到|搜索不到|搜索失败|搜索不了", text):
            return "搜索异常或搜不到资源"

        if re.search(r"网页.*打不开|浏览器.*打不开|网站.*打不开|网址.*打不开", text):
            return "网页或站点无法正常打开"

    # ---------- 会员 ----------
    if category_key == "member":
        if "解压" in text and re.search(r"会员|付费|充值|开通|不能|无法", text):
            return "解压等基础功能存在会员门槛"

        if re.search(r"自动扣|扣款|扣费|不知情.*扣", text):
            return "自动扣款争议"

        if re.search(r"自动续费|自动续期", text):
            return "自动续费争议"

        if re.search(r"退款|退费|退钱", text):
            return "会员退款诉求"

        if re.search(r"封禁|冻结|解冻", text):
            return "会员账号封禁或使用异常"

        if re.search(r"兑换|换绑|权益.*不到账|权益.*未恢复", text):
            return "会员兑换及权益到账异常"

        if re.search(
            r"诱导|强制消费|强制付费|"
            r"弹.*vip|弹.*会员",
            text,
        ):
            return "会员付费引导争议"

        if re.search(r"空间不足|容量不足", text):
            return "会员空间及容量提示异常"

    # ---------- 下载 ----------
    if category_key == "download":
        if "上传" in text and re.search(r"无法|不能|失败|异常", text):
            return "上传功能异常"

        if re.search(r"限速|降速", text):
            return "下载限速或中途降速"

        if re.search(
            r"速度.*慢|下载.*慢|龟速|"
            r"越来越慢|很慢|太慢",
            text,
        ):
            return "下载速度长期过慢"

        if re.search(r"捆绑|偷偷下载|自动下载", text):
            return "下载过程中出现捆绑内容"

        if re.search(r"云添加.*失败|云添加.*不了", text):
            return "云添加失败或进度异常"

        if re.search(r"转存.*失败|不能转存|无法转存|取回.*失败", text):
            return "转存或取回失败"

        if re.search(r"无法下载|下载不了|下载失败|不能下载", text):
            return "下载失败或无法下载"

        if re.search(r"找不到文件|文件.*不见|文件.*丢失", text):
            return "下载后文件无法找到"

    # ---------- 新版本 ----------
    if category_key == "update":
        if REPORT_REQUEST_SIGNAL.search(text):
            # 对建议型标题，尽量保留动态主题本身
            cleaned = clean_title(raw_name)

            if (
                cleaned
                and cleaned != "其他问题"
                and len(cleaned) >= 4
            ):
                return f"新版本功能建议：{cleaned[:16]}"

            return "新版本功能优化建议"

        if re.search(r"闪退|崩溃|卡顿|卡死|发热", text):
            return "更新后卡顿、闪退或性能下降"

        if re.search(r"找不到|不见了|入口.*没|功能.*没", text):
            return "更新后原有功能或入口发生变化"

        if re.search(
            r"失效|无法|不能|用不了|打不开",
            text,
        ):
            return "更新后部分功能无法正常使用"

    # ---------- 通用去口语 ----------
    title = clean_title(raw_name)

    title = re.sub(
        r"^(为什么|怎么|为何|就是|现在|还是|请问|你好|你们)",
        "",
        title,
    )

    title = title.strip("，,。！？!?；; ")

    if len(title) > 22:
        title = title[:22].rstrip() + "…"

    return title or raw_name


def filter_low_value_playback(issue):

    if issue.get("category_key") != "playback":
        return False

    text = " ".join(
        issue.get("evidence_samples", [])
    )

    # PC / TV / 第三方生态，不属于移动端播放体验
    if re.search(
        r"tv端|电视端|第三方应用授权|鸿蒙平台开发|播放器接入",
        text,
        re.I
    ):
        return True

    # 明显非移动端播放问题
    if re.search(
        r"进入游戏|下载客户端后",
        text,
        re.I
    ):
        return True

    return False



def _build_legacy_titled_issues(assignments, negative_ids):
    """
    聚类逻辑完全沿用原版本。
    这里只在生成后统一润色标题。
    """

    issues, backends = _build_clustered_issues(
        assignments,
        negative_ids,
    )

    for issue in issues:
        issue["raw_name"] = issue["name"]

        issue["name"] = polish_issue_title(
            issue["name"],
            issue["category_key"],
            issue.get("evidence_samples", []),
        )

        if issue["name"] == "播放控件及进度操作体验问题":
            relevant = [
                q
                for q in issue.get("quotes", [])
                if re.search(
                    r"进度|快进|快退|倍速|控件|操作栏",
                    q.get("quote", ""),
                )
            ]

            if relevant:
                issue["quotes"] = relevant

    # 报告层合并相同展示主题。
    # 不改变底层动态聚类，只避免周报里同一问题重复出现。
    merged = {}

    for issue in issues:
        key = (
            issue["category_key"],
            issue["name"],
        )

        if key not in merged:
            merged[key] = issue
            continue

        dst = merged[key]

        record_ids = list(dict.fromkeys(
            dst.get("record_ids", [])
            + issue.get("record_ids", [])
        ))

        dst["record_ids"] = record_ids

        if record_ids:
            dst["record_count"] = len(record_ids)
            dst["negative_count"] = sum(
                1
                for rid in record_ids
                if rid in negative_ids
            )
        else:
            dst["record_count"] = (
                dst.get("record_count", 0)
                + issue.get("record_count", 0)
            )

        dst["sources"] = sorted(set(
            dst.get("sources", [])
            + issue.get("sources", [])
        ))

        quotes = []
        seen_quotes = set()

        for q in (
            dst.get("quotes", [])
            + issue.get("quotes", [])
        ):
            qkey = (
                q.get("record_id"),
                q.get("quote"),
            )

            if qkey in seen_quotes:
                continue

            seen_quotes.add(qkey)
            quotes.append(q)

        dst["quotes"] = quotes

        dst["evidence_samples"] = list(dict.fromkeys(
            dst.get("evidence_samples", [])
            + issue.get("evidence_samples", [])
        ))

    return list(merged.values()), backends


# ============================================================
# Final weekly report guard
# 最终周报质量兜底：
# - 修正少数动态标题
# - 清理明显无关原声
# - 排除PC-only低频问题
# - N类过滤空泛“建议最新版/修正这个问题”
# ============================================================

def polish_issue_title(
    raw_name: str,
    category_key: str,
    evidence_samples: list[str] | None = None,
) -> str:

    evidence = "；".join(evidence_samples or [])
    all_text = f"{raw_name}；{evidence}".lower()

    # 投屏设备搜索失败
    if (
        category_key == "playback"
        and "投屏" in all_text
        and re.search(
            r"搜不到电视|搜索不到电视|"
            r"搜不到设备|搜索不到设备|"
            r"无法投屏|不能投屏",
            all_text,
        )
    ):
        return "投屏设备搜索失败或无法连接"

    # 云添加删除操作建议
    if (
        category_key == "download"
        and "云添加" in all_text
        and "删除" in all_text
        and re.search(
            r"同步删除|同时删除|一起删除",
            all_text,
        )
    ):
        return "云添加记录删除操作建议"

    # 明确的新版本种子下载问题
    if (
        category_key == "update"
        and "种子" in all_text
        and re.search(r"多选|超过", all_text)
        and re.search(
            r"不能下载|无法下载|下载失败|丢失",
            all_text,
        )
    ):
        return "多选种子文件时下载失败"

    return _polish_legacy_title(
        raw_name,
        category_key,
        evidence_samples,
    )


def build_dynamic_issues(assignments, negative_ids):

    issues, backends = (
        _build_legacy_titled_issues(
            assignments,
            negative_ids,
        )
    )


    # final playback merge
    # 同类播放失败问题统一标题后合并

    playback_merged = {}

    for issue in issues:

        if issue.get("category_key") == "playback":

            text = " ".join(
                issue.get("evidence_samples", [])
            )

            # 非会员场景的播放失败统一
            if (
                re.search(
                    r"看不了|不能播放|无法播放|播放失败|播不了|打不开",
                    text
                )
                and not re.search(
                    r"会员|VIP|SVIP|充值|开通|购买|续费",
                    text,
                    re.I
                )
            ):
                issue["name"] = "视频无法播放"


        key = (
            issue.get("category_key"),
            issue.get("name")
        )

        if key not in playback_merged:
            playback_merged[key] = issue
        else:
            dst = playback_merged[key]

            dst["record_ids"] = list(dict.fromkeys(
                dst.get("record_ids", [])
                + issue.get("record_ids", [])
            ))

            dst["record_count"] = len(
                dst["record_ids"]
            )

            dst["negative_count"] = (
                dst.get("negative_count", 0)
                + issue.get("negative_count", 0)
            )

            dst["evidence_samples"] = list(dict.fromkeys(
                dst.get("evidence_samples", [])
                + issue.get("evidence_samples", [])
            ))[:8]

            dst["quotes"] = (
                dst.get("quotes", [])
                + issue.get("quotes", [])
            )[:5]



    # Display value/volume selection belongs to report/issues.py; keep the
    # existing mobile-scope filter without the obsolete undefined predicate.
    issues = [x for x in issues if not filter_low_value_playback(x)]

    # 再用最终标题跑一次，保证刚才新增的标题规则生效
    for issue in issues:
        issue["name"] = polish_issue_title(
            issue.get(
                "raw_name",
                issue["name"],
            ),
            issue["category_key"],
            issue.get("evidence_samples", []),
        )

    # 播放问题最终归并：
    # 同一播放失败症状避免被标题差异拆散
    for issue in issues:
        if issue.get("category_key") == "playback":
            text = " ".join(
                issue.get("evidence_samples", [])
            )

            if "会员" not in text and "VIP" not in text.upper():
                if re.search(
                    r"看不了|不能播放|无法播放|播放失败|打不开",
                    text
                ):
                    issue["name"] = "视频无法播放"

    # 最终展示标题一致的问题再合并一次
    # 播放失败类标题统一，避免同义问题拆分
    for issue in issues:
        if issue.get("category_key") == "playback":
            name = issue.get("name", "")

            # 播放失败
            if re.search(
                r"视频无法播放|视频无法播放或打开|视频看不了|不能播放|无法播放|播放失败|播不了|打不开|观看不了",
                name
            ):
                issue["name"] = "视频无法播放"

            # 投屏问题
            elif re.search(
                r"投屏设备搜索失败|投屏失败",
                name
            ):
                issue["name"] = "投屏设备搜索失败或无法连接"

            # 字幕问题
            elif re.search(
                r"字幕|显示网络错误",
                name
            ):
                issue["name"] = "在线字幕加载或显示异常"

            # 播放卡顿
            elif re.search(
                r"播放卡顿|加载照片或者视频太慢|能干干不能干滚",
                name
            ):
                issue["name"] = "播放卡顿、黑屏或卡死"

    merged = {}

    for issue in issues:
        key = (
            issue["category_key"],
            issue["name"],
        )

        if key not in merged:
            merged[key] = issue
            continue

        dst = merged[key]

        record_ids = list(dict.fromkeys(
            dst.get("record_ids", [])
            + issue.get("record_ids", [])
        ))

        dst["record_ids"] = record_ids

        if record_ids:
            dst["record_count"] = len(record_ids)
            dst["negative_count"] = sum(
                1
                for rid in record_ids
                if rid in negative_ids
            )

        dst["sources"] = sorted(set(
            dst.get("sources", [])
            + issue.get("sources", [])
        ))

        dst["evidence_samples"] = list(dict.fromkeys(
            dst.get("evidence_samples", [])
            + issue.get("evidence_samples", [])
        ))

        quotes = []
        seen = set()

        for q in (
            dst.get("quotes", [])
            + issue.get("quotes", [])
        ):
            k = (
                q.get("record_id"),
                q.get("quote"),
            )

            if k in seen:
                continue

            seen.add(k)
            quotes.append(q)

        dst["quotes"] = quotes

    issues = list(merged.values())

    # 播放控件问题只保留真正相关原声
    for issue in issues:
        if (
            issue["name"]
            == "播放控件及进度操作体验问题"
        ):
            valid_quotes = [
                q
                for q in issue.get("quotes", [])
                if re.search(
                    r"进度条|进度|快进|快退|"
                    r"倍速|控件|操作栏|拖动",
                    q.get("quote", ""),
                )
            ]

            if valid_quotes:
                issue["quotes"] = valid_quotes

    return issues, backends


