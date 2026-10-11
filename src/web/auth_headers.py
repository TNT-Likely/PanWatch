"""Do not cache authentication responses or leak callback URLs as referrers."""
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send


class AuthResponseHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        path = scope.get('path', '')
        root = scope.get('root_path', '').rstrip('/')
        if root and (path == root or path.startswith(root + '/')):
            path = path[len(root):]
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        async def send_auth(message):
            protected = scope.get('state', {}).get('oidc_session_identity')
            if message['type'] == 'http.response.start' and (path.startswith('/api/auth/') or protected):
                headers = MutableHeaders(scope=message)
                headers['Cache-Control'] = 'no-store'
                headers['Pragma'] = 'no-cache'
                headers['Referrer-Policy'] = 'no-referrer'
                vary = [item.strip() for item in headers.get('Vary', '').split(',') if item.strip()]
                for name in ('Cookie', 'Authorization'):
                    if name.lower() not in {item.lower() for item in vary}:
                        vary.append(name)
                headers['Vary'] = ', '.join(vary)
            await send(message)

        await self.app(scope, receive, send_auth)
