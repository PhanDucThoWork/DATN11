from pydantic import BaseModel, EmailStr
from typing import Optional


class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str
    # Dang ky mac dinh la doi_tac, frontend khong can gui role.
    role: str = "doi_tac"
    enterprise_id: str

    # Các field này không có trong schema MongoDB bạn mô tả, nên để optional để tránh lỗi 422.
    full_name: Optional[str] = None
    phone: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    username: str
    email: EmailStr
    role: str
    enterprise_id: str

    # Có thể không tồn tại trong document nếu frontend không gửi/DB không lưu.
    full_name: Optional[str] = None
    is_active: bool
