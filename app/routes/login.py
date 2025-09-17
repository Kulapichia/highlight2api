from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from ..config import PROXY
from ..login_service import process_highlight_login, LoginError

router = APIRouter()


@router.post("/login")
async def handle_login_request(request: Request):
    """处理前端登录请求以生成 API Key"""
    try:
        body = await request.json()
        login_link = body.get("login_link")
        if not login_link:
            # Deno 前端发送的是 code，为了兼容，也检查 code
            code = body.get("code")
            if not code:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Missing login_link or code parameter"}
                )
            # 如果是 code，构造一个假的 login_link
            login_link = f"https://highlightai.com/deeplink?code={code}"

        proxy = body.get("proxy")
        if not proxy and PROXY:
            proxy = PROXY

        user_info = await process_highlight_login(login_link, proxy)
        return JSONResponse(content=user_info)

    except LoginError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"An unexpected error occurred: {e}"})


@router.get("/", response_class=FileResponse)
@router.get("/index.html", response_class=FileResponse)
async def login_page():
    """提供登录和 API Key 生成的前端页面"""
    return FileResponse("static/login.html")
