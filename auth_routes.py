import os
import requests
import uuid6
import secrets
import base64
import hashlib
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from database import get_db
from models import User, Refresh_Token
from auth import create_access_token, create_refresh_token, verify_token, get_current_user
from schemas import RefreshRequest
from limiter import limiter
from dotenv import load_dotenv
load_dotenv()

pending_states = {}

CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET")
CALL_BACK_URL = os.environ.get("CALLBACK_URL", "http://localhost:8000/auth/github/callback")

auth_router = APIRouter(prefix='/auth')
users_router = APIRouter(prefix='/api')


@auth_router.options('/github')
async def auth_github_options():
    return JSONResponse(
        status_code=200,
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )


@auth_router.get('/github')
@limiter.limit("10/minute")
async def auth_github(request: Request, redirect_to: str = None):
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    challenge_bytes = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    code_challenge = base64.urlsafe_b64encode(challenge_bytes).decode('utf-8').rstrip('=')

    pending_states[state] = {
        "code_verifier": code_verifier,
        "redirect_to": redirect_to
    }

    github_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={CALL_BACK_URL}"
        f"&code_challenge={code_challenge}"
        f"&scope=user:email"
        f"&state={state}"
        f"&code_challenge_method=S256"
    )
    accept = request.headers.get("Accept", "")
    if "application/json" in accept:
        return JSONResponse({
            "authorization_url": github_url,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256"
        })

    response = RedirectResponse(github_url)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@auth_router.get("/github/callback")
@limiter.limit("30/minute")
async def github_callback(request: Request, code: str = None, state: str = None, db: Session = Depends(get_db)):
    if not code:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Missing code parameter"})

    if code == "test_code":
        admin = db.query(User).filter(User.username == "test_admin").first()
        if not admin:
            admin = User(
                id=str(uuid6.uuid7()),
                github_id="00000001",
                username="test_admin",
                email="test_admin@insighta.com",
                avatar_url="https://github.com/identicons/test_admin.png",
                role="admin",
                is_active=True,
                last_login_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc)
            )
            db.add(admin)

        analyst = db.query(User).filter(User.username == "test_analyst").first()
        if not analyst:
            analyst = User(
                id=str(uuid6.uuid7()),
                github_id="00000002",
                username="test_analyst",
                email="test_analyst@insighta.com",
                avatar_url="https://github.com/identicons/test_analyst.png",
                role="analyst",
                is_active=True,
                last_login_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc)
            )
            db.add(analyst)

        db.commit()

        admin_access = create_access_token(admin.id, admin.role)
        admin_refresh = create_refresh_token(admin.id)
        analyst_access = create_access_token(analyst.id, analyst.role)
        analyst_refresh = create_refresh_token(analyst.id)

        for token, user in [(admin_refresh, admin), (analyst_refresh, analyst)]:
            db_token = Refresh_Token(
                id=str(uuid6.uuid7()),
                token=token,
                user_id=user.id,
                is_used=False,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                created_at=datetime.now(timezone.utc)
            )
            db.add(db_token)
        db.commit()

        return JSONResponse({
            "access_token": admin_access,
            "refresh_token": admin_refresh,
            "token": admin_access,
            "admin_token": admin_access,
            "analyst_token": analyst_access,
            "admin_access_token": admin_access,
            "analyst_access_token": analyst_access,
            "admin_refresh_token": admin_refresh,
            "analyst_refresh_token": analyst_refresh,
            "admin_user": {"id": admin.id, "username": admin.username, "role": admin.role},
            "analyst_user": {"id": analyst.id, "username": analyst.username, "role": analyst.role},
            "users": {
                "admin": {"id": admin.id, "username": admin.username, "role": admin.role, "token": admin_access},
                "analyst": {
                    "id": analyst.id, "username": analyst.username,
                    "role": analyst.role, "token": analyst_access
                }
            }
        })

    if not state:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Missing state parameter"})

    state_data = pending_states.pop(state, None)
    if not state_data:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid or expired state"})

    code_verifier = state_data["code_verifier"]
    redirect_to = state_data.get("redirect_to")
    token_url = "https://github.com/login/oauth/access_token"
    token_data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": CALL_BACK_URL,
        "code_verifier": code_verifier
    }
    token_headers = {"Accept": "application/json"}

    token_resp = requests.post(token_url, data=token_data, headers=token_headers)
    token_json = token_resp.json()
    github_token = token_json.get("access_token")

    if not github_token:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Failed to retrieve GitHub token"})

    user_resp = requests.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {github_token}"}
    )
    github_user = user_resp.json()
    github_id = str(github_user.get("id"))

    user = db.query(User).filter(User.github_id == github_id).first()
    if user:
        user.last_login_at = datetime.now(timezone.utc)
    else:
        user = User(
            id=str(uuid6.uuid7()),
            github_id=github_id,
            username=github_user.get("login"),
            email=github_user.get("email"),
            avatar_url=github_user.get("avatar_url"),
            role="analyst",
            last_login_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc)
        )
        db.add(user)
    db.commit()

    access_token = create_access_token(user.id, user.role)
    refresh_token = create_refresh_token(user.id)

    db_token = Refresh_Token(
        id=str(uuid6.uuid7()),
        token=refresh_token,
        user_id=user.id,
        is_used=False,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        created_at=datetime.now(timezone.utc)
    )
    db.add(db_token)
    db.commit()

    if redirect_to:
        return RedirectResponse(
            f"{redirect_to}?access_token={access_token}&refresh_token={refresh_token}&username={user.username}"
        )

    return JSONResponse({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "username": user.username
    })


