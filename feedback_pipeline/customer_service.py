"""User-role extraction reused from the original feedback workflow."""
import re
from html.parser import HTMLParser

ROLES = re.compile(r"(用户|客服|系统|机器人)\s*[:：]")

NO_INFORMATION = {
    "没有了", "没了", "没有", "好的", "好", "好吧", "谢谢", "谢谢了", "感谢", "嗯", "嗯嗯",
    "哦", "哦哦", "知道了", "明白了", "再见", "拜拜", "你好", "您好", "您好啊", "你好啊",
    "人工客服", "人工", "转人工", "找人工", "我要人工客服", "谢谢客服", "没有了谢谢", "好的谢谢",
}

class MessageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == 'br':
            self.parts.append('\n')

def plain_message(message):
    parser = MessageText()
    parser.feed(message)
    parser.close()
    return ''.join(parser.parts).strip()

def user_messages(dialogue):
    matches = list(ROLES.finditer(dialogue))
    return [dialogue[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(dialogue)].strip()
            for i, m in enumerate(matches) if m[1] == "用户"]

def meaningful(message):
    text = re.sub(r"[\s，。！？,.!?、~～…]+", "", message).lower()
    if not text or text in NO_INFORMATION or text in {"ok", "okay", "thanks"}:
        return False
    return not re.fullmatch(r"(?:(?:https?://|www\.)\S+|[a-z0-9-]+(?:\.[a-z0-9-]+)+/?)(?:\s*)", message.strip(), re.I)
