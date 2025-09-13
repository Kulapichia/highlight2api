import base64
import json
from enum import Enum
from pathlib import Path
from typing import List, Dict, Any, Optional, Union, Callable, Set

from curl_cffi.requests.exceptions import RequestException
from loguru import logger
from sse_starlette import EventSourceResponse
from starlette.responses import JSONResponse

from .errors import HighlightError
from .models import Message, OpenAITool


def format_messages_to_prompt(messages: List[Message]) -> str:
    """将OpenAI格式的消息转换为单个提示字符串"""
    formatted_messages = []
    for message in messages:
        if message.role:
            if message.content:
                if isinstance(message.content, list):
                    for item in message.content:
                        formatted_messages.append(f"{message.role}: {item.text}")
                else:
                    formatted_messages.append(f"{message.role}: {message.content}")
            if message.tool_calls:
                formatted_messages.append(
                    f"{message.role}: {json.dumps(message.tool_calls)}"
                )
            if message.tool_call_id:
                formatted_messages.append(
                    f"{message.role}: tool_call_id: {message.tool_call_id} {message.content}"
                )
    return "\n\n".join(formatted_messages)


def format_openai_tools(tools: Optional[List[OpenAITool]]) -> List[Dict[str, Any]]:
    """将OpenAI格式的工具转换为Highlight格式"""
    if not tools:
        return []

    highlight_tools = []
    for tool in tools:
        if tool.type == "function":
            highlight_tool = {
                "name": tool.function.name,
                "description": tool.function.description or "",
                "parameters": tool.function.parameters or {}
            }
            highlight_tools.append(highlight_tool)

    return highlight_tools


