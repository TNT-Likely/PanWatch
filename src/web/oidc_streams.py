"""Recheck the exact OIDC session before sending each private SSE chunk."""
import anyio
from starlette.types import ASGIApp, Receive, Scope, Send

from src.modules.administration.oidc import session_is_active


class OIDCStreamSessionMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        streaming = False
        finished = False
        # Cancelling this local scope closes the underlying generator/task group;
        # do not keep consuming private events after logout or session expiry.
        with anyio.CancelScope() as stream_scope:
            async def send_checked(message):
                nonlocal streaming, finished
                if finished:
                    return
                if message['type'] == 'http.response.start':
                    streaming = any(key.lower() == b'content-type' and b'text/event-stream' in value.lower() for key, value in message.get('headers', []))
                identity = scope.get('state', {}).get('oidc_session_identity')
                if streaming and identity and message['type'] == 'http.response.body':
                    if not await anyio.to_thread.run_sync(session_is_active, identity):
                        finished = True
                        await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
                        stream_scope.cancel()
                        return
                await send(message)

            await self.app(scope, receive, send_checked)
