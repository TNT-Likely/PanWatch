"""统一 API 响应格式中间件"""
import json

from starlette.types import ASGIApp, Receive, Scope, Send


class ResponseWrapperMiddleware:
    """将所有 /api/ 响应包装为标准格式: {code, success, data, message}

    使用纯 ASGI 实现，避免 BaseHTTPMiddleware 的已知 streaming hang 问题。
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return

        status_code = 200
        response_headers: list[tuple[bytes, bytes]] = []
        body_parts: list[bytes] = []

        async def capture_send(message):
            nonlocal status_code, response_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = list(message.get("headers", []))
            elif message["type"] == "http.response.body":
                body_parts.append(message.get("body", b""))

        await self.app(scope, receive, capture_send)

        # 检查是否 JSON 响应
        content_type = ""
        for key, value in response_headers:
            if key.lower() == b"content-type":
                content_type = value.decode()
                break

        body = b"".join(body_parts)

        if "application/json" not in content_type:
            # 非 JSON 响应，原样返回
            await send({"type": "http.response.start", "status": status_code, "headers": response_headers})
            await send({"type": "http.response.body", "body": body})
            return

        try:
            original_data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            await send({"type": "http.response.start", "status": status_code, "headers": response_headers})
            await send({"type": "http.response.body", "body": body})
            return

        if 200 <= status_code < 300:
            # 允许业务层在 2xx 中显式返回 success/code/message
            if isinstance(original_data, dict) and "success" in original_data:
                success = bool(original_data.get("success"))
                raw_code = original_data.get("code")
                try:
                    code = int(raw_code) if raw_code is not None else (0 if success else 1)
                except Exception:
                    code = 0 if success else 1
                if success and code != 0:
                    code = 0
                if (not success) and code == 0:
                    code = 1

                if success:
                    # 统一成功返回：message 为空
                    message = ""
                    data = original_data.get("data")
                    if data is None:
                        data = {
                            k: v
                            for k, v in original_data.items()
                            if k not in ("code", "success", "message")
                        }
                else:
                    # 统一失败返回：data 为空
                    message = str(original_data.get("message") or "failed")
                    data = None

                wrapped = {
                    "code": code,
                    "success": success,
                    "data": data,
                    "message": message,
                }
            else:
                # 默认 2xx 视为成功
                wrapped = {"code": 0, "success": True, "data": original_data, "message": ""}
        else:
            detail = original_data.get("detail", original_data) if isinstance(original_data, dict) else original_data
            code = status_code
            message: str
            if isinstance(detail, dict):
                raw_code = detail.get("code")
                try:
                    if raw_code is not None:
                        code = int(raw_code)
                except Exception:
                    code = status_code
                message = str(
                    detail.get("message")
                    or detail.get("detail")
                    or json.dumps(detail, ensure_ascii=False)
                )
            else:
                message = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
            if code == 0:
                code = status_code if status_code != 0 else 1
            wrapped = {"code": code, "success": False, "data": None, "message": message}

        new_body = json.dumps(wrapped, ensure_ascii=False).encode()

        # 更新 content-length header
        new_headers = []
        for key, value in response_headers:
            if key.lower() == b"content-length":
                new_headers.append((key, str(len(new_body)).encode()))
            else:
                new_headers.append((key, value))

        await send({"type": "http.response.start", "status": status_code, "headers": new_headers})
        await send({"type": "http.response.body", "body": new_body})
