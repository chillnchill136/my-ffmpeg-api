import subprocess
import uuid
import os
import glob
import shutil
import urllib3
import textwrap
import gc
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

# Tắt cảnh báo SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI()

# === CẤU HÌNH FONT & CDN ===
FONT_BOLD = "Lora-Bold.ttf"
FONT_REG = "Lora-Regular.ttf"
CDN_BOLD = "https://cdn.jsdelivr.net/gh/google/fonts/ofl/lora/static/Lora-Bold.ttf"
CDN_REG = "https://cdn.jsdelivr.net/gh/google/fonts/ofl/lora/static/Lora-Regular.ttf"

# === HÀM TẢI HỆ THỐNG ===
def system_download(url, filename):
    try:
        # Timeout 45s
        subprocess.run(["curl", "-L", "-k", "-o", filename, url], check=True, timeout=45)
        if os.path.exists(filename) and os.path.getsize(filename) > 5000:
            return True
    except: pass
    return False

# === STARTUP CHECK (Tải Font Tự Động) ===
@app.on_event("startup")
async def startup_check():
    # Đảm bảo có font Lora
    if not os.path.exists(FONT_BOLD): system_download(CDN_BOLD, FONT_BOLD)
    if not os.path.exists(FONT_REG): system_download(CDN_REG, FONT_REG)

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

# === HELPERS ===
def cleanup_files(files):
    for f in files:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass
    gc.collect() 

def download_file_req(url, filename):
    if not url: return False
    return system_download(url, filename)

def get_ready_font():
    if os.path.exists(FONT_BOLD): 
        return FONT_BOLD, FONT_REG if os.path.exists(FONT_REG) else FONT_BOLD
    return None, None

# === LOGIC VẼ TEXT (Giữ nguyên style Highlight) ===
def draw_highlighted_line(draw, x_start, y_start, text, font_bold, font_reg, max_width, line_height):
    COLOR_HIGHLIGHT = (204, 0, 0, 255) 
    COLOR_NORMAL = (0, 0, 0, 255)      

    if ":" in text:
        parts = text.split(":", 1)
        part_bold = parts[0] + ":"
        part_reg = parts[1]
    else:
        part_bold = ""
        part_reg = text

    current_x = x_start
    current_y = y_start
    
    if part_bold:
        words = part_bold.split()
        for i, word in enumerate(words):
            suffix = " " if i < len(words) else "" 
            word_w = draw.textlength(word + suffix, font=font_bold)
            if current_x + word_w > x_start + max_width:
                current_x = x_start
                current_y += line_height
            draw.text((current_x, current_y), word, font=font_bold, fill=COLOR_HIGHLIGHT)
            current_x += word_w

    if part_reg:
        words = part_reg.split()
        if part_bold and current_x > x_start:
             space_w = draw.textlength(" ", font=font_reg)
             current_x += space_w
        for i, word in enumerate(words):
            word_w = draw.textlength(word, font=font_reg)
            space_w = draw.textlength(" ", font=font_reg)
            if current_x + word_w > x_start + max_width:
                current_x = x_start
                current_y += line_height
                draw.text((current_x, current_y), word, font=font_reg, fill=COLOR_NORMAL)
                current_x += word_w
            else:
                draw.text((current_x, current_y), word, font=font_reg, fill=COLOR_NORMAL)
                current_x += word_w
            if i < len(words) - 1: current_x += space_w

    return current_y + line_height

