import os
import jwt
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from fastapi import Request, HTTPException, Depends
from database import get_db
from models import User
from dotenv import load_dotenv
load_dotenv()

JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")


def create_token(data, exp_mins):
    payload = {**data}
    payload["exp"] = datetime.now(tz=timezone.utc) + timedelta(minutes=exp_mins)
    payload["iat"] = datetime.now(tz=timezone.utc)
    token = jwt.encode(payload=payload, key=JWT_SECRET_KEY, algorithm="HS256")
    return token


def create_access_token(user_id, role):
    data = {"user_id": user_id, "role": role}
    return create_token(data, 15)


def create_refresh_token(user_id):
    data = {"user_id": user_id}
    return create_token(data, 20)


def verify_token(token):
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"status": "error", "message": "Token not found"})
    token = auth_header.split(" ")[1]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail={"status": "error", "message": "Token is expired or invalid"})
    user = db.query(User).filter(User.id == payload["user_id"]).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail={"status": "error", "message": "Access denied"})
    return user


def require_role(role: str):
    def role_checker(current_user=Depends(get_current_user)):
        if current_user.role != role:
            raise HTTPException(status_code=403, detail={"status": "error", "message": "Insufficient permissions"})
        return current_user
    return role_checker


def check_api_version(request: Request):
    header = request.headers.get("X-API-Version")
    if not header or header != "1":
        raise HTTPException(status_code=400, detail={"status": "error", "message": "API version header required"})
