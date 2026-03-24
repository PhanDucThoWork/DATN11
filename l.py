from pymongo import MongoClient

uri = "mongodb+srv://thopro532004_db_user:Datn11Abc123@cluster0.xo7xio7.mongodb.net/DATN11?appName=Cluster0"


try:
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    client.server_info()  # Buộc kết nối ngay
    print("✅ Kết nối thành công!")
    print("Collections:", client["DATN11"].list_collection_names())
except Exception as e:
    print(f"❌ Lỗi: {e}")
