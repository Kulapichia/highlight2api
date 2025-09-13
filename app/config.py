"""应用配置"""
import json
import os
import sys

from loguru import logger

from app.utils import decode_base64url_safe

# Highlight AI 配置
HIGHLIGHT_BASE_URL = "https://chat-backend.highlightai.com"
# USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Highlight/1.3.61 Chrome/132.0.6834.210 Electron/34.5.8 Safari/537.36"
USER_AGENT = decode_base64url_safe(os.environ.get("HIGHLIGHT_USER_AGENT",
                                                  "TW96aWxsYS81LjAgKFdpbmRvd3MgTlQgMTAuMDsgV2luNjQ7IHg2NCkgQXBwbGVXZWJLaXQvNTM3LjM2IChLSFRNTCwgbGlrZSBHZWNrbykgSGlnaGxpZ2h0LzEuMy42MSBDaHJvbWUvMTMyLjAuNjgzNC4yMTAgRWxlY3Ryb24vMzQuNS44IFNhZmFyaS81MzcuMzY")).decode(
    "utf-8")

# 网络配置
TLS_VERIFY = os.environ.get("TLS_VERIFY", 'True').lower() == "true"

DEBUG = os.environ.get("DEBUG", 'False').lower() == "true"
if not DEBUG:
    logger.remove()
    logger.add(sys.stdout, level="INFO")

MAX_RETRIES = int(os.environ.get("MAX_RETRIES", '1'))
BAN_STRS = json.loads(decode_base64url_safe(os.environ.get('BAN_STRS',
                                                           'WyJZb3VyIGFjY291bnQgaGFzIGJlZW4gc2VjdXJlZCIsICJUbyBwcm90ZWN0IG91ciBjb21tdW5pdHkiLCAiWW91ciBhY2NvdW50IHN0YXR1cyBoYXMgYmVlbiB1cGRhdGVkIHRvICdyZXN0cmljdGVkJyIsICJzdXBwb3J0QGhpZ2hsaWdodC5pbmciLCAiV2VcdTIwMTl2ZSBkZXRlY3RlZCB1bnVzdWFsIiwgInN1cHBvcnRAaGlnaGxpZ2h0YWkuY29tIiwgIldlJ3ZlIHRlbXBvcmFyaWx5IHJlc3RyaWN0ZWQgYWNjZXNzIiwgImR1ZSB0byBzdXNwaWNpb3VzIGFjdGl2aXR5LiIsICJIaWdobGlnaHQgc3VwcG9ydCIsICJZb3VyIGFjY291bnQgYWNjZXNzIGlzIGxpbWl0ZWQiLCAib3VyIHN1cHBvcnQgdGVhbSIsICJXZVx1MjAxOXZlIGRldGVjdGVkIHVudXN1YWwgYWN0aXZpdHkiLCAiaGF2ZSByZXN0cmljdGVkIGFjY2VzcyJd')))

PROXY = os.environ.get('PROXY', '')
MATCH_SUCCESS_LEN = float(os.environ.get('MATCH_SUCCESS_LEN', '0.5'))
CHAT_SEMAPHORE = int(os.environ.get("CHAT_SEMAPHORE", '1'))
