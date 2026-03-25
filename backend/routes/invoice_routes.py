from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import hashlib
import os
import cloudinary
import cloudinary.uploader
from auth import get_current_user
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()
client = MongoClient(os.getenv("MONGO_URI"))
db = client["DATN11"]

# ===== CẤU HÌNH CLOUDINARY =====
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)


# ===== HÀM TÍNH HASH FILE =====
def calculate_bytes_hash(file_bytes: bytes) -> str:
    sha256 = hashlib.sha256()
    sha256.update(file_bytes)
    return sha256.hexdigest()


# ===== TẠO HÓA ĐƠN MỚI =====
@router.post("/create")
async def create_invoice(
    invoice_number: str = Form(...),
    title: str = Form(...),
    party_a: str = Form(...),
    party_b: str = Form(...),
    amount: float = Form(...),
    content: str = Form(""),
    invoice_date: str = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    # Kiểm tra quyền
    if current_user["role"] not in ["super_admin", "nhan_vien"]:
        raise HTTPException(status_code=403, detail="Không có quyền tạo hóa đơn")

    # Kiểm tra định dạng file
    allowed_types = ["application/pdf", "text/xml", "application/xml"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận file PDF hoặc XML")

    # Kiểm tra số hóa đơn đã tồn tại chưa
    existing = db.invoices.find_one({
        "invoice_number": invoice_number,
        "enterprise_id": current_user["enterprise_id"]
    })
    if existing:
        raise HTTPException(status_code=400, detail=f"Số hóa đơn '{invoice_number}' đã tồn tại")

    # Đọc bytes file
    file_bytes = await file.read()

    # Tính hash SHA-256 từ bytes (trước khi upload)
    file_hash = calculate_bytes_hash(file_bytes)

    # Upload lên Cloudinary
    try:
        upload_result = cloudinary.uploader.upload(
            file_bytes,
            folder="datn11/invoices",
            public_id=f"{current_user['enterprise_id']}_{invoice_number}",
            resource_type="raw",   # raw = PDF/XML (không phải ảnh/video)
            use_filename=True,
            unique_filename=False
        )
        file_url = upload_result["secure_url"]
        cloudinary_public_id = upload_result["public_id"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload Cloudinary thất bại: {str(e)}")

    # Lưu vào MongoDB
    new_invoice = {
        "invoice_number": invoice_number,
        "title": title,
        "parties": {
            "party_a": party_a,
            "party_b": party_b
        },
        "amount": amount,
        "content": content,
        "invoice_date": invoice_date,
        "file_name": file.filename,
        "file_url": file_url,                      # Link Cloudinary
        "cloudinary_public_id": cloudinary_public_id,
        "file_hash": file_hash,                    # Hash SHA-256
        "blockchain_tx": None,                     # Chức năng 3
        "qr_code": None,                           # Chức năng 3
        "status": "draft",
        "enterprise_id": current_user["enterprise_id"],
        "created_by": current_user["sub"],
        "created_at": datetime.utcnow()
    }

    result = db.invoices.insert_one(new_invoice)

    # Ghi audit log
    db.audit_logs.insert_one({
        "enterprise_id": current_user["enterprise_id"],
        "action": "create_invoice",
        "user_id": current_user["sub"],
        "timestamp": datetime.utcnow(),
        "details": {
            "invoice_number": invoice_number,
            "invoice_id": str(result.inserted_id),
            "result": "success"
        }
    })

    return {
        "message": "Tạo hóa đơn thành công!",
        "invoice_id": str(result.inserted_id),
        "invoice_number": invoice_number,
        "file_url": file_url,
        "file_hash": file_hash,
        "status": "draft"
    }


# ===== XEM DANH SÁCH HÓA ĐƠN =====
@router.get("/list")
def get_invoices(current_user: dict = Depends(get_current_user)):
    query = {"enterprise_id": current_user["enterprise_id"]}

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
    if current_user["role"] != "super_admin":
        raise HTTPException(status_code=403, detail="Chỉ super_admin mới xóa được")

    try:
        invoice = db.invoices.find_one({"_id": ObjectId(invoice_id)})
    except:
        raise HTTPException(status_code=400, detail="invoice_id không hợp lệ")

    if not invoice:
        raise HTTPException(status_code=404, detail="Không tìm thấy hóa đơn")

    if invoice["status"] != "draft":
        raise HTTPException(status_code=400, detail="Chỉ xóa được hóa đơn ở trạng thái draft")

    # Xóa file trên Cloudinary
    try:
        cloudinary.uploader.destroy(
            invoice["cloudinary_public_id"],
            resource_type="raw"
        )
    except:
        pass  # Không dừng dù xóa Cloudinary thất bại

    db.invoices.delete_one({"_id": ObjectId(invoice_id)})

    return {"message": "Đã xóa hóa đơn thành công!"}