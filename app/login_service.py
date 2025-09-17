import re
import uuid
from typing import Dict, Any

from curl_cffi import AsyncSession
from loguru import logger

from .config import HIGHLIGHT_BASE_URL, TLS_VERIFY


class LoginError(Exception):
    pass


async def process_highlight_login(login_link: str, proxy=None) -> Dict[str, Any]:
    """处理 Highlight 登录流程, 成功则返回用户信息字典, 失败则抛出 LoginError"""
    # 提取 code
    code_match = re.search(r'code=(.+)', login_link)
    if not code_match:
        raise LoginError("无法从链接中提取 code，请确保链接格式正确")

    code = code_match.group(1)
    chrome_device_id = str(uuid.uuid4())
    device_id = str(uuid.uuid4())

    async with AsyncSession(verify=TLS_VERIFY, timeout=30.0, impersonate='chrome', proxy=proxy) as client:
        # 第一步：交换 token
        try:
            exchange_response = await client.post(
                f'{HIGHLIGHT_BASE_URL}/api/v1/auth/exchange',
                headers={'Content-Type': 'application/json'},
                json={'code': code, 'amplitudeDeviceId': chrome_device_id}
            )
        except Exception as e:
            logger.error(f"交换Token网络请求失败: {e}")
            raise LoginError(f"网络请求失败: {e}")

        if exchange_response.status_code != 200:
            error_text = exchange_response.text
            logger.error(f"交换Token HTTP错误: {exchange_response.status_code} {error_text}")
            if exchange_response.status_code == 400:
                raise LoginError("请求格式错误，请检查授权代码是否正确或已过期")
            raise LoginError(f"登录服务暂时不可用 (错误代码: {exchange_response.status_code})")

        exchange_data = exchange_response.json()
        if not exchange_data.get('success'):
            error_message = exchange_data.get('error', "未知错误")
            logger.error(f"登录失败详情: {error_message}")
            if "expired" in error_message or "invalid" in error_message:
                raise LoginError("授权代码已过期或无效。请重新登录获取新的代码。")
            if "already used" in error_message:
                raise LoginError("此授权代码已被使用过，请重新登录获取新的代码。")
            raise LoginError(f"登录失败: {error_message}。")

        at = exchange_data['data']['accessToken']
        rt = exchange_data['data']['refreshToken']

        # 第二步：注册客户端 (失败不影响主流程)
        try:
            await client.post(
                f'{HIGHLIGHT_BASE_URL}/api/v1/users/me/client',
                headers={'Content-Type': 'application/json', 'authorization': f'Bearer {at}'},
                json={"client_uuid": device_id}
            )
        except Exception as e:
            logger.warning(f"注册客户端失败，但不影响登录: {e}")

        # 第三步：获取用户信息
        try:
            profile_response = await client.get(
                f'{HIGHLIGHT_BASE_URL}/api/v1/auth/profile',
                headers={'authorization': f'Bearer {at}'}
            )
            profile_response.raise_for_status()
            profile = profile_response.json()
            user_id = profile['id']
            email = profile['email']
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            raise LoginError(f"无法获取用户信息，请重试。如果问题持续存在，请重新登录。")

        logger.success(f'登录成功: {user_id} {email}')

        user_info = {
            'rt': rt,
            'user_id': user_id,
            'email': email,
            'client_uuid': device_id
        }
        if proxy:
            user_info['proxy'] = proxy
            
        return user_info