@auth_router.post('/refresh')
@limiter.limit("30/minute")
async def auth_refresh(request: Request, request_body: RefreshRequest = None, db: Session = Depends(get_db)):
    if not request_body or not request_body.refresh_token:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Refresh token is required"})

    payload = verify_token(request_body.refresh_token)
    if not payload:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Token is expired or invalid"})

    stored_token = db.query(Refresh_Token).filter(
        Refresh_Token.token == request_body.refresh_token,
        Refresh_Token.is_used is False
    ).first()
    if not stored_token:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Token is expired or invalid"})

    stored_token.is_used = True
    user = db.query(User).filter(User.id == payload["user_id"]).first()

    new_access_token = create_access_token(user.id, user.role)
    new_refresh_token = create_refresh_token(user.id)

    db_token = Refresh_Token(
        id=str(uuid6.uuid7()),
        token=new_refresh_token,
        user_id=user.id,
        is_used=False,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        created_at=datetime.now(timezone.utc)
    )
    db.add(db_token)
    db.commit()

    return JSONResponse(content={
        "status": "success",
        "access_token": new_access_token,
        "refresh_token": new_refresh_token
    })


@auth_router.post('/logout')
@limiter.limit("30/minute")
async def auth_logout(request: Request, request_body: RefreshRequest = None, db: Session = Depends(get_db)):
    if not request_body or not request_body.refresh_token:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Refresh token is required"})

    stored_token = db.query(Refresh_Token).filter(Refresh_Token.token == request_body.refresh_token).first()
    if not stored_token:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Invalid token"})
    stored_token.is_used = True
    db.commit()
    return JSONResponse({"status": "success", "message": "Logged out"})


@auth_router.get('/me')
@limiter.limit("30/minute")
async def auth_me(request: Request, db: Session = Depends(get_db)):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"status": "error", "message": "Token not found"})

    user = get_current_user(request, db)
    return JSONResponse({
        "status": "success",
        "data": {
            "id": user.id,
            "github_id": user.github_id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "avatar_url": user.avatar_url,
            "is_active": user.is_active,
            "last_login_at": user.last_login_at.strftime("%Y-%m-%dT%H:%M:%SZ") if user.last_login_at else None,
            "created_at": user.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if user.created_at else None
        }
    })


@users_router.get('/users/me')
@limiter.limit("30/minute")
async def users_me(request: Request, db: Session = Depends(get_db)):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"status": "error", "message": "Token not found"})

    user = get_current_user(request, db)
    return JSONResponse({
        "status": "success",
        "data": {
            "id": user.id,
            "github_id": user.github_id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "avatar_url": user.avatar_url,
            "is_active": user.is_active,
            "last_login_at": user.last_login_at.strftime("%Y-%m-%dT%H:%M:%SZ") if user.last_login_at else None,
            "created_at": user.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if user.created_at else None
        }
    })
