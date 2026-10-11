"""Browser OIDC endpoints. Redirects only use the configured canonical URL."""
import logging

import httpx
import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from src.modules.administration import oidc
from src.platform.persistence.database import get_db

router = APIRouter()
logger = logging.getLogger(__name__)


def _redirect(config: oidc.OIDCConfig, error: str | None = None) -> RedirectResponse:
    target = config.public_url + ("/login?oidc_error=" + error if error else "/")
    response = RedirectResponse(target, status_code=303)
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"})
    return response


@router.get("/login")
def login(request: Request, db: Session = Depends(get_db)):
    config = oidc.require_config()
    try:
        target, binding, browser_hash = oidc.start_login(db, config, request)
    except (oidc.OIDCError, httpx.HTTPError, ValueError, TypeError, KeyError):
        # Provider responses, tokens, codes and client credentials must not be logged.
        logger.warning("OIDC authorization could not start")
        return _redirect(config, "unavailable")
    response = RedirectResponse(target, status_code=303)
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    for name in oidc.obsolete_cookies(db, request, config):
        response.delete_cookie(name, path=config.cookie_path, secure=config.secure_cookie, httponly=True, samesite="lax")
    response.set_cookie(oidc.generation_cookie(config.transaction_cookie, browser_hash), binding, max_age=oidc.TRANSACTION_SECONDS + config.session_seconds, path=config.cookie_path, secure=config.secure_cookie, httponly=True, samesite="lax")
    return response


@router.get("/callback")
def callback(request: Request, db: Session = Depends(get_db)):
    config = oidc.require_config()
    transaction = None
    try:
        params = request.query_params
        if len(params.getlist("state")) != 1 or len(params.getlist("code")) > 1 or len(params.getlist("iss")) > 1 or len(params.getlist("error")) > 1:
            raise oidc.OIDCError("Ambiguous callback")
        transaction = oidc.consume_transaction(db, config, params.get("state", ""), request)
        if params.get("error"):
            oidc.cancel_transaction(db, transaction)
            response = _redirect(config, "cancelled")
        else:
            if params.get("iss") is not None and params["iss"] != config.issuer:
                raise oidc.OIDCError("Authorization response issuer mismatch")
            session_id, _ = oidc.finish_login(db, config, params.get("code", ""), transaction)
            response = _redirect(config)
            for name in oidc.obsolete_cookies(db, request, config):
                response.delete_cookie(name, path=config.cookie_path, secure=config.secure_cookie, httponly=True, samesite="lax")
            response.set_cookie(oidc.generation_cookie(config.session_cookie, transaction.browser_hash), session_id, max_age=config.session_seconds, path=config.cookie_path, secure=config.secure_cookie, httponly=True, samesite="lax")
    except PermissionError:
        if transaction is not None:
            oidc.cancel_transaction(db, transaction)
        response = _redirect(config, "denied")
    except (oidc.OIDCError, httpx.HTTPError, jwt.PyJWTError, ValueError, TypeError, KeyError):
        if transaction is not None:
            oidc.cancel_transaction(db, transaction)
        logger.warning("OIDC callback validation failed")
        response = _redirect(config, "failed")
    # Keep this generation's binding until its bounded cookie horizon. A held
    # successful response must remain associated with later login/logout requests.
    # No response clears fixed-name legacy cookies or rewrites a live binding.
    return response
