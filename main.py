import subprocess
import uuid
import os
import requests
import shutil
import textwrap
import glob
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

app = FastAPI()

# === MODEL DỮ LIỆU ===
class MergeRequest(BaseModel):
    video_url: str = ""
    image_url: str = "" 
    audio_url: str
    keyword: Optional[str] = ""
    subtitle_content: Optional[str] = ""
    ping_pong: Optional[bool] = True

class ShortsRequest(BaseModel):
    video_url: str
    audio_url: str
    header_text: str = "TOP LIST" 
    list_content: str = ""        
    duration: int = 5             

# === HÀM BỔ TRỢ CHUNG ===
def cleanup_files(files):
    for f in files:
        if os.path.exists(f):
            try:
                os.remove(f)
            except:
                pass

def download_file(url, filename, file_type="File"):
    if not url: return False
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    try:
        print(f"Đang tải {file_type} từ: {url}")
        response = requests.get(url, headers=headers, stream=True, timeout=30)
        if response.status_code != 200:
            print(f"-> Thất bại: {response.status_code}")
            return False
        with open(filename, 'wb') as f:
            response.raw.decode_content = True
            shutil.copyfileobj(response.raw, f)
        print("-> Thành công!")
        return True
    except Exception as e:
        print(f"-> Lỗi: {e}")
        return False

# === CHIẾN LƯỢC TẢI FONT 3 LỚP ===
def get_valid_font_path():
    font_filename = "MyFont-Bold.ttf"
    if os.path.exists(font_filename) and os.path.getsize(font_filename) > 10000:
        return font_filename

    # Ưu tiên Roboto hoặc Arial vì nó gọn (Condensed), hợp với style liệt kê
    font_urls = [
        "https://github.com/google/fonts/raw/main/apache/roboto/static/Roboto-Bold.ttf",
        "https://github.com/matomo-org/travis-scripts/raw/master/fonts/Arial-Bold.ttf",
    ]

    print("Đang tìm tải Font...")
    for url in font_urls:
        if download_file(url, font_filename, "Font Candidate"):
            return font_filename
    
    # Fallback
    system_fonts = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)
    if system_fonts: return system_fonts[0]
    return None

