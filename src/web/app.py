from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from src.web.api import stocks, agents, settings, logs, providers, channels, datasources, accounts, history, news, market, auth, suggestions
from src.web.api.auth import get_current_user
from src.web.api.settings import get_app_version
from src.web.response import ResponseWrapperMiddleware

app = FastAPI(
    title="PanWatch API",
    version="0.1.0",
    redirect_slashes=False,  # 避免重定向丢失 Authorization header
)

app.add_middleware(ResponseWrapperMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 认证路由（无需登录）
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
# 市场指数（公共数据，无需登录）
app.include_router(market.router, prefix="/api/market", tags=["market"])

# 需要登录的路由
protected = [Depends(get_current_user)]
app.include_router(stocks.router, prefix="/api/stocks", tags=["stocks"], dependencies=protected)
app.include_router(accounts.router, prefix="/api", tags=["accounts"], dependencies=protected)
app.include_router(agents.router, prefix="/api/agents", tags=["agents"], dependencies=protected)
app.include_router(providers.router, prefix="/api/providers", tags=["providers"], dependencies=protected)
app.include_router(channels.router, prefix="/api/channels", tags=["channels"], dependencies=protected)
app.include_router(datasources.router, prefix="/api/datasources", tags=["datasources"], dependencies=protected)
app.include_router(settings.router, prefix="/api/settings", tags=["settings"], dependencies=protected)
app.include_router(logs.router, prefix="/api/logs", tags=["logs"], dependencies=protected)
app.include_router(history.router, prefix="/api", tags=["history"], dependencies=protected)
app.include_router(news.router, prefix="/api/news", tags=["news"], dependencies=protected)
app.include_router(suggestions.router, prefix="/api/suggestions", tags=["suggestions"], dependencies=protected)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/version")
async def version():
    """获取应用版本号（公开接口）"""
    return {"version": get_app_version()}
