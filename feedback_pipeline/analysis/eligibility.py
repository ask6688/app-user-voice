"""V2 feedback eligibility gate.

只负责判断一条反馈是否适合进入移动端用户声音正式分类。
不负责 P/W/M/D/N 分类。
"""

import re


MOBILE_CONTEXT = re.compile(
    r"手机|移动端|安卓|android|ios|iphone|鸿蒙"
)

PC_CONTEXT = re.compile(
    r"电脑端|pc端|mac端|电脑版|桌面端|"
    r"windows(?:版本|客户端|系统|端)|"
    r"鼠标右键|右键菜单|本地右键"
)

def check_eligibility(record: dict, low_information_patterns) -> tuple[bool, str]:
    """
    返回：
        (True, "eligible")
        (False, reason_code)
    """
    text = f"{record.get('title') or ''} {record.get('text') or ''}".strip()
    lowered = text.lower()

    # 1. 空文本
    if not text:
        return False, "empty"

    # 2. 纯情绪 / 低信息
    stripped = re.sub(r"[\s。，,.!！?？、~～…]+", "", text)

    if len(stripped) < 4:
        return False, "low_information"

    for pattern in low_information_patterns:
        if pattern.match(text):
            return False, "low_information"

    # 3. 明确仅 PC 端场景
    # 若同时明确提到手机/移动端，则不能因为出现“电脑”就直接删掉。
    if (PC_CONTEXT.search(lowered) and not MOBILE_CONTEXT.search(lowered)
            and record.get("source") not in ("ios", "android")):
        return False, "pc_only"

    return True, "eligible"
