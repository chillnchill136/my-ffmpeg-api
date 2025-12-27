import subprocess
import uuid
import os
import glob
import shutil
import urllib3
import textwrap
import gc
import json
import requests
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

# Tắt cảnh báo SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI()

# === CẤU HÌNH FONT (QUAN TRỌNG: LẤY ĐƯỜNG DẪN TUYỆT ĐỐI) ===
# Trên Railway/Docker, thư mục hiện tại thường là /app
CURRENT_DIR = os.getcwd() 
FONT_BOLD_NAME = "Lora-Bold.ttf"
FONT_REG_NAME = "Lora-Regular.ttf"

# Đường dẫn tuyệt đối (Ví dụ: /app/Lora-Bold.ttf)
ABS_PATH_BOLD = os.path.join(CURRENT_DIR, FONT_BOLD_NAME)
ABS_PATH_REG = os.path.join(CURRENT_DIR, FONT_REG_NAME)

@app.on_event("startup")
async def startup_check():
    print(f"--- THƯ MỤC HIỆN TẠI: {CURRENT_DIR} ---")
    print(f"--- ĐANG TÌM FONT TẠI: {ABS_PATH_BOLD} ---")
    
    if os.path.exists(ABS_PATH_BOLD):
        print(f"✅ ĐÃ TÌM THẤY FONT BOLD: {os.path.getsize(ABS_PATH_BOLD)} bytes")
    else:
        print(f"❌ KHÔNG THẤY FONT BOLD TẠI {ABS_PATH_BOLD}")
        # Liệt kê file để debug
        print(f"Danh sách file: {os.listdir(CURRENT_DIR)}")

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

def cleanup_files(files):
    for f in files:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass
    gc.collect() 

def system_download(url, filename):
    try:
        # Giả lập Chrome để tải file (đặc biệt là Airtable)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        with requests.get(url, headers=headers, stream=True, verify=False, timeout=60) as r:
            if r.status_code != 200: return False
            with open(filename, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        if os.path.exists(filename) and os.path.getsize(filename) > 1000: return True
    except: pass
    return False

def download_file_req(url, filename):
    if not url: return False
    return system_download(url, filename)

def get_font_objects(size_header, size_body):
    """Load font từ đường dẫn tuyệt đối"""
    try:
        # Nếu tìm thấy file Lora Bold thì dùng, không thì fallback
        if os.path.exists(ABS_PATH_BOLD):
            font_header = ImageFont.truetype(ABS_PATH_BOLD, size_header)
            font_body_bold = ImageFont.truetype(ABS_PATH_BOLD, size_body)
            # Check Regular
            path_reg = ABS_PATH_REG if os.path.exists(ABS_PATH_REG) else ABS_PATH_BOLD
            font_body_reg = ImageFont.truetype(path_reg, size_body)
            return font_header, font_body_bold, font_body_reg
        else:
            print("⚠️ Không thấy font Lora, dùng Default")
            return ImageFont.load_default(), ImageFont.load_default(), ImageFont.load_default()
    except Exception as e:
        print(f"⚠️ Lỗi load font: {e}")
        return ImageFont.load_default(), ImageFont.load_default(), ImageFont.load_default()

def draw_highlighted_line(draw, x_start, y_start, text, font_bold, font_reg, max_width, line_height):
    COLOR_HIGHLIGHT = (204, 0, 0, 255) # Đỏ đậm
    COLOR_NORMAL = (0, 0, 0, 255)      # Đen
    
    if ":" in text:
        parts = text.split(":", 1)
        part_bold = parts[0] + ":"
        part_reg = parts[1]
    else:
        part_bold = ""
        part_reg = text

    current_x = x_start
    current_y = y_start
    
    # Vẽ phần Bold
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

    # Vẽ phần Regular
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

def create_list_overlay(header, content, output_img_path):
    # Canvas 540x960 (9:16)
    W, H = 540, 960 
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # === CẤU HÌNH SIZE CHỮ TO (X2 so với cũ) ===
    # Lúc trước 26 bé quá, giờ tăng lên 40 cho body
    FONT_SIZE_HEADER = 50 
    FONT_SIZE_BODY = 40   
    
    # Load Font Tuyệt đối
    font_header, font_body_bold, font_body_reg = get_font_objects(FONT_SIZE_HEADER, FONT_SIZE_BODY)

    clean_header = header.replace("\\n", "\n").replace("\\N", "\n")
    clean_content = content.replace("\\n", "\n").replace("\\N", "\n")

    # Tăng lề (padding) để chữ không sát mép
    box_width = 500
    padding_x = 20 
    max_text_width = box_width - (padding_x * 2)

    import textwrap
    header_lines = []
    # Wrap text chặt hơn vì chữ to ra
    for line in clean_header.split('\n'):
        header_lines.extend(textwrap.wrap(line.strip().upper(), width=15))

    line_height_header = int(FONT_SIZE_HEADER * 1.2)
    line_height_body = int(FONT_SIZE_BODY * 1.3)
    spacing_header_body = 30 
    padding_y = 30
    
    h_header = len(header_lines) * line_height_header
    
    # Tính chiều cao Body
    temp_y = 0
    body_items = clean_content.split('\n')
    dummy_draw = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    for item in body_items:
        if not item.strip(): continue
        temp_y = draw_highlighted_line(dummy_draw, 0, temp_y, item, font_body_bold, font_body_reg, max_text_width, line_height_body)
        temp_y += 15 
    
    h_body = temp_y
    box_height = padding_y + h_header + spacing_header_body + h_body + padding_y
    
    # Căn giữa Box
    box_x = (W - box_width) // 2
    box_y = (H - box_height) // 2
    
    # Vẽ nền trắng mờ
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], fill=(255, 255, 255, 245), outline=None)
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], outline=(200, 200, 200, 150), width=3)

    # Vẽ Header
    current_y = box_y + padding_y
    for line in header_lines:
        text_w = draw.textlength(line, font=font_header)
        text_x = box_x + (box_width - text_w) // 2 
        draw.text((text_x, current_y), line, font=font_header, fill=(204, 0, 0, 255))
        current_y += line_height_header

    # Vẽ Body
    current_y += spacing_header_body
    start_x = box_x + padding_x
    for item in body_items:
        if not item.strip(): continue
        current_y = draw_highlighted_line(draw, start_x, current_y, item, font_body_bold, font_body_reg, max_text_width, line_height_body)
        current_y += 15

    img.save(output_img_path)

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
        download_file_req(request.video_url, input_video)
        download_file_req(request.audio_url, input_audio)
        path_bold = ABS_PATH_BOLD if os.path.exists(ABS_PATH_BOLD) else "Arial"
        font_path = path_bold
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

