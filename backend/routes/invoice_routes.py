from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId
from datetime import datetime
import hashlib
import os
from dotenv import load_dotenv
from pathlib import Path
from backend.auth import get_current_user

# Nạp đúng `backend/.env` dù bạn chạy lệnh từ thư mục nào.
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

router = APIRouter()
client = MongoClient(os.getenv("MONGO_URI"), serverSelectionTimeoutMS=5000)
db = client[os.getenv("MONGO_DB_NAME", "DATN11")]


# ===== HÀM TÍNH HASH FILE =====
def calculate_bytes_hash(file_bytes: bytes) -> str:
    sha256 = hashlib.sha256()
    sha256.update(file_bytes)
    return sha256.hexdigest()


def _serialize_bson(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize_bson(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_bson(v) for v in value]
    return value


# ===== TẠO HÓA ĐƠN/HỢP ĐỒNG MỚI =====
@router.post("/create")
async def create_invoice(
    invoice_id: str = Form(...),
    title: str = Form(...),
    party_a: str = Form(...),
    party_b: str = Form(...),
    amount: float = Form(...),
    date: str = Form(...),
    content: str = Form(""),
    file: UploadFile | str | None = File(None),
    current_user: dict = Depends(get_current_user),
):
    enterprise_id = current_user.get("enterprise_id")
    if not enterprise_id:
        raise HTTPException(status_code=401, detail="Token thieu enterprise_id")

    try:
        existing = db.invoices.find_one({"invoice_id": invoice_id, "enterprise_id": enterprise_id})
    except PyMongoError:
        raise HTTPException(status_code=500, detail="Loi MongoDB")
    if existing:
        raise HTTPException(status_code=400, detail=f"invoice_id '{invoice_id}' da ton tai")

    try:
        parsed_date = datetime.fromisoformat(date.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(status_code=400, detail="date phai dang ISO, vi du 2026-03-22T00:00:00Z")

    file_hash = None
    file_meta = {}
    # Tranh 422 khi Swagger gui file rong (""): chuyen thanh loi 400 de de hieu hon.
    if isinstance(file, str):
        file = None

    if file is not None:
        allowed_types = ["application/pdf", "text/xml", "application/xml"]
        if file.content_type not in allowed_types:
            raise HTTPException(status_code=400, detail="Chi chap nhan file PDF/XML")
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="File dinh kem dang rong")
        file_hash = calculate_bytes_hash(file_bytes)
        file_meta = {"file_name": file.filename, "file_content_type": file.content_type}
    elif content.strip():
        # Neu chua co file, van cho phep tao tu noi dung text va hash noi dung.
        file_hash = calculate_bytes_hash(content.strip().encode("utf-8"))
    else:
        raise HTTPException(
            status_code=400,
            detail="Ban can upload file PDF/XML hoac nhap content de tao van tay hash",
        )

    sub = current_user.get("sub")
    created_by = None
    if sub:
        try:
            created_by = ObjectId(sub)
        except Exception:
            created_by = sub

    new_invoice = {
        "invoice_id": invoice_id,
        "enterprise_id": enterprise_id,
        "title": title,
        "parties": {"party_a": party_a, "party_b": party_b},
        "amount": amount,
        "content": content,
        "date": parsed_date,
        "file_hash": file_hash,
        "blockchain_tx": None,
        "qr_code": None,
        "status": "draft",
        "created_by": created_by,
        "created_at": datetime.utcnow(),
        **file_meta,
    }

    try:
        result = db.invoices.insert_one(new_invoice)
    except PyMongoError:
        raise HTTPException(status_code=500, detail="Loi ghi MongoDB")

    return {"_id": str(result.inserted_id), "invoice_id": invoice_id, "file_hash": file_hash, "status": "draft"}


# ===== XEM DANH SÁCH HÓA ĐƠN =====
@router.get("/list")
def get_invoices(current_user: dict = Depends(get_current_user)):
    enterprise_id = current_user.get("enterprise_id")
    if not enterprise_id:
        raise HTTPException(status_code=401, detail="Token thieu enterprise_id")

    query = {"enterprise_id": enterprise_id}
    invoices = list(db.invoices.find(query).sort("created_at", -1))
    invoices = [_serialize_bson(inv) for inv in invoices]

    return {"total": len(invoices), "invoices": invoices}


# ===== XEM CHI TIẾT 1 HÓA ĐƠN =====
@router.get("/{invoice_id}")
def get_invoice_detail(
    invoice_id: str,
    current_user: dict = Depends(get_current_user),
):
    enterprise_id = current_user.get("enterprise_id")
    if not enterprise_id:
        raise HTTPException(status_code=401, detail="Token thieu enterprise_id")

    invoice = None
    # Cho phep truyen ca Mongo _id lẫn ma invoice_id (vd: HD001)
    try:
        invoice = db.invoices.find_one({"_id": ObjectId(invoice_id)})
    except Exception:
        invoice = None
    if not invoice:
        invoice = db.invoices.find_one({"invoice_id": invoice_id})

    if not invoice:
        raise HTTPException(status_code=404, detail="Khong tim thay hoa don")

    if invoice.get("enterprise_id") != enterprise_id:
        raise HTTPException(status_code=403, detail="Khong co quyen xem hoa don nay")

    return _serialize_bson(invoice)


# ===== XÓA HÓA ĐƠN =====
@router.delete("/{invoice_id}")
def delete_invoice(
    invoice_id: str,
    current_user: dict = Depends(get_current_user),
):
    enterprise_id = current_user.get("enterprise_id")
    if not enterprise_id:
        raise HTTPException(status_code=401, detail="Token thieu enterprise_id")

    invoice = None
    delete_query = None
    try:
        oid = ObjectId(invoice_id)
        invoice = db.invoices.find_one({"_id": oid})
        delete_query = {"_id": oid}
    except Exception:
        invoice = db.invoices.find_one({"invoice_id": invoice_id})
        delete_query = {"invoice_id": invoice_id}

    if not invoice:
        raise HTTPException(status_code=404, detail="Khong tim thay hoa don")

    if invoice.get("enterprise_id") != enterprise_id:
        raise HTTPException(status_code=403, detail="Khong co quyen xoa hoa don nay")

    if invoice.get("status") != "draft":
        raise HTTPException(status_code=400, detail="Chi xoa duoc hoa don trang thai draft")

    db.invoices.delete_one(delete_query)

    return {"message": "Da xoa hoa don thanh cong!"}