async def safe_stream_wrapper(
        generator_func, *args, **kwargs
) -> Union[EventSourceResponse, JSONResponse]:
    """
    安全的流响应包装器
    先执行生成器获取第一个值，如果成功才创建流响应
    """
    # 创建生成器实例
    generator = generator_func(*args, **kwargs)

    # 尝试获取第一个值
    first_item = await generator.__anext__()

    # 如果成功获取第一个值，创建新的生成器包装原生成器
    async def wrapped_generator():
        # 先yield第一个值
        yield first_item
        # 然后yield剩余的值
        async for item in generator:
            yield item

    # 创建流响应
    return EventSourceResponse(
        wrapped_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def error_wrapper(func: Callable, *args, **kwargs) -> Any:
    from .config import MAX_RETRIES
    for attempt in range(MAX_RETRIES + 1):  # 包含初始尝试，所以是 MAX_RETRIES + 1
        try:
            return await func(*args, **kwargs)
        except (HighlightError, RequestException) as e:

            if isinstance(e, HighlightError):
                if 'HighlightAI account suspended' in e.message:
                    return JSONResponse(
                        e.to_openai_error(),
                        status_code=e.response_status_code
                    )

            # 如果已经达到最大重试次数，返回错误响应
            if attempt == MAX_RETRIES:
                if isinstance(e, HighlightError):
                    return JSONResponse(
                        e.to_openai_error(),
                        status_code=e.response_status_code
                    )
                elif isinstance(e, RequestException):
                    return JSONResponse(
                        {
                            'error': {
                                'message': str(e),
                                "type": "http_error",
                                "code": "http_error"
                            }
                        },
                        status_code=500
                    )

            if attempt < MAX_RETRIES:
                continue
    return None


def decode_base64url_safe(data):
    """使用安全的base64url解码"""
    # 添加必要的填充
    missing_padding = len(data) % 4
    if missing_padding:
        data += '=' * (4 - missing_padding)

    return base64.urlsafe_b64decode(data)


def check_ban_content(content: str) -> bool:
    from .config import BAN_STRS
    ban_strs = BAN_STRS
    for ban_str in ban_strs:
        if ban_str in content:
            return True
    return False


def check_ban_delay(delays: list[int], contents: list[str]) -> bool:
    # 特征
    # 延迟有多个1000ms 间隔
    # 剔除掉1000ms 平均响应间隔为200ms
    # 每个 content 内容较短
    # 1. 数出delays内大于1000的数
    count_over_1000 = sum(1 for delay in delays if delay > 1000)
    len_delays = len(delays)

    # 2. 剔除掉delays内大于1000的值，求平均
    filtered_delays = [delay for delay in delays if delay <= 1000]
    if filtered_delays:
        avg_delay = sum(filtered_delays) / len(filtered_delays)
    else:
        avg_delay = 0

    # 3. 将contents空值剔除，计算出平均长度
    non_empty_contents = [content for content in contents if content]
    if non_empty_contents:
        avg_content_length = sum(len(content) for content in non_empty_contents) / len(non_empty_contents)
    else:
        avg_content_length = 0

    logger.debug(
        f"检查ban内容特征: 大于1000延迟数:{count_over_1000} 平均延迟:{avg_delay} 平均长度:{avg_content_length} 延迟长度: {len_delays}")

    content = "".join(contents)

    # 延迟大于1000的超过1个 平均延迟大于190 小于350 平均长度 大于4 小于6 延迟长度大于25 疑似封号
    if 1 < count_over_1000 and 190 < avg_delay < 350 and 4 < avg_content_length < 6 and 25 < len_delays:
        logger.error(
            f"疑似封号内容\n检查ban内容特征: 大于1000延迟数:{count_over_1000} 平均延迟:{avg_delay} 平均长度:{avg_content_length} 延迟长度: {len_delays}\n{content}")

        CheckBanContent.get_instance().add_ban_content(content)
        return True
    return False


class MatchResult(Enum):
    MATCH_SUCCESS = "匹配成功"
    NO_MATCH = "未匹配"
    NEED_MORE_CONTENT = "还需更多内容"


class CheckBanContent:
    _instance = None
    _initialized = False
    ban_contents = [
        "We've temporarily restricted access to your account due to suspicious activity. If you think this is a mistake, please reach out to us via support@highlightai.com or Discord.",
        "Our monitoring systems have detected behavior associated with policy violations, resulting in account restrictions being applied. For questions or to request a review, please contact us."
        "Hey there! We’ve had to pause some of your account features due to activities that triggered our community guidelines. Think we made an error? Just shoot us a message using the help icon at the top right of this chat!",
        "We’ve detected unusual activity on your account and have restricted access to certain features. Please contact our support team if you believe this is an error.",
        "Your account access is limited as a precaution against activity that may conflict with our guidelines. Contact Highlight support if you wish to dispute this action.",
        "We’ve restricted your Highlight account. If you believe you’re receiving this message in error, please contact our support team at support@highlightai.com.",
        "We’ve detected unusual activity on your account and have restricted access to certain features. Please contact our support team if you believe this is an error.",
        "Your account access is limited as a precaution against activity that may conflict with our guidelines. Contact Highlight support if you wish to dispute this action.",
        "We've applied a restriction to your account after detecting behavior outside our acceptable use policy. Please get in touch with us if you think this was applied incorrectly."
    ]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CheckBanContent, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        # 确保只初始化一次
        if not CheckBanContent._initialized:
            self.ban_content_set = self.load_ban_content()
            CheckBanContent._initialized = True

    def load_ban_content(self) -> Set[str]:
        path = Path('./config/ban_contents.json')
        if not path.is_file():
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.ban_contents, f, ensure_ascii=False, indent=4)
        with open(path, 'r', encoding='utf-8') as f:
            return set(json.load(f))  # 使用内置的 set() 而不是 Set()

    def save_ban_content(self):
        path = Path('./config/ban_contents.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(list(self.ban_content_set), f, ensure_ascii=False, indent=4)

    def add_ban_content(self, content: str):
        self.ban_content_set.add(content)
        self.save_ban_content()

    def match_string_with_set(self, content: str) -> MatchResult:
        from .config import MATCH_SUCCESS_LEN
        """
        根据输入的字符串和字符串集合进行匹配

        Args:
            content: 当前输入的字符串

        Returns:
            MatchResult: 匹配结果枚举
        """
        # 1. 检查完全匹配（字符串完全相等）
        for s in self.ban_content_set:
            if content == s:
                return MatchResult.MATCH_SUCCESS

        # 2. 找出所有以content为开头的字符串
        matching_prefixes = []
        for s in self.ban_content_set:
            if s.startswith(content):
                matching_prefixes.append(s)

        # 3. 如果没有任何字符串以content开头
        if not matching_prefixes:
            return MatchResult.NO_MATCH

        # 4. 检查是否有字符串满足长度条件（content长度 >= 目标字符串长度的50%）
        for s in matching_prefixes:
            if len(content) >= len(s) * MATCH_SUCCESS_LEN:
                return MatchResult.MATCH_SUCCESS

        # 5. content是多个字符串的开头，但长度都不满足50%条件
        return MatchResult.NEED_MORE_CONTENT

    @classmethod
    def get_instance(cls):
        """获取单例实例的便捷方法"""
        return cls()
