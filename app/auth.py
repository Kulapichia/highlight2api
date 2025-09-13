import base64
import json
import time
import uuid
from typing import Dict, Any, Optional

from curl_cffi import AsyncSession
from curl_cffi.requests.exceptions import RequestException
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from loguru import logger

from .config import HIGHLIGHT_BASE_URL, USER_AGENT, TLS_VERIFY
from .errors import HighlightError

# 存储格式：{rt: {"access_token": str, "expires_at": int,"is_ban":bool}}
access_tokens: Dict[str, Dict[str, Any]] = {}


def parse_api_key(api_key_base64: str) -> Optional[Dict[str, Any]]:
    """解析base64编码的JSON API Key"""
    try:
        decoded_bytes = base64.b64decode(api_key_base64)
        data = json.loads(decoded_bytes)
        return data
    except Exception:
        return None


async def get_user_info_from_token(credentials: HTTPAuthorizationCredentials) -> Dict[str, Any]:
    """从认证令牌中获取用户信息"""
    token = credentials.credentials

    # 尝试解析为base64编码的JSON
    user_info = parse_api_key(token)
    if user_info:
        return user_info

    raise HTTPException(status_code=401, detail="Invalid authorization token format")


async def refresh_access_token(rt: str, proxy: str | None = None) -> str:
    """使用refresh token获取新的access token"""
    logger.debug(f"{rt} 刷新")
    url = f"{HIGHLIGHT_BASE_URL}/api/v1/auth/refresh"
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "Idempotency-Key": str(uuid.uuid4())}
    json_data = {"refreshToken": rt}

    async with AsyncSession(verify=TLS_VERIFY, timeout=30.0, impersonate='chrome') as client:
        try:
            response = await client.post(url, headers=headers, json=json_data, proxy=proxy)

            if response.status_code != 200:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to refresh access token, response: {response.status_code} {response.text}"
                )

            resp_json = response.json()
            if not resp_json.get("success"):
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to refresh access token, response: {response.status_code} {response.text}"
                )

            access_token = resp_json["data"]["accessToken"]
            expires_in = resp_json["data"].get("expiresIn", 3600)  # 默认1小时
            expires_at = int(time.time()) + expires_in - 60  # 提前1分钟过期

            # 更新缓存
            access_tokens[rt] = {"access_token": access_token, "expires_at": expires_at, "is_ban": False}

            return access_token

        except RequestException as e:
            raise HTTPException(
                status_code=500, detail=f"HTTP error during token refresh: {str(e)}"
            )


async def get_access_token(rt: str, refresh=False, proxy: str = None) -> str:
    """获取access token（带缓存）"""
    if refresh:
        return await refresh_access_token(rt, proxy)

    current_time = int(time.time())

    # 检查缓存
    if rt in access_tokens:
        token_info = access_tokens[rt]
        is_ban = token_info.get("is_ban", False)
        if is_ban:
            raise HighlightError(200, 'HighlightAI account suspended', 403)
        if current_time < token_info["expires_at"]:
            return token_info["access_token"]

    # 缓存过期或不存在，刷新token
    return await refresh_access_token(rt)


def set_ban_rt(rt: str):
    access_tokens[rt]['is_ban'] = True


def is_ban_rt(rt: str):
    return access_tokens[rt]['is_ban']


def get_highlight_headers(access_token: str, identifier: str) -> Dict[str, str]:
    """获取Highlight API请求头"""
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-Highlight-Identifier": identifier,
    }
