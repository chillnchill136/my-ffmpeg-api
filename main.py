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
            raise Exception(f"Server trả về mã lỗi {response.status_code}")
        with open(filename, 'wb') as f:
            response.raw.decode_content = True
            shutil.copyfileobj(response.raw, f)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi tải {file_type}: {str(e)} | URL: {url}")

def ensure_font_exists():
    font_name = "Lora-Bold.ttf"
    if not os.path.exists(font_name):
        download_file("https://github.com/google/fonts/raw/main/ofl/lora/static/Lora-Bold.ttf", font_name, "Font")
    return font_name if os.path.exists(font_name) else None

@app.post("/merge")
def merge_video_audio(request: MergeRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_v.mp4"
    input_audio = f"{req_id}_a.mp3"
    output_file = f"{req_id}_out.mp4"
    subtitle_file = f"{req_id}.srt"
    
    files_to_clean = [input_video, input_audio, output_file, subtitle_file]

    try:
        # 1. Tải file
        download_file(request.video_url, input_video, "Video")
        download_file(request.audio_url, input_audio, "Audio")
        font_path = ensure_font_exists()

        # 2. Xử lý Subtitle
        has_sub = False
        if request.subtitle_content and len(request.subtitle_content.strip()) > 0:
            with open(subtitle_file, "w", encoding="utf-8") as f:
                f.write(request.subtitle_content)
            has_sub = True

        # 3. Xây dựng Filter
        filters = []
        filters.append(f"[0:v]format=yuv420p[v0]") 
        last_stream = "[v0]"
        
        # === PHẦN CHỈNH SỬA STYLE TEXT Ở ĐÂY ===
        if request.keyword:
            sanitized_text = request.keyword.replace(":", "\\:").replace("'", "")
            font_cmd = f"fontfile={font_path}:" if font_path else ""
            
            # Style mới: Chữ trắng (white), viền đen (bordercolor=black), dày 8px (borderw=8)
            styling = "fontcolor=white:bordercolor=black:borderw=8:fontsize=130"
            position = "x=(w-text_w)/2:y=(h-text_h)/2"
            
            draw_cmd = f"drawtext={font_cmd}text='{sanitized_text}':{styling}:{position}"
            filters.append(f"{last_stream}{draw_cmd}[v1]")
            last_stream = "[v1]"
        # =======================================

        if has_sub:
            # Style cho Subtitle (giữ nguyên màu vàng viền đen cho dễ đọc bên dưới)
            style = "FontName=Lora-Bold,FontSize=24,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,MarginV=30,Alignment=2"
            sub_cmd = f"subtitles={subtitle_file}:fontsdir=.:force_style='{style}'"
            filters.append(f"{last_stream}{sub_cmd}[v2]")
            last_stream = "[v2]"

        filter_complex = ";".join(filters)
        
        cmd = [
            "ffmpeg",
            "-stream_loop", "-1",
            "-i", input_video,
            "-i", input_audio,
            "-filter_complex", filter_complex,
            "-map", last_stream,
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "aac",
            "-shortest",
            "-y",
            output_file
        ]

        subprocess.run(cmd, check=True)
        
        background_tasks.add_task(cleanup_files, files_to_clean)
        return FileResponse(path=output_file, media_type='video/mp4', filename="final_video_styled.mp4")

    except subprocess.CalledProcessError as e:
        cleanup_files(files_to_clean)
        raise HTTPException(status_code=500, detail=f"Lỗi FFmpeg Render: {str(e)}")
    except Exception as e:
        cleanup_files(files_to_clean)
        raise e