@app.post("/shorts_list")
def create_shorts_list(request: ShortsRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_bg.mp4"
    input_audio = f"{req_id}_a.mp3"
    overlay_img = f"{req_id}_over.png"
    normalized_bg = f"{req_id}_norm.mp4"
    output_file = f"{req_id}_short.mp4"
    files_to_clean = [input_video, input_audio, overlay_img, normalized_bg, output_file]

    try:
        # Tải File (Airtable Safe)
        vid_ok = download_file_req(request.video_url, input_video)
        aud_ok = download_file_req(request.audio_url, input_audio)
        
        # Vẽ Overlay (Font Lora Absolute Path + Size To)
        create_list_overlay(request.header_text, request.list_content, overlay_img)

        # Xử lý Video Nền
        bg_ready = False
        if vid_ok:
            try:
                # Resize về 540p trước
                subprocess.run([
                    "ffmpeg", "-threads", "1", "-y", 
                    "-i", input_video, 
                    "-t", str(request.duration),
                    "-vf", "scale=540:-2", 
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-an", 
                    normalized_bg
                ], check=True)
                bg_ready = True
            except:
                print("⚠️ Lỗi resize -> Dùng nền đen")
        
        if not bg_ready:
            subprocess.run([
                "ffmpeg", "-f", "lavfi", "-i", f"color=c=black:s=540x960:r=30", 
                "-t", str(request.duration),
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-y", 
                normalized_bg
            ], check=True)

        # Ghép Final
        subprocess.run([
            "ffmpeg", "-threads", "4", 
            "-stream_loop", "-1", 
            "-i", normalized_bg, 
            "-i", input_audio, 
            "-i", overlay_img, 
            "-filter_complex", "[0:v][2:v]overlay=0:0[v]", 
            "-map", "[v]", "-map", "1:a", 
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", 
            "-t", str(request.duration), "-y", 
            output_file
        ], check=True)

        background_tasks.add_task(cleanup_files, files_to_clean)
        return FileResponse(output_file, media_type='video/mp4', filename="list_short.mp4")
    except Exception as e:
        cleanup_files(files_to_clean)
        raise HTTPException(status_code=400, detail=f"Lỗi: {str(e)}")
