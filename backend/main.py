import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

# Cho phép chạy trực tiếp: `python backend/main.py`
# (khi chạy kiểu này, thư mục project root có thể không nằm trong sys.path).
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from backend.routes.auth_routes import router as auth_router

app = FastAPI(title="Invoice Certification System")

# Cho phép React gọi API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/auth", tags=["Auth"])

@app.get("/")
def root():
    # Để bạn bấm vào `/` là vào thẳng Swagger UI test nhanh.
    return RedirectResponse(url="/docs")


if __name__ == "__main__":
    # Chạy nhanh để test local.
    import uvicorn
    import os
    import socket

    # Tắt reload để tránh chạy 2 process (làm log hiện lặp).
    #
    # Nếu cổng 8000 đang bị chiếm (WinError 10048), tự đổi sang cổng tiếp theo để server vẫn chạy.
    host = os.getenv("HOST", "127.0.0.1")
    start_port = int(os.getenv("PORT", "8000"))

    def _port_is_free(h: str, p: int) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((h, p))
            return True
        except OSError:
            return False
        finally:
            s.close()

    port = start_port
    for _ in range(20):
        if _port_is_free(host, port):
            break
        port += 1

    uvicorn.run("backend.main:app", host=host, port=port, reload=False)
