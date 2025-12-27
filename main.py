import subprocess
import uuid
import os
import shutil
import requests
import gc
import json
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from PIL import Image, ImageDraw, ImageFont, features

# Tắt cảnh báo SSL
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI()

# ==========================================
# 1. CẤU HÌNH FONT & HỆ THỐNG
# ==========================================
FONT_DIR = "/app/fonts"
if not os.path.exists(FONT_DIR): os.makedirs(FONT_DIR, exist_ok=True)

FONT_BOLD_PATH = os.path.join(FONT_DIR, "Lora-Bold.ttf")
FONT_REG_PATH = os.path.join(FONT_DIR, "Lora-Regular.ttf")

# LINK TẢI FONT CHUẨN TỪ REPO TÁC GIẢ (CYREAL)
URL_BOLD = "https://github.com/cyrealtype/Lora-Cyrillic/raw/main/fonts/ttf/Lora-Bold.ttf"
URL_REG = "https://github.com/cyrealtype/Lora-Cyrillic/raw/main/fonts/ttf/Lora-Regular.ttf"

def download_font_force():
    """Tải font về hệ thống (Force Download nếu file < 10KB)"""
    print("--- CHECKING FONTS ---")
    try:
        # Tải Bold
        if not os.path.exists(FONT_BOLD_PATH) or os.path.getsize(FONT_BOLD_PATH) < 10000:
            print(f"⬇️ Đang tải Lora-Bold từ Cyreal...")
            r = requests.get(URL_BOLD, timeout=30)
            if r.status_code == 200:
                with open(FONT_BOLD_PATH, 'wb') as f: f.write(r.content)
            else:
                print(f"❌ Lỗi HTTP {r.status_code} khi tải Bold")
        
        # Tải Regular
        if not os.path.exists(FONT_REG_PATH) or os.path.getsize(FONT_REG_PATH) < 10000:
            print(f"⬇️ Đang tải Lora-Regular từ Cyreal...")
            r = requests.get(URL_REG, timeout=30)
            if r.status_code == 200:
                with open(FONT_REG_PATH, 'wb') as f: f.write(r.content)
            else:
                print(f"❌ Lỗi HTTP {r.status_code} khi tải Regular")
            
        print("✅ Fonts Check Done!")
    except Exception as e:
        print(f"❌ Lỗi tải font: {e}")

@app.on_event("startup")
async def startup_check():
    # Check thư viện FreeType
    has_freetype = features.check('freetype2')
    print(f"🖥️ FREETYPE SUPPORT: {has_freetype}")
    if not has_freetype:
        print("⚠️ CẢNH BÁO: Server chưa cài thư viện Font! Hãy tạo file nixpacks.toml!")
    
    download_font_force()

# ==========================================
# 2. MODELS & HELPERS
# ==========================================
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

def download_file(url, filename):
    if not url: return False
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        with requests.get(url, headers=headers, stream=True, verify=False, timeout=60) as r:
            if r.status_code != 200: return False
            with open(filename, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        if os.path.exists(filename) and os.path.getsize(filename) > 1000: return True
    except: pass
    return False

def get_video_dimensions(filepath):
    """Lấy kích thước video gốc"""
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", filepath]
        result = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(result.stdout)
        return data['streams'][0]['width'], data['streams'][0]['height']
    except:
        return 1080, 1920

# ==========================================
# 3. DRAWING LOGIC (Dynamic Resolution)
# ==========================================
def get_font_objects(size_header, size_body):
    try:
        font_header = ImageFont.truetype(FONT_BOLD_PATH, size_header)
        font_body_bold = ImageFont.truetype(FONT_BOLD_PATH, size_body)
        font_body_reg = ImageFont.truetype(FONT_REG_PATH, size_body)
        return font_header, font_body_bold, font_body_reg
    except:
        # Nếu lỗi thì dùng Default (chấp nhận xấu còn hơn crash)
        return ImageFont.load_default(), ImageFont.load_default(), ImageFont.load_default()

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

def create_list_overlay(header, content, output_img_path, target_w, target_h):
    img = Image.new('RGBA', (target_w, target_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Dynamic Size: Header 8%, Body 5.5% chiều rộng video
    FONT_SIZE_HEADER = int(target_w * 0.08)
    FONT_SIZE_BODY = int(target_w * 0.055)
    
    font_header, font_body_bold, font_body_reg = get_font_objects(FONT_SIZE_HEADER, FONT_SIZE_BODY)

    clean_header = header.replace("\\n", "\n").replace("\\N", "\n")
    clean_content = content.replace("\\n", "\n").replace("\\N", "\n")

    box_width = int(target_w * 0.9)
    padding_x = int(target_w * 0.04)
    max_text_width = box_width - (padding_x * 2)

    import textwrap
    header_lines = []
    # Ước lượng số ký tự trên 1 dòng
    chars_header = int(max_text_width / (FONT_SIZE_HEADER * 0.5))
    for line in clean_header.split('\n'):
        header_lines.extend(textwrap.wrap(line.strip().upper(), width=chars_header))

    line_height_header = int(FONT_SIZE_HEADER * 1.25)
    line_height_body = int(FONT_SIZE_BODY * 1.4)
    spacing_header_body = int(target_h * 0.03) 
    padding_y = int(target_h * 0.04)
    
    h_header = len(header_lines) * line_height_header
    
    temp_y = 0
    body_items = clean_content.split('\n')
    dummy_draw = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    for item in body_items:
        if not item.strip(): continue
        temp_y = draw_highlighted_line(dummy_draw, 0, temp_y, item, font_body_bold, font_body_reg, max_text_width, line_height_body)
        temp_y += int(target_h * 0.015) 
    
    h_body = temp_y
    box_height = padding_y + h_header + spacing_header_body + h_body + padding_y
    box_x = (target_w - box_width) // 2
    box_y = (target_h - box_height) // 2
    
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], fill=(255, 255, 255, 245), outline=None)
    border_w = int(target_w * 0.005)
    if border_w < 1: border_w = 1
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], outline=(200, 200, 200, 150), width=border_w)

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
        current_y += int(target_h * 0.015) 

    img.save(output_img_path)

