# Sử dụng Python 3.10 làm nền tảng
FROM python:3.10-slim

# 1. CÀI ĐẶT FFMPEG (Quan trọng nhất - Đây là bước fix lỗi của bạn)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# 2. Thiết lập thư mục làm việc
WORKDIR /app

# 3. Copy file requirements và cài thư viện Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy source code
COPY main.py .

# 5. Chạy app (Sử dụng biến môi trường PORT của Railway)
CMD uvicorn main:app --host 0.0.0.0 --port $PORT
