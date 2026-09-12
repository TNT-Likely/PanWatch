"""应用升级检测模块（基于 Docker Hub tag）。"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from threading import Lock

import requests
from requests import exceptions as req_exc

_CACHE_LOCK = Lock()
_CACHE: dict[str, object] = {
    "ts": 0.0,
    "latest_version": None,
    "release_url": None,
    "error": None,
}
_CACHE_TTL_SECONDS = 15 * 60


def _normalize(version: str | None) -> str:
    return str(version or "").strip().lstrip("vV")


def _parse_semver(version: str | None) -> tuple[int, int, int] | None:
    v = _normalize(version)
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", v)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _extract_best_semver(tags: list[str]) -> str | None:
    best_sem: tuple[int, int, int] | None = None
    best_tag: str | None = None
    for raw in tags:
        tag = str(raw or "").strip()
        sem = _parse_semver(tag)
        if sem is None:
            continue
        if best_sem is None or sem > best_sem:
            best_sem = sem
            best_tag = _normalize(tag)
    return best_tag


def _build_proxies(proxy: str | None) -> dict[str, str] | None:
    p = str(proxy or "").strip()
    if not p:
        return None
    return {"http": p, "https": p}


def _fetch_latest_from_hub(repo: str, proxy: str | None = None) -> tuple[str | None, str | None, str | None]:
    # Docker Hub API:
    # GET /v2/namespaces/{namespace}/repositories/{repository}/tags?page_size=100
    parts = [p for p in repo.strip("/").split("/") if p]
    if len(parts) != 2:
        return None, None, "invalid_repo"
    namespace, repository = parts
    url = f"https://hub.docker.com/v2/namespaces/{namespace}/repositories/{repository}/tags?page_size=100"
    try:
        resp = requests.get(url, timeout=8, proxies=_build_proxies(proxy))
        if resp.status_code != 200:
            return None, None, f"hub_http_{resp.status_code}"
        data = resp.json() or {}
        results = data.get("results") or []
        tags = [str(item.get("name") or "").strip() for item in results]
        best_tag = _extract_best_semver(tags)
        tags_url = f"https://hub.docker.com/r/{namespace}/{repository}/tags"
        if best_tag:
            return best_tag, tags_url, None
        return None, tags_url, "no_semver_tag"
    except req_exc.Timeout:
        return None, None, "hub_timeout"
    except req_exc.ConnectionError:
        return None, None, "hub_unreachable"
    except Exception:
        return None, None, "hub_request_failed"


def _fetch_latest_from_registry(repo: str, proxy: str | None = None) -> tuple[str | None, str | None, str | None]:
    # Docker Registry API flow (same path as docker pull):
    # 1) GET token from auth.docker.io
    # 2) GET tags from registry-1.docker.io
    parts = [p for p in repo.strip("/").split("/") if p]
    if len(parts) != 2:
        return None, None, "invalid_repo"
    namespace, repository = parts
    repo_path = f"{namespace}/{repository}"
    tags_url = f"https://hub.docker.com/r/{namespace}/{repository}/tags"
    try:
        token_resp = requests.get(
            "https://auth.docker.io/token",
            params={
                "service": "registry.docker.io",
                "scope": f"repository:{repo_path}:pull",
            },
            timeout=8,
            proxies=_build_proxies(proxy),
        )
        if token_resp.status_code != 200:
            return None, tags_url, f"registry_auth_http_{token_resp.status_code}"
        token = str((token_resp.json() or {}).get("token") or "").strip()
        if not token:
            return None, tags_url, "registry_auth_no_token"

        tags_resp = requests.get(
            f"https://registry-1.docker.io/v2/{repo_path}/tags/list",
            headers={"Authorization": f"Bearer {token}"},
            params={"n": 200},
            timeout=8,
            proxies=_build_proxies(proxy),
        )
        if tags_resp.status_code != 200:
            return None, tags_url, f"registry_http_{tags_resp.status_code}"

        tags = (tags_resp.json() or {}).get("tags") or []
        if not isinstance(tags, list):
            return None, tags_url, "registry_invalid_tags"
        best_tag = _extract_best_semver([str(t) for t in tags])
        if best_tag:
            return best_tag, tags_url, None
        return None, tags_url, "no_semver_tag"
    except req_exc.Timeout:
        return None, tags_url, "registry_timeout"
    except req_exc.ConnectionError:
        return None, tags_url, "registry_unreachable"
    except Exception:
        return None, tags_url, "registry_request_failed"


def _fetch_latest_docker_tag(repo: str, proxy: str | None = None) -> tuple[str | None, str | None, str | None]:
    latest, release_url, err = _fetch_latest_from_hub(repo, proxy=proxy)
    if latest:
        return latest, release_url, None
    # Hub 网络失败时，回退到 registry 链路（通常和 docker pull 一致，更稳定）
    if err in {"hub_timeout", "hub_unreachable", "hub_request_failed"} or str(err).startswith("hub_http_"):
        r_latest, r_url, r_err = _fetch_latest_from_registry(repo, proxy=proxy)
        if r_latest:
            return r_latest, r_url, None
        return None, r_url or release_url, r_err or err
    return latest, release_url, err


def _human_error(err: str | None) -> str | None:
    code = str(err or "").strip()
    if not code:
        return None
    mapping = {
        "disabled": "已禁用升级检测",
        "invalid_repo": "升级检测配置无效",
        "no_semver_tag": "未找到可用版本标签",
        "hub_timeout": "连接 Docker Hub 超时",
        "hub_unreachable": "网络不可达，无法连接 Docker Hub",
        "hub_request_failed": "Docker Hub 请求失败",
        "registry_timeout": "连接 Docker Registry 超时",
        "registry_unreachable": "网络不可达，无法连接 Docker Registry",
        "registry_request_failed": "Docker Registry 请求失败",
        "registry_auth_no_token": "Docker Registry 鉴权失败（无 token）",
        "registry_invalid_tags": "Docker Registry 返回数据格式异常",
    }
    if code.startswith("hub_http_"):
        return f"Docker Hub 返回异常（HTTP {code.replace('hub_http_', '')}）"
    if code.startswith("registry_auth_http_"):
        return f"Docker Registry 鉴权异常（HTTP {code.replace('registry_auth_http_', '')}）"
    if code.startswith("registry_http_"):
        return f"Docker Registry 返回异常（HTTP {code.replace('registry_http_', '')}）"
    if code.startswith("http_"):
        return f"Docker Hub 返回异常（{code.replace('http_', 'HTTP ')}）"
    return mapping.get(code, "升级检测失败")


def check_update(current_version: str, proxy: str | None = None) -> dict[str, object]:
    repo = os.getenv("UPDATE_CHECK_DOCKER_REPO", "sunxiao0721/panwatch")
    force_disable = os.getenv("UPDATE_CHECK_DISABLE", "").strip() in {"1", "true", "True"}
    if force_disable:
        return {
            "enabled": False,
            "source": "docker",
            "current_version": _normalize(current_version),
            "latest_version": None,
            "update_available": False,
            "release_url": f"https://hub.docker.com/r/{repo}/tags",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "error": _human_error("disabled"),
        }

    now = time.monotonic()
    with _CACHE_LOCK:
        age = now - float(_CACHE["ts"] or 0)
        cache_has_value = _CACHE.get("latest_version") is not None or _CACHE.get("error") is not None
        if age <= _CACHE_TTL_SECONDS and cache_has_value:
            latest = str(_CACHE.get("latest_version") or "")
            release_url = str(_CACHE.get("release_url") or f"https://hub.docker.com/r/{repo}/tags")
            err = _human_error(str(_CACHE.get("error") or ""))
        else:
            latest, release_url, err = _fetch_latest_docker_tag(repo, proxy=proxy)
            _CACHE["ts"] = now
            _CACHE["latest_version"] = latest
            _CACHE["release_url"] = release_url
            _CACHE["error"] = err
            err = _human_error(err)

    current_norm = _normalize(current_version)
    cur_sem = _parse_semver(current_norm)
    latest_sem = _parse_semver(latest)
    update_available = bool(cur_sem and latest_sem and latest_sem > cur_sem)

    return {
        "enabled": True,
        "source": "docker",
        "current_version": current_norm,
        "latest_version": latest,
        "update_available": update_available,
        "release_url": release_url or f"https://hub.docker.com/r/{repo}/tags",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "error": err,
    }