# ==========================================
# 4. API 1: /merge (BLOG VIDEO - PING PONG LOOP)
# ==========================================
@app.post("/merge")
def merge_video_audio(request: MergeRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_v.mp4"
    pingpong_video = f"{req_id}_pp.mp4"
    input_audio = f"{req_id}_a.mp3"
    output_file = f"{req_id}_out.mp4"
    clean_list = [input_video, pingpong_video, input_audio, output_file]

    try:
        download_file(request.video_url, input_video)
        download_file(request.audio_url, input_audio)
        
        final_input_video = input_video
        if request.ping_pong:
            try:
                subprocess.run([
                    "ffmpeg", "-threads", "2", "-y",
                    "-i", input_video, 
                    "-filter_complex", "[0:v]split[main][rev];[rev]reverse[r];[main][r]concat=n=2:v=1:a=0[v]", 
                    "-map", "[v]", 
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", 
                    pingpong_video
                ], check=True)
                final_input_video = pingpong_video
            except: pass 

        cmd = [
            "ffmpeg", "-threads", "4", "-y",
            "-stream_loop", "-1",       
            "-i", final_input_video,    
            "-i", input_audio,          
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "ultrafast", 
            "-c:a", "aac", 
            "-shortest",                
            output_file
        ]
        subprocess.run(cmd, check=True)
        
        background_tasks.add_task(cleanup_files, clean_list)
        return FileResponse(output_file, media_type='video/mp4', filename="blog_video.mp4")
    except Exception as e:
        cleanup_files(clean_list)
        raise HTTPException(status_code=400, detail=str(e))

# ==========================================
# 5. API 2: /shorts_list (SHORTS VIDEO - LIST)
# ==========================================
@app.post("/shorts_list")
def create_shorts_list(request: ShortsRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_bg.mp4"
    input_audio = f"{req_id}_a.mp3"
    overlay_img = f"{req_id}_over.png"
    output_file = f"{req_id}_short.mp4"
    clean_list = [input_video, input_audio, overlay_img, output_file]

    try:
        vid_ok = download_file(request.video_url, input_video)
        aud_ok = download_file(request.audio_url, input_audio)
        
        # 1. Lấy size video gốc (Để tính size chữ)
        target_w, target_h = 1080, 1920 
        if vid_ok:
            target_w, target_h = get_video_dimensions(input_video)
        
        # 2. Tạo Overlay (Kích thước = Video gốc)
        create_list_overlay(request.header_text, request.list_content, overlay_img, target_w, target_h)

        # 3. Ghép
        if vid_ok:
            subprocess.run([
                "ffmpeg", "-threads", "4", "-y",
                "-i", input_video,
                "-i", input_audio,
                "-i", overlay_img,
                "-filter_complex", "[0:v][2:v]overlay=0:0[v]",
                "-map", "[v]", "-map", "1:a",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                "-t", str(request.duration),
                output_file
            ], check=True)
        else:
            # Fallback nền đen
            subprocess.run([
                "ffmpeg", "-loop", "1", "-y",
                "-i", overlay_img,
                "-i", input_audio,
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                "-c:a", "aac", 
                "-t", str(request.duration), 
                "-shortest", 
                output_file
            ], check=True)

        background_tasks.add_task(cleanup_files, clean_list)
        return FileResponse(output_file, media_type='video/mp4', filename="list_short.mp4")
    except Exception as e:
        cleanup_files(clean_list)
        raise HTTPException(status_code=400, detail=str(e))
