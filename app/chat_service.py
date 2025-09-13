import json
import time
import uuid
from typing import Dict, Any, AsyncGenerator, Optional

from curl_cffi import AsyncSession, Response
from fastapi.responses import JSONResponse
from loguru import logger

from .auth import get_access_token, get_highlight_headers, set_ban_rt
from .config import HIGHLIGHT_BASE_URL, TLS_VERIFY
from .errors import HighlightError
from .models import ChatCompletionResponse, Choice, Usage
from .utils import check_ban_delay, CheckBanContent, MatchResult


async def parse_sse_line(line: str) -> Optional[str]:
    """解析SSE数据行"""
    line = line.strip()
    if line.startswith("data: "):
        return line[6:]  # 去掉 'data: ' 前缀
    return None


async def stream_generator(
        highlight_data: Dict[str, Any], access_token: str, identifier: str, model: str, rt: str, proxy=None
) -> AsyncGenerator[Dict[str, Any], None]:
    """生成流式响应"""
    response_id = f"chatcmpl-{str(uuid.uuid4())}"
    created = int(time.time())

    full_content = ""

    for i in range(2):
        # 使用httpx的流式请求
        headers = get_highlight_headers(access_token, identifier)
        tool_call_idx = 0
        async with AsyncSession(verify=TLS_VERIFY, timeout=60, impersonate='chrome', proxy=proxy) as s:
            async with s.stream('POST',
                                HIGHLIGHT_BASE_URL + "/api/v1/chat",
                                headers=headers,
                                json=highlight_data) as response:
                response: Response
                req_id = uuid.uuid4()

                if response.status_code == 401 and i == 0:
                    access_token = await get_access_token(rt, True, proxy)
                    continue
                if response.status_code != 200:
                    text = await response.atext()
                    if 'Attention Required! | Cloudflare' in text:
                        text = 'Cloudflare 403'
                    raise HighlightError(response.status_code, text)

                # 发送初始消息
                is_send_initial_chunk = False
                last_timestamp_ms = None
                sse_content_time = []
                contents = []

                content_tmp = ''
                has_tool_use = False

                async for line in response.aiter_lines():
                    line = line.decode("utf-8")
                    logger.debug(f"req_id: {str(req_id)}, {line}")

                    # 解析SSE行
                    data = await parse_sse_line(line)
                    if data and data.strip():
                        try:
                            event_data = json.loads(data)
                            if event_data.get("type") == "text":
                                content = event_data.get("content", "")
                                if content:
                                    full_content += content

                                    match_result = CheckBanContent.get_instance().match_string_with_set(full_content)
                                    now_timestamp_ms = int(time.time() * 1000)
                                    if last_timestamp_ms:
                                        # logger.debug(now_timestamp_ms - last_timestamp_ms)
                                        sse_content_time.append(now_timestamp_ms - last_timestamp_ms)

                                    last_timestamp_ms = now_timestamp_ms
                                    contents.append(content)

                                    if match_result == MatchResult.MATCH_SUCCESS:
                                        set_ban_rt(rt)
                                        response.close()
                                        raise HighlightError(200, 'HighlightAI account suspended', 403)
                                    elif match_result == MatchResult.NEED_MORE_CONTENT:
                                        content_tmp += content
                                        continue

                                    if not is_send_initial_chunk:
                                        initial_chunk = {
                                            "id": response_id,
                                            "object": "chat.completion.chunk",
                                            "created": created,
                                            "model": model,
                                            "choices": [
                                                {
                                                    "index": 0,
                                                    "delta": {"role": "assistant"},
                                                    "finish_reason": None,
                                                }
                                            ],
                                        }
                                        yield {"data": json.dumps(initial_chunk)}

                                    chunk_data = {
                                        "id": response_id,
                                        "object": "chat.completion.chunk",
                                        "created": created,
                                        "model": model,
                                        "choices": [
                                            {
                                                "index": 0,
                                                "delta": {"content": content_tmp + content},
                                                "finish_reason": None,
                                            }
                                        ],
                                    }
                                    content_tmp = ''
                                    yield {"data": json.dumps(chunk_data)}
                            elif event_data.get("type") == "toolUse":
                                has_tool_use = True
                                tool_name = event_data.get("name", "")
                                tool_id = event_data.get("toolId", "")
                                tool_input = event_data.get("input", "")
                                if tool_name:
                                    chunk_data = {
                                        "id": response_id,
                                        "object": "chat.completion.chunk",
                                        "created": created,
                                        "model": model,
                                        "choices": [
                                            {
                                                "index": 0,
                                                "delta": {
                                                    "tool_calls": [
                                                        {
                                                            "index": tool_call_idx,
                                                            "id": tool_id,
                                                            "type": "function",
                                                            "function": {
                                                                "name": tool_name,
                                                                "arguments": tool_input,
                                                            },
                                                        }
                                                    ]
                                                },
                                                "finish_reason": None,
                                            }
                                        ],
                                    }
                                    tool_call_idx += 1
                                    # logger.debug(
                                    #     json.dumps({"data": json.dumps(chunk_data)}, ensure_ascii=False))
                                    yield {"data": json.dumps(chunk_data)}
                            elif event_data.get("type") == "error":
                                raise HighlightError(response.status_code, event_data.get('error'))
                        except json.JSONDecodeError:
                            # 忽略无效的JSON数据
                            continue

                if not full_content and not has_tool_use:
                    raise HighlightError(200, 'HighlightAI 空回复', 500)

                # 发送完成消息
                final_chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                # if check_ban_content(full_content):
                #     set_ban_rt(rt)
                yield {"data": json.dumps(final_chunk)}
                yield {"data": "[DONE]"}
                # logger.debug(sse_content_time)
                if check_ban_delay(sse_content_time, contents):
                    set_ban_rt(rt)
                return


