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
    keyword: Optional[str] = ""

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
        print(f"Warning: Không tải được file {url}. Lỗi: {e}")
        # Không raise Exception ở đây để code có thể chạy tiếp (nếu là lỗi font)

def ensure_font_exists():
    """
    Tải font Lora-Bold.
    Đường dẫn chuẩn: https://github.com/google/fonts/raw/main/ofl/lora/static/Lora-Bold.ttf
    """
    font_name = "Lora-Bold.ttf"
    if not os.path.exists(font_name):
        print(f"Đang tải font {font_name}...")
        # URL mới đã sửa (thêm /static/)
        url = "https://github.com/google/fonts/raw/main/ofl/lora/static/Lora-Bold.ttf"
        download_file(url, font_name)
    
    # Kiểm tra lại xem tải được không, nếu không thì trả về None để dùng font mặc định
    if os.path.exists(font_name):
        return font_name
    return None

@app.post("/merge")
def merge_video_audio(request: MergeRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_v.mp4"
    input_audio = f"{req_id}_a.mp3"
    output_file = f"{req_id}_out.mp4"
    
    # 1. Chuẩn bị file
    try:
        download_file(request.video_url, input_video)
        download_file(request.audio_url, input_audio)
        
        # Kiểm tra xem file video/audio có tải về thành công không
        if not os.path.exists(input_video) or not os.path.exists(input_audio):
            raise HTTPException(status_code=400, detail="Không thể tải Video hoặc Audio từ URL cung cấp.")

        font_path = ensure_font_exists()

        # 2. Xây dựng lệnh FFmpeg
        # Logic: Loop video (-stream_loop -1) + Chèn Text + Cắt theo Audio (-shortest)
        
        filter_complex = ""
        
        # Xử lý Text
        if request.keyword:
            sanitized_text = request.keyword.replace(":", "\\:").replace("'", "")
            
            # Nếu tải được font thì dùng font đó, không thì dùng font mặc định
            font_cmd = f"fontfile={font_path}:" if font_path else ""
            
            # Drawtext filter: Font Lora, Size 130, Màu đen, Căn giữa
            text_filter = f"drawtext={font_cmd}text='{sanitized_text}':fontcolor=black:fontsize=130:x=(w-text_w)/2:y=(h-text_h)/2"
            
            # Kết hợp filter: format pixel -> vẽ chữ
            filter_complex = f"[0:v]format=yuv420p,{text_filter}[v]"
        else:
            filter_complex = "[0:v]format=yuv420p[v]"

        cmd = [
            "ffmpeg",
            "-stream_loop", "-1",        # Lặp video đầu vào
            "-i", input_video,
            "-i", input_audio,
            "-filter_complex", filter_complex,
            "-map", "[v]",               # Map video stream từ filter
            "-map", "1:a",               # Map audio stream từ file audio
            "-c:v", "libx264",           # Encode H.264
            "-preset", "ultrafast",      # Render siêu nhanh để đỡ tốn RAM/CPU Railway
            "-c:a", "aac",
            "-shortest",                 # Ngắt khi hết audio
            "-y",
            output_file
        ]

        subprocess.run(cmd, check=True)
        
        background_tasks.add_task(cleanup_files, [input_video, input_audio, output_file])
        
        return FileResponse(
            path=output_file, 
            media_type='video/mp4', 
            filename="output_video_looped.mp4"
        )

    except subprocess.CalledProcessError as e:
        cleanup_files([input_video, input_audio, output_file])
        print(f"FFmpeg Error Log: {e}")
        raise HTTPException(status_code=500, detail="Lỗi xử lý FFmpeg (File lỗi hoặc Text không hợp lệ)")
    except Exception as e:
        cleanup_files([input_video, input_audio, output_file])
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/")
def read_root():
    return {"Hello": "Luangiai.vn Video Render Engine (Fixed Font Path)"}