# === VẼ OVERLAY 540P ===
def create_list_overlay(header, content, output_img_path):
    # Setup Canvas 540x960 (Nhẹ)
    W, H = 540, 960 
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    path_bold, path_reg = get_ready_font()
    
    # Font size cho 540p
    FONT_SIZE_HEADER = 38
    FONT_SIZE_BODY = 26
    
    try:
        if path_bold:
            font_header = ImageFont.truetype(path_bold, FONT_SIZE_HEADER)
            font_body_bold = ImageFont.truetype(path_bold, FONT_SIZE_BODY)
            font_body_reg = ImageFont.truetype(path_reg, FONT_SIZE_BODY)
        else:
            font_header = ImageFont.load_default()
            font_body_bold = ImageFont.load_default()
            font_body_reg = ImageFont.load_default()
    except:
        font_header = ImageFont.load_default()
        font_body_bold = ImageFont.load_default()
        font_body_reg = ImageFont.load_default()

    clean_header = header.replace("\\n", "\n").replace("\\N", "\n")
    clean_content = content.replace("\\n", "\n").replace("\\N", "\n")

    box_width = 480
    padding_x = 30 
    max_text_width = box_width - (padding_x * 2)

    header_lines = []
    for line in clean_header.split('\n'):
        header_lines.extend(textwrap.wrap(line.strip().upper(), width=22))

    line_height_header = int(FONT_SIZE_HEADER * 1.2)
    line_height_body = int(FONT_SIZE_BODY * 1.4)
    spacing_header_body = 25 
    padding_y = 30
    
    h_header = len(header_lines) * line_height_header
    
    temp_y = 0
    body_items = clean_content.split('\n')
    dummy_draw = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    for item in body_items:
        if not item.strip(): continue
        temp_y = draw_highlighted_line(dummy_draw, 0, temp_y, item, font_body_bold, font_body_reg, max_text_width, line_height_body)
        temp_y += 8 
    
    h_body = temp_y
    box_height = padding_y + h_header + spacing_header_body + h_body + padding_y
    
    box_x = (W - box_width) // 2
    box_y = (H - box_height) // 2
    
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], fill=(255, 255, 255, 245), outline=None)
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], outline=(200, 200, 200, 150), width=2)

    current_y = box_y + padding_y
    for line in header_lines:
        text_w = draw.textlength(line, font=font_header)
        text_x = box_x + (box_width - text_w) // 2 
        draw.text((text_x, current_y), line, font=font_header, fill=(204, 0, 0, 255))
        current_y += line_height_header

    current_y += spacing_header_body
    start_x = box_x + padding_x
    for item in body_items:
        if not item.strip(): continue
        current_y = draw_highlighted_line(draw, start_x, current_y, item, font_body_bold, font_body_reg, max_text_width, line_height_body)
        current_y += 8

    img.save(output_img_path)

# ==========================================
# CÁC API KHÁC
# ==========================================
@app.post("/merge")
def merge_video_audio(request: MergeRequest, background_tasks: BackgroundTasks):
    # GIỮ NGUYÊN CODE CŨ
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_v.mp4"
    pingpong_video = f"{req_id}_pp.mp4"
    input_audio = f"{req_id}_a.mp3"
    output_file = f"{req_id}_out.mp4"
    subtitle_file = f"{req_id}.srt"
    files_to_clean = [input_video, pingpong_video, input_audio, output_file, subtitle_file]
    try:
        download_file_req(request.video_url, input_video)
        download_file_req(request.audio_url, input_audio)
        path_bold, _ = get_ready_font() 
        font_path = path_bold if path_bold else "Arial"
        final_input_video = input_video
        if request.ping_pong:
            try: subprocess.run(["ffmpeg", "-threads", "1", "-i", input_video, "-filter_complex", "[0:v]split[main][rev];[rev]reverse[r];[main][r]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-y", pingpong_video], check=True)
            except: pass
            final_input_video = pingpong_video
        
        has_sub = False
        if request.subtitle_content and len(request.subtitle_content.strip()) > 0:
            with open(subtitle_file, "w", encoding="utf-8") as f: f.write(request.subtitle_content)
            has_sub = True
        
        cmd = ["ffmpeg", "-threads", "1", "-stream_loop", "-1", "-i", final_input_video, "-i", input_audio, "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", "-y", output_file]
        subprocess.run(cmd, check=True)
        background_tasks.add_task(cleanup_files, files_to_clean)
        return FileResponse(output_file, media_type='video/mp4', filename="short.mp4")
    except Exception as e:
        cleanup_files(files_to_clean)
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/podcast")
def create_podcast(request: MergeRequest, background_tasks: BackgroundTasks):
    return HTTPException(status_code=200, detail="OK")

# ==========================================
# API: SHORTS LIST (V26 - DIRECT OVERLAY)
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
        download_file_req(request.video_url, input_video)
        download_file_req(request.audio_url, input_audio)
        
        # 1. Tạo Overlay (Size 540x960)
        create_list_overlay(request.header_text, request.list_content, overlay_img)

        # 2. Render KHÔNG RESIZE (Để tránh sập RAM)
        # Giả định: Video đầu vào ĐÃ được resize về 540x960 (hoặc 720x1280) ở bên ngoài.
        # Lệnh này chỉ đơn giản là Dán đè lên.
        
        cmd = [
            "ffmpeg",
            "-threads", "1",
            "-stream_loop", "-1",
            "-i", input_video,      # Input đã nhẹ
            "-i", input_audio,
            "-i", overlay_img,
            "-filter_complex", 
            f"[0:v][2:v]overlay=0:0[v]", # Bỏ scale, bỏ crop. Chỉ overlay thôi.
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", 
            "-preset", "ultrafast",
            "-crf", "30",
            "-max_muxing_queue_size", "1024",
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
        print(f"LỖI: {e}")
        raise HTTPException(status_code=400, detail=str(e))