# === HÀM VẼ ẢNH OVERLAY (V10 - STYLE CHUẨN) ===
def create_list_overlay(header, content, output_img_path):
    W, H = 1080, 1920
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    font_path = get_valid_font_path()
    if not font_path:
        raise Exception("Lỗi: Không tìm thấy Font chữ!")

    # CẤU HÌNH SIZE (Đã tinh chỉnh nhỏ hơn để vừa màn hình)
    FONT_SIZE_HEADER = 65  # Giảm từ 90 -> 65
    FONT_SIZE_BODY = 45    # Giảm từ 60 -> 45
    
    try:
        font_header = ImageFont.truetype(font_path, FONT_SIZE_HEADER)
        font_body = ImageFont.truetype(font_path, FONT_SIZE_BODY)
    except:
        font_header = ImageFont.load_default()
        font_body = ImageFont.load_default()

    # Cấu hình Box
    box_width = 920 
    padding_x = 50 # Lề trái phải trong box
    padding_y = 50 # Lề trên dưới trong box
    
    # 1. Xử lý Header
    # Tăng width wrap lên vì font nhỏ hơn (22 ký tự/dòng)
    header_lines = textwrap.wrap(header.upper(), width=22) 
    
    # 2. Xử lý Body
    raw_lines = content.split('\n')
    body_lines = []
    for line in raw_lines:
        # Wrap body dài hơn (38 ký tự/dòng)
        wrapped = textwrap.wrap(line, width=38)
        body_lines.extend(wrapped)

    # 3. Tính chiều cao Box
    line_height_header = int(FONT_SIZE_HEADER * 1.3) # Giãn dòng 1.3
    line_height_body = int(FONT_SIZE_BODY * 1.4)     # Giãn dòng 1.4 cho dễ đọc
    spacing_header_body = 40 
    
    total_header_height = len(header_lines) * line_height_header
    total_body_height = len(body_lines) * line_height_body
    
    box_height = padding_y + total_header_height + spacing_header_body + total_body_height + padding_y
    
    # Giới hạn chiều cao Box không quá 80% màn hình (tránh tràn)
    max_height = int(H * 0.85)
    if box_height > max_height:
        print("Cảnh báo: Nội dung quá dài, có thể bị cắt bớt!")
        # Ở đây có thể scale font nhỏ hơn nữa nếu muốn, nhưng tạm thời giữ nguyên
    
    # Tọa độ
    box_x = (W - box_width) // 2
    box_y = (H - box_height) // 2
    
    # 4. Vẽ Box Trắng Mờ (Opacity 240/255 ~ 94%)
    # Giả lập bo góc bằng cách vẽ rectangle thường (Pillow vẽ bo góc hơi phức tạp)
    draw.rectangle(
        [(box_x, box_y), (box_x + box_width, box_y + box_height)],
        fill=(255, 255, 255, 240), 
        outline=None
    )
    # Viền box nhạt (tùy chọn)
    draw.rectangle(
        [(box_x, box_y), (box_x + box_width, box_y + box_height)],
        outline=(200, 200, 200, 100),
        width=2
    )

    # 5. Vẽ Header (Màu Đỏ Đậm: #CC0000)
    current_y = box_y + padding_y
    for line in header_lines:
        text_w = draw.textlength(line, font=font_header)
        text_x = box_x + (box_width - text_w) // 2 # Căn giữa
        draw.text((text_x, current_y), line, font=font_header, fill=(204, 0, 0, 255))
        current_y += line_height_header

    # 6. Vẽ Body (Màu Đen: #000000)
    current_y += spacing_header_body
    for line in body_lines:
        # Body căn trái, lùi vào so với mép box
        draw.text(
            (box_x + padding_x, current_y), 
            line, 
            font=font_body, 
            fill=(0, 0, 0, 255)
        )
        current_y += line_height_body

    img.save(output_img_path)

