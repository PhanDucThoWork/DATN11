from fastapi import APIRouter, HTTPException
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from datetime import datetime
from backend.models import UserRegister, UserLogin
from backend.auth import hash_password, verify_password, create_access_token
import os
from dotenv import load_dotenv
from pathlib import Path
import json
import time
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

# Nạp đúng `backend/.env` dù bạn chạy lệnh từ thư mục nào.
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

router = APIRouter()

MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "DATN11")

_raw_mongo_uri = os.getenv("MONGO_URI")
_mongo_uri = _raw_mongo_uri
client = None
db = None
_mongo_error = None


def _debug_log(hypothesisId: str, location: str, message: str, data: dict | None = None) -> None:
    """
    Ghi NDJSON để debug runtime theo session 304bb2.
    Khong log secret (password/token).
    """
    log_path = Path(__file__).resolve().parent.parent / "debug-304bb2.log"
    payload = {
        "sessionId": "304bb2",
        "id": f"py_{int(time.time()*1000)}_{hypothesisId}",
        "timestamp": int(time.time() * 1000),
        "location": location,
        "message": message,
        "data": data or {},
        "runId": os.getenv("RUN_ID", "debug"),
        "hypothesisId": hypothesisId,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")

# Hỗ trợ trường hợp URL đang để placeholder `<db_password>`.
def _resolve_mongo_uri(uri: str) -> str | None:
    if not uri or "<db_password>" not in uri:
        return uri

    # Cho phép nhiều tên env để giảm sai sót khi bạn copy từ nơi khác.
    candidate_keys = [
        "MONGO_PASSWORD",
        "MONGO_DB_PASSWORD",
        "DB_PASSWORD",
        "db_password",
    ]
    for key in candidate_keys:
        val = os.getenv(key)
        if val:
            return uri.replace("<db_password>", val)

    # Khong crash server; loi se hien ra ro rang khi goi API.
    return None


def _ensure_auth_source(uri: str | None) -> str | None:
    """
    Atlas users are typically in `admin`.
    If URI has db path (e.g. /DATN11) and no authSource, force authSource=admin.
    """
    if not uri:
        return uri
    try:
        parsed = urlparse(uri)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if "authSource" not in query:
            query["authSource"] = "admin"
            new_query = urlencode(query)
            return urlunparse(parsed._replace(query=new_query))
        return uri
    except Exception:
        return uri

if _mongo_uri:
    _mongo_uri = _resolve_mongo_uri(_mongo_uri)
    _mongo_uri = _ensure_auth_source(_mongo_uri)
    #region agent log
    _debug_log(
        hypothesisId="H1_resolve_placeholder",
        location="auth_routes.py:resolve_mongo_uri",
        message="Resolved MONGO_URI (no secrets)",
        data={
            "raw_has_placeholder": bool(_raw_mongo_uri and "<db_password>" in _raw_mongo_uri),
            "candidate_env_keys_present": [
                k for k in ["MONGO_PASSWORD", "MONGO_DB_PASSWORD", "DB_PASSWORD", "db_password"] if os.getenv(k)
            ],
            "resolved_uri_is_none": _mongo_uri is None,
        },
    )
    #endregion

if _mongo_uri:
    try:
        # serverSelectionTimeoutMS de tranh treo khi Mongo khong ket noi duoc.
        client = MongoClient(_mongo_uri, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        db = client[MONGO_DB_NAME]
    except PyMongoError:
        client = None
        db = None
        _mongo_error = "Mongo authentication or connectivity failed."
    #region agent log
    _debug_log(
        hypothesisId="H2_mongo_ping",
        location="auth_routes.py:mongo_ping",
        message="Mongo ping result",
        data={
            "db_connected": db is not None,
            "mongo_db_name": MONGO_DB_NAME,
        },
    )
    #endregion

def _require_db():
    if db is None:
        placeholder_hint = ""
        if _raw_mongo_uri and "<db_password>" in _raw_mongo_uri:
            placeholder_hint = " (MONGO_URI dang con <db_password>)"
        #region agent log
        _debug_log(
            hypothesisId="H3_db_none_on_require",
            location="auth_routes.py:_require_db",
            message="Reject request because db is None",
            data={
                "raw_has_placeholder": bool(_raw_mongo_uri and "<db_password>" in _raw_mongo_uri),
                "resolved_db_none": True,
            },
        )
        #endregion
        raise HTTPException(
            status_code=500,
            detail=(
                "Khong ket noi duoc MongoDB. Hay kiem tra `MONGO_URI` va password trong `backend/.env`. "
                "Neu MONGO_URI con `<db_password>`, hay dat mot trong cac bien: `MONGO_PASSWORD`/`MONGO_DB_PASSWORD`/`DB_PASSWORD`. "
                + placeholder_hint
                + (f" Detail: {_mongo_error}" if _mongo_error else "")
            ),
        )

# ===== ĐĂNG KÝ =====
@router.post("/register")
def register(user: UserRegister):
    _require_db()
    try:
        # Kiểm tra email đã tồn tại chưa
        existing = db.users.find_one({"email": user.email})
        if existing:
            raise HTTPException(status_code=400, detail="Email da ton tai")

        # Hash password trước khi lưu
        hashed_pw = hash_password(user.password)

        new_user = {
            "username": user.username,
            "email": user.email,
            "password_hash": hashed_pw,
            # Dang ky luon la doi_tac (khong tin role tu client).
            "role": "doi_tac",
            "enterprise_id": user.enterprise_id,
            "created_at": datetime.utcnow(),
            "is_active": True,
        }

        # Cho phép lưu thêm nếu frontend gửi (không bắt buộc theo schema bạn mô tả).
        if user.full_name:
            new_user["full_name"] = user.full_name
        if user.phone:
            new_user["phone"] = user.phone

        db.users.insert_one(new_user)
        return {"message": "Dang ky thanh cong!"}
    except PyMongoError:
        raise HTTPException(status_code=500, detail="Loi truy cap MongoDB khi dang ky.")


# ===== ĐĂNG NHẬP =====
@router.post("/login")
def login(user: UserLogin):
    _require_db()
    try:
        # Tìm user theo email hoặc username (để Swagger test linh hoạt hơn).
        found = db.users.find_one(
            {"$or": [{"email": user.email}, {"username": user.email}]}
        )
        if not found:
            raise HTTPException(status_code=404, detail="Tai khoan khong ton tai")

        # Kiểm tra tài khoản còn hoạt động không
        if not found.get("is_active", True):
            raise HTTPException(status_code=403, detail="Tai khoan da bi khoa")

        # Kiểm tra password
        # Fallback thêm trường hợp document cũ không dùng tên field `password_hash`.
        password_hash = found.get("password_hash") or found.get("password")
        if not password_hash or not verify_password(user.password, password_hash):
            raise HTTPException(status_code=401, detail="Sai mat khau")

        # Tạo JWT token
        token = create_access_token(
            {
                "sub": str(found["_id"]),
                "email": found["email"],
                "role": found["role"],
                "enterprise_id": found["enterprise_id"],
            }
        )

        return {
            "access_token": token,
            "token_type": "bearer",
            "role": found.get("role"),
            "enterprise_id": found.get("enterprise_id"),
            "username": found.get("username"),
        }
    except PyMongoError:
        raise HTTPException(status_code=500, detail="Loi truy cap MongoDB khi dang nhap.")
