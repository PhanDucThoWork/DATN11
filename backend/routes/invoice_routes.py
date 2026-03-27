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


# ===== TẠO HÓA ĐƠN/HỢP ĐỒNG MỚI =====
@router.post("/create")
async def create_invoice(
    invoice_id: str = Form(...),
    title: str = Form(...),
    party_a: str = Form(...),
    party_b: str = Form(...),
    amount: float = Form(...),
    date: str = Form(...),
    file: UploadFile | None = File(None),
    current_user: dict = Depends(get_current_user)
):
    enterprise_id = current_user.get("enterprise_id")
    if not enterprise_id:
        raise HTTPException(status_code=401, detail="Token missing enterprise_id")

    # Kiểm tra invoice_id đã tồn tại chưa (theo schema MongoDB bạn đưa).
    try:
        existing = db.invoices.find_one({"invoice_id": invoice_id, "enterprise_id": enterprise_id})
    except PyMongoError:
        raise HTTPException(status_code=500, detail="MongoDB error")
    if existing:
        raise HTTPException(status_code=400, detail=f"invoice_id '{invoice_id}' da ton tai")

    # Parse date ISO/string -> datetime
    try:
        parsed_date = datetime.fromisoformat(date.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(status_code=400, detail="date must be ISO format, e.g. 2026-03-22T00:00:00Z")

    file_hash = None
    file_meta = {}
    if file is not None:
        allowed_types = ["application/pdf", "text/xml", "application/xml"]
        if file.content_type not in allowed_types:
            raise HTTPException(status_code=400, detail="Chi chap nhan file PDF/XML")
        file_bytes = await file.read()
        file_hash = calculate_bytes_hash(file_bytes)
        file_meta = {"file_name": file.filename, "file_content_type": file.content_type}

    created_by = current_user.get("sub")
    try:
        created_by = ObjectId(created_by) if created_by else None
    except Exception:
        # nếu sub không phải ObjectId thì lưu string
        pass

    new_invoice = {
        "invoice_id": invoice_id,
        "enterprise_id": enterprise_id,
        "title": title,
        "parties": {"party_a": party_a, "party_b": party_b},
        "amount": amount,
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
        raise HTTPException(status_code=500, detail="MongoDB insert failed")

    return {"_id": str(result.inserted_id), "invoice_id": invoice_id, "file_hash": file_hash, "status": "draft"}


# ===== XEM DANH SÁCH HÓA ĐƠN =====
@router.get("/list")
def get_invoices(current_user: dict = Depends(get_current_user)):
    enterprise_id = current_user.get("enterprise_id")
    if not enterprise_id:
        raise HTTPException(status_code=401, detail="Token missing enterprise_id")

    query = {"enterprise_id": enterprise_id}

    if current_user["role"] == "doi_tac":
        query["$or"] = [
            {"parties.party_a": current_user["email"]},
            {"parties.party_b": current_user["email"]}
        ]

    invoices = list(db.invoices.find(query).sort("created_at", -1))
    for inv in invoices:
        inv["_id"] = str(inv["_id"])

    return {"total": len(invoices), "invoices": invoices}


# ===== XEM CHI TIẾT 1 HÓA ĐƠN =====
@router.get("/{invoice_id}")
def get_invoice_detail(
    invoice_id: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        invoice = db.invoices.find_one({"_id": ObjectId(invoice_id)})
    except:
        raise HTTPException(status_code=400, detail="invoice_id không hợp lệ")

    if not invoice:
        raise HTTPException(status_code=404, detail="Không tìm thấy hóa đơn")

    if current_user["role"] == "doi_tac":
        if current_user["email"] not in [
            invoice["parties"]["party_a"],
            invoice["parties"]["party_b"]
        ]:
            raise HTTPException(status_code=403, detail="Không có quyền xem hóa đơn này")

    invoice["_id"] = str(invoice["_id"])
    return invoice


# ===== XÓA HÓA ĐƠN =====
@router.delete("/{invoice_id}")
def delete_invoice(
    invoice_id: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        invoice = db.invoices.find_one({"_id": ObjectId(invoice_id)})
    except:
        raise HTTPException(status_code=400, detail="invoice_id không hợp lệ")

    if not invoice:
        raise HTTPException(status_code=404, detail="Không tìm thấy hóa đơn")

    if invoice["status"] != "draft":
        raise HTTPException(status_code=400, detail="Chỉ xóa được hóa đơn ở trạng thái draft")

    # Chi cho xoa neu la super_admin hoac la nguoi tao
    if current_user.get("role") != "super_admin":
        if str(invoice.get("created_by")) != str(current_user.get("sub")):
            raise HTTPException(status_code=403, detail="Khong co quyen xoa hoa don nay")

    db.invoices.delete_one({"_id": ObjectId(invoice_id)})

    return {"message": "Đã xóa hóa đơn thành công!"}