async def non_stream_response(
        highlight_data: Dict[str, Any], access_token: str, identifier: str, model: str, rt: str, proxy=None
) -> JSONResponse:  # type: ignore
    """处理非流式响应"""
    for i in range(2):
        headers = get_highlight_headers(access_token, identifier)
        async with AsyncSession(verify=TLS_VERIFY, timeout=60, impersonate='chrome', proxy=proxy) as s:
            async with s.stream('POST',
                                HIGHLIGHT_BASE_URL + "/api/v1/chat",
                                headers=headers,
                                json=highlight_data) as response:
                response: Response
                if response.status_code == 401 and i == 0:
                    access_token = await get_access_token(rt, True, proxy)
                    continue

                if response.status_code != 200:
                    text = await response.atext()
                    if 'Attention Required! | Cloudflare' in text:
                        text = 'Cloudflare 403'
                    raise HighlightError(response.status_code, text)

                # 收集完整响应
                full_response = ""
                tool_calls = []
                last_timestamp_ms = None
                sse_content_time = []
                contents = []

                async for line in response.aiter_lines():
                    line = line.decode("utf-8")
                    logger.debug(line)
                    data = await parse_sse_line(line)
                    if data and data.strip():
                        try:
                            event_data = json.loads(data)
                            if event_data.get("type") == "text":
                                now_timestamp_ms = int(time.time() * 1000)
                                if last_timestamp_ms:
                                    # logger.debug(now_timestamp_ms - last_timestamp_ms)
                                    sse_content_time.append(now_timestamp_ms - last_timestamp_ms)

                                last_timestamp_ms = now_timestamp_ms
                                contents.append(event_data.get("content", ""))
                                full_response += event_data.get("content", "")
                            elif event_data.get("type") == "toolUse":
                                tool_name = event_data.get("name", "")
                                tool_id = event_data.get("toolId", "")
                                tool_input = event_data.get("input", "")
                                if tool_name:
                                    tool_calls.append({
                                        "id": tool_id,
                                        "type": "function",
                                        "function": {
                                            "name": tool_name,
                                            "arguments": tool_input,
                                        }
                                    })
                            elif event_data.get("type") == "error":
                                raise HighlightError(response.status_code, event_data.get('error'))
                        except json.JSONDecodeError:
                            continue

        # 创建 OpenAI 格式的响应
        response_id = f"chatcmpl-{str(uuid.uuid4())}"
        created = int(time.time())

        # 构建消息内容
        message_content: Dict[str, any] = {"role": "assistant"}

        if not tool_calls and not full_response:
            raise HighlightError(200, 'HighlightAI 空回复', 500)

        if full_response:
            message_content["content"] = full_response
        if tool_calls:
            message_content["tool_calls"] = tool_calls

        if check_ban_delay(sse_content_time, contents):
            set_ban_rt(rt)
            raise HighlightError(200, 'HighlightAI account suspended', 403)

        match_result = CheckBanContent.get_instance().match_string_with_set(full_response)
        if match_result == MatchResult.MATCH_SUCCESS:
            set_ban_rt(rt)
            raise HighlightError(200, 'HighlightAI account suspended', 403)

        response_data = ChatCompletionResponse(
            id=response_id,
            object="chat.completion",
            created=created,
            model=model,
            choices=[
                Choice(
                    index=0,
                    message=message_content,
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        )
        return JSONResponse(content=response_data.model_dump())