# ==========================================
# 1. API: SHORT VIDEO (PING-PONG)
# ==========================================
@app.post("/merge")
def merge_video_audio(request: MergeRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_v.mp4"
    pingpong_video = f"{req_id}_pp.mp4"
    input_audio = f"{req_id}_a.mp3"
    output_file = f"{req_id}_out.mp4"
    subtitle_file = f"{req_id}.srt"
    files_to_clean = [input_video, pingpong_video, input_audio, output_file, subtitle_file]

    try:
        download_file(request.video_url, input_video, "Video")
        download_file(request.audio_url, input_audio, "Audio")
        font_path = get_valid_font_path()

        final_input_video = input_video
        if request.ping_pong:
            try:
                subprocess.run([
                    "ffmpeg", "-threads", "1", "-i", input_video,
                    "-filter_complex", "[0:v]split[main][rev];[rev]reverse[r];[main][r]concat=n=2:v=1:a=0[v]",
                    "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-y", pingpong_video
                ], check=True)
                final_input_video = pingpong_video
            except: pass

        has_sub = False
        if request.subtitle_content and len(request.subtitle_content.strip()) > 0:
            with open(subtitle_file, "w", encoding="utf-8") as f:
                f.write(request.subtitle_content)
            has_sub = True

        filters = [f"[0:v]format=yuv420p[v0]"]
        last_stream = "[v0]"
        
        if request.keyword:
            sanitized_text = request.keyword.replace(":", "\\:").replace("'", "")
            font_cmd = f"fontfile={font_path}:" if font_path else ""
            styling = "fontcolor=white:bordercolor=black:borderw=7:fontsize=130"
            filters.append(f"{last_stream}drawtext={font_cmd}text='{sanitized_text}':{styling}:x=(w-text_w)/2:y=(h-text_h)/2[v1]")
            last_stream = "[v1]"

        if has_sub:
            font_arg = font_path if font_path else "Arial"
            style = f"FontName={font_arg},FontSize=24,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,MarginV=30,Alignment=2"
            filters.append(f"{last_stream}subtitles={subtitle_file}:fontsdir=.:force_style='{style}'[v2]")
            last_stream = "[v2]"

        cmd = [
            "ffmpeg", "-threads", "1", "-stream_loop", "-1", "-i", final_input_video, "-i", input_audio,
            "-filter_complex", ";".join(filters), "-map", last_stream, "-map", "1:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", "-y", output_file
        ]
        subprocess.run(cmd, check=True)
        background_tasks.add_task(cleanup_files, files_to_clean)
        return FileResponse(output_file, media_type='video/mp4', filename="short.mp4")
    except Exception as e:
        cleanup_files(files_to_clean)
        raise HTTPException(status_code=400, detail=str(e))

# ==========================================
# 2. API: PODCAST (STATIC IMAGE)
# ==========================================
@app.post("/podcast")
def create_podcast(request: MergeRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_image = f"{req_id}_img.jpg"
    input_audio = f"{req_id}_a.mp3"
    output_file = f"{req_id}_podcast.mp4"
    subtitle_file = f"{req_id}.srt"
    files_to_clean = [input_image, input_audio, output_file, subtitle_file]

    try:
        download_file(request.image_url, input_image, "Thumbnail")
        download_file(request.audio_url, input_audio, "Audio")
        font_path = get_valid_font_path()

        has_sub = False
        if request.subtitle_content and len(request.subtitle_content.strip()) > 0:
            with open(subtitle_file, "w", encoding="utf-8") as f:
                f.write(request.subtitle_content)
            has_sub = True

        cmd = ["ffmpeg", "-threads", "1", "-loop", "1", "-i", input_image, "-i", input_audio]
        if has_sub:
            font_name_sub = font_path if font_path else "Arial"
            style = f"FontName={font_name_sub},FontSize=18,PrimaryColour=&H00FFFFFF,BorderStyle=1,Outline=2,MarginV=50,Alignment=2"
            cmd.extend(["-vf", f"subtitles={subtitle_file}:fontsdir=.:force_style='{style}'", "-tune", "stillimage", "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p"])
        else:
            cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage", "-c:a", "aac", "-pix_fmt", "yuv420p"])

        cmd.extend(["-shortest", "-y", output_file])
        subprocess.run(cmd, check=True)
        background_tasks.add_task(cleanup_files, files_to_clean)
        return FileResponse(output_file, media_type='video/mp4', filename="podcast.mp4")
    except Exception as e:
        cleanup_files(files_to_clean)
        raise HTTPException(status_code=400, detail=str(e))

# ==========================================
# 3. API: SHORTS LIST (5S) - RAM OPTIMIZED + FIX STYLE
# ==========================================
@app.post("/shorts_list")
def create_shorts_list(request: ShortsRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_bg.mp4"
    input_audio = f"{req_id}_a.mp3"
    overlay_img = f"{req_id}_over.png"
    output_file = f"{req_id}_short.mp4"
    files_to_clean = [input_video, input_audio, overlay_img, output_file]

    try:
        download_file(request.video_url, input_video, "BG Video")
        download_file(request.audio_url, input_audio, "Audio")
        
        create_list_overlay(request.header_text, request.list_content, overlay_img)

        cmd = [
            "ffmpeg",
            "-threads", "1",
            "-stream_loop", "-1",
            "-i", input_video,
            "-i", input_audio,
            "-i", overlay_img,
            "-filter_complex", 
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920:(iw-ow)/2:(ih-oh)/2[bg];[bg][2:v]overlay=0:0[v]",
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", 
            "-preset", "ultrafast",
            "-crf", "28",
            "-c:a", "aac",
            "-t", str(request.duration),
            "-y",
            output_file
        ]
        subprocess.run(cmd, check=True)
        background_tasks.add_task(cleanup_files, files_to_clean)
        return FileResponse(output_file, media_type='video/mp4', filename="list_short.mp4")
    except Exception as e:
        cleanup_files(files_to_clean)
        raise HTTPException(status_code=400, detail=str(e))
