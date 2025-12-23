import subprocess
import uuid
import os
import requests
import shutil
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

class MergeRequest(BaseModel):
    video_url: str
    audio_url: str
    keyword: Optional[str] = ""
    subtitle_content: Optional[str] = ""
    ping_pong: Optional[bool] = True # Mặc định BẬT chế độ loop mượt

def cleanup_files(files):
    for f in files:
        if os.path.exists(f):
            try:
                os.remove(f)
            except:
                pass

def download_file(url, filename, file_type="File"):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://google.com',
    }
    try:
        print(f"Đang tải {file_type} từ: {url}")
        response = requests.get(url, headers=headers, stream=True, timeout=60)
        
        if response.status_code != 200:
            if file_type == "Font":
                return False
            raise Exception(f"Mã lỗi {response.status_code}")
            
        with open(filename, 'wb') as f:
            response.raw.decode_content = True
            shutil.copyfileobj(response.raw, f)
        return True
    except Exception as e:
        if file_type == "Font": return False
        raise HTTPException(status_code=400, detail=f"Lỗi tải {file_type}: {str(e)}")

def ensure_font_exists():
    font_name = "Merriweather-Bold.ttf"
    if not os.path.exists(font_name):
        success = download_file("https://github.com/google/fonts/raw/main/ofl/merriweather/Merriweather-Bold.ttf", font_name, "Font")
        if not success: return None
    return font_name if os.path.exists(font_name) else None

def create_pingpong_video(input_path, output_path):
    """
    Tạo video Boomerang: [Gốc] + [Ngược]
    """
    try:
        cmd = [
            "ffmpeg",
            "-i", input_path,
            # Filter: Split ra 2 luồng, luồng 2 đảo ngược, rồi nối lại
            "-filter_complex", "[0:v]split[main][rev];[rev]reverse[r];[main][r]concat=n=2:v=1:a=0[v]",
            "-map", "[v]",
            "-c:v", "libx264",
            "-preset", "ultrafast", # Xử lý nhanh
            "-crf", "23",
            "-y",
            output_path
        ]
        subprocess.run(cmd, check=True)
        return True
    except Exception as e:
        print(f"Lỗi tạo Ping-Pong: {e}")
        return False

@app.post("/merge")
def merge_video_audio(request: MergeRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_v.mp4"
    pingpong_video = f"{req_id}_pp.mp4" # File trung gian
    input_audio = f"{req_id}_a.mp3"
    output_file = f"{req_id}_out.mp4"
    subtitle_file = f"{req_id}.srt"
    
    files_to_clean = [input_video, pingpong_video, input_audio, output_file, subtitle_file]

    try:
        # 1. Tải file
        download_file(request.video_url, input_video, "Video")
        download_file(request.audio_url, input_audio, "Audio")
        font_path = ensure_font_exists()

        # 2. Xử lý Ping-Pong (Nếu được yêu cầu)
        final_input_video = input_video
        if request.ping_pong:
            print("Đang tạo video Ping-Pong...")
            if create_pingpong_video(input_video, pingpong_video):
                final_input_video = pingpong_video

        # 3. Chuẩn bị Subtitle
        has_sub = False
        if request.subtitle_content and len(request.subtitle_content.strip()) > 0:
            with open(subtitle_file, "w", encoding="utf-8") as f:
                f.write(request.subtitle_content)
            has_sub = True

        # 4. Xây dựng Filter
        filters = []
        filters.append(f"[0:v]format=yuv420p[v0]") 
        last_stream = "[v0]"
        
        # Style Text (YouTube Thumbnail)
        if request.keyword:
            sanitized_text = request.keyword.replace(":", "\\:").replace("'", "")
            font_cmd = f"fontfile={font_path}:" if font_path else ""
            styling = "fontcolor=white:bordercolor=black:borderw=7:fontsize=130"
            position = "x=(w-text_w)/2:y=(h-text_h)/2"
            draw_cmd = f"drawtext={font_cmd}text='{sanitized_text}':{styling}:{position}"
            filters.append(f"{last_stream}{draw_cmd}[v1]")
            last_stream = "[v1]"

        # Style Subtitle
        if has_sub:
            font_name_sub = "Merriweather-Bold" if font_path else "Arial"
            style = f"FontName={font_name_sub},FontSize=24,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,MarginV=30,Alignment=2"
            sub_cmd = f"subtitles={subtitle_file}:fontsdir=.:force_style='{style}'"
            filters.append(f"{last_stream}{sub_cmd}[v2]")
            last_stream = "[v2]"

        filter_complex = ";".join(filters)
        
        cmd = [
            "ffmpeg",
            "-stream_loop", "-1",        # Loop video đầu vào (lúc này đã là ping-pong)
            "-i", final_input_video,     # Input file (Gốc hoặc Ping-Pong)
            "-i", input_audio,
            "-filter_complex", filter_complex,
            "-map", last_stream,
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "aac",
            "-shortest",                 # Cắt khi hết nhạc
            "-y",
            output_file
        ]

        subprocess.run(cmd, check=True)
        
        background_tasks.add_task(cleanup_files, files_to_clean)
        return FileResponse(path=output_file, media_type='video/mp4', filename="video_pingpong.mp4")

    except Exception as e:
        cleanup_files(files_to_clean)
        # In lỗi ra console của Railway để dễ debug
        print(f"CRITICAL ERROR: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
