import subprocess
import uuid
import os
import requests
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

class MergeRequest(BaseModel):
    video_url: str
    audio_url: str
    keyword: Optional[str] = "" # Thêm trường Keyword (có thể để trống)

def cleanup_files(files):
    for f in files:
        if os.path.exists(f):
            try:
                os.remove(f)
            except:
                pass

def download_file(url, filename):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, stream=True, timeout=30)
        response.raise_for_status()
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi tải file: {url}. Chi tiết: {str(e)}")

def ensure_font_exists():
    """Tải font Lora-Bold nếu chưa có"""
    font_path = "Lora-Bold.ttf"
    if not os.path.exists(font_path):
        print("Đang tải font Lora-Bold...")
        url = "https://github.com/google/fonts/raw/main/ofl/lora/Lora-Bold.ttf"
        download_file(url, font_path)
    return font_path

@app.post("/merge")
def merge_video_audio(request: MergeRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_v.mp4"
    input_audio = f"{req_id}_a.mp3"
    output_file = f"{req_id}_out.mp4"
    font_path = ensure_font_exists()

    # 1. Tải tài nguyên
    download_file(request.video_url, input_video)
    download_file(request.audio_url, input_audio)

    # 2. Xây dựng lệnh FFmpeg
    # Logic:
    # -stream_loop -1: Lặp video đầu vào vô hạn
    # -i input_video: Video đầu vào
    # -i input_audio: Audio đầu vào
    # -vf drawtext...: Bộ lọc vẽ chữ
    # -shortest: Kết thúc khi stream ngắn nhất (là audio) kết thúc
    
    # Cấu hình Text (màu đen, size 130, căn giữa)
    text_filter = ""
    if request.keyword:
        # Escape các ký tự đặc biệt để tránh lỗi command
        sanitized_text = request.keyword.replace(":", "\\:").replace("'", "")
        # Công thức căn giữa: x=(w-text_w)/2:y=(h-text_h)/2
        text_filter = f",drawtext=fontfile={font_path}:text='{sanitized_text}':fontcolor=black:fontsize=130:x=(w-text_w)/2:y=(h-text_h)/2"

    cmd = [
        "ffmpeg",
        "-stream_loop", "-1",    # Lặp video
        "-i", input_video,
        "-i", input_audio,
        "-filter_complex", f"[0:v]format=yuv420p{text_filter}[v]", # Áp dụng filter màu + text
        "-map", "[v]",           # Lấy video đã xử lý
        "-map", "1:a",           # Lấy audio
        "-c:v", "libx264",       # Encode lại video (Bắt buộc để chèn chữ)
        "-preset", "veryfast",   # Tăng tốc độ render (giảm CPU)
        "-c:a", "aac",           # Audio chuẩn
        "-shortest",             # Cắt theo độ dài audio
        "-y",
        output_file
    ]

    try:
        subprocess.run(cmd, check=True)
        
        background_tasks.add_task(cleanup_files, [input_video, input_audio, output_file])
        
        return FileResponse(
            path=output_file, 
            media_type='video/mp4', 
            filename="output_video_looped.mp4"
        )

    except subprocess.CalledProcessError as e:
        cleanup_files([input_video, input_audio, output_file])
        raise HTTPException(status_code=500, detail="Lỗi xử lý FFmpeg (Có thể do file lỗi hoặc text quá dài)")

@app.get("/")
def read_root():
    return {"Hello": "Luangiai.vn Video Render Engine (Loop + Text)"}
