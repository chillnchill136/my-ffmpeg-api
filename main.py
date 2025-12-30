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

# Link Google Font Chính Chủ (Cyreal Repo)
URL_BOLD = "https://github.com/cyrealtype/Lora-Cyrillic/raw/main/fonts/ttf/Lora-Bold.ttf"
URL_REG = "https://github.com/cyrealtype/Lora-Cyrillic/raw/main/fonts/ttf/Lora-Regular.ttf"

def download_font_force():
    try:
        if not os.path.exists(FONT_BOLD_PATH) or os.path.getsize(FONT_BOLD_PATH) < 10000:
            print(f"⬇️ Đang tải Lora-Bold...")
            r = requests.get(URL_BOLD, timeout=30)
            if r.status_code == 200:
                with open(FONT_BOLD_PATH, 'wb') as f: f.write(r.content)
        
        if not os.path.exists(FONT_REG_PATH) or os.path.getsize(FONT_REG_PATH) < 10000:
            print(f"⬇️ Đang tải Lora-Regular...")
            r = requests.get(URL_REG, timeout=30)
            if r.status_code == 200:
                with open(FONT_REG_PATH, 'wb') as f: f.write(r.content)
    except Exception as e:
        print(f"❌ Lỗi tải font: {e}")

@app.on_event("startup")
async def startup_check():
    has_freetype = features.check('freetype2')
    print(f"🖥️ FREETYPE SUPPORT: {has_freetype}")
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
    duration: int = 0 

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
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", filepath]
        result = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(result.stdout)
        return data['streams'][0]['width'], data['streams'][0]['height']
    except:
        return 1080, 1920

# ==========================================
# 3. DRAWING LOGIC (Watermark Added Here)
# ==========================================
def get_font_objects(size_header, size_body):
    try:
        font_header = ImageFont.truetype(FONT_BOLD_PATH, size_header)
        font_body_bold = ImageFont.truetype(FONT_BOLD_PATH, size_body)
        font_body_reg = ImageFont.truetype(FONT_REG_PATH, size_body)
        return font_header, font_body_bold, font_body_reg
    except:
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
    
    # Fonts
    FONT_SIZE_HEADER = int(target_w * 0.07)
    FONT_SIZE_BODY = int(target_w * 0.05)
    font_header, font_body_bold, font_body_reg = get_font_objects(FONT_SIZE_HEADER, FONT_SIZE_BODY)

    # Box Config
    box_width = int(target_w * 0.88)
    padding_x = int(target_w * 0.06)
    max_text_width = box_width - (padding_x * 2)

    # Clean Text
    clean_header = header.replace("\\n", "\n").replace("\\N", "\n")
    clean_content = content.replace("\\n", "\n").replace("\\N", "\n")

    # Measure Header
    import textwrap
    header_lines = []
    avg_char_width = FONT_SIZE_HEADER * 0.65
    chars_per_line = int(max_text_width / avg_char_width)
    for line in clean_header.split('\n'):
        header_lines.extend(textwrap.wrap(line.strip().upper(), width=chars_per_line))

    # Dimensions
    line_height_header = int(FONT_SIZE_HEADER * 1.3)
    line_height_body = int(FONT_SIZE_BODY * 1.5)
    spacing_header_body = int(target_h * 0.035) 
    padding_y = int(target_h * 0.04)
    h_header = len(header_lines) * line_height_header
    
    # Measure Body
    temp_y = 0
    body_items = clean_content.split('\n')
    dummy_draw = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    for item in body_items:
        if not item.strip(): continue
        temp_y = draw_highlighted_line(dummy_draw, 0, temp_y, item, font_body_bold, font_body_reg, max_text_width, line_height_body)
        temp_y += int(target_h * 0.015) 
    h_body = temp_y
    
    box_height = padding_y + h_header + spacing_header_body + h_body + padding_y
    
    # Box Position
    box_x = (target_w - box_width) // 2
    box_y = (target_h - box_height) // 2
    
    # Draw Box
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], fill=(255, 255, 255, 245), outline=None)
    border_w = int(target_w * 0.006)
    if border_w < 2: border_w = 2
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], outline=(200, 200, 200, 150), width=border_w)

    # Draw Header
    current_y = box_y + padding_y
    for line in header_lines:
        text_w = draw.textlength(line, font=font_header)
        text_x = box_x + (box_width - text_w) // 2 
        draw.text((text_x, current_y), line, font=font_header, fill=(204, 0, 0, 255))
        current_y += line_height_header

    # Draw Body
    current_y += spacing_header_body
    start_x = box_x + padding_x
    for item in body_items:
        if not item.strip(): continue
        current_y = draw_highlighted_line(draw, start_x, current_y, item, font_body_bold, font_body_reg, max_text_width, line_height_body)
        current_y += int(target_h * 0.015) 

    # --- WATERMARK LOGIC (NEW) ---
    try:
        wm_text = "luangiai.vn"
        # Size bé hơn body một chút (4% chiều rộng)
        wm_size = int(target_w * 0.04) 
        font_wm = ImageFont.truetype(FONT_BOLD_PATH, wm_size)
        
        # Đo chiều rộng chữ
        wm_w = draw.textlength(wm_text, font=font_wm)
        
        # Căn giữa theo chiều ngang
        wm_x = (target_w - wm_w) // 2
        
        # Căn dưới đáy, cách đáy 5% chiều cao
        wm_y = target_h - int(target_h * 0.05) - wm_size
        
        # Màu xám nhạt (220, 220, 220), độ trong suốt Alpha = 80 (khoảng 30%)
        draw.text((wm_x, wm_y), wm_text, font=font_wm, fill=(220, 220, 220, 80))
    except: pass

    img.save(output_img_path)

# ==========================================
# 4. API 1: /merge (BLOG VIDEO)
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
# 5. API 2: /shorts_list (SHORTS VIDEO)
# ==========================================
@app.post("/shorts_list")
def create_shorts_list(request: ShortsRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_bg.mp4"
    processed_bg = f"{req_id}_bg_processed.mp4" 
    input_audio = f"{req_id}_a.mp3"
    overlay_img = f"{req_id}_over.png"
    output_file = f"{req_id}_short.mp4"
    clean_list = [input_video, processed_bg, input_audio, overlay_img, output_file]

    try:
        vid_ok = download_file(request.video_url, input_video)
        aud_ok = download_file(request.audio_url, input_audio)
        
        target_w, target_h = 1080, 1920 
        if vid_ok:
            target_w, target_h = get_video_dimensions(input_video)
        
        # Tạo Overlay có Watermark
        create_list_overlay(request.header_text, request.list_content, overlay_img, target_w, target_h)

        bg_ready = False
        if vid_ok:
            try:
                subprocess.run([
                    "ffmpeg", "-threads", "2", "-y",
                    "-i", input_video,
                    "-filter_complex", 
                    f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h}[scaled];[scaled]split[main][rev];[rev]reverse[r];[main][r]concat=n=2:v=1:a=0[v]",
                    "-map", "[v]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                    processed_bg
                ], check=True)
                bg_ready = True
            except: pass

        if bg_ready:
            subprocess.run([
                "ffmpeg", "-threads", "4", "-y",
                "-stream_loop", "-1",       
                "-i", processed_bg,         
                "-i", input_audio,          
                "-i", overlay_img,          
                "-filter_complex", "[0:v][2:v]overlay=0:0[v]",
                "-map", "[v]", "-map", "1:a",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                "-shortest",                
                output_file
            ], check=True)
        else:
            subprocess.run([
                "ffmpeg", "-loop", "1", "-y",
                "-i", overlay_img,
                "-i", input_audio,
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-shortest",
                output_file
            ], check=True)

        background_tasks.add_task(cleanup_files, clean_list)
        return FileResponse(output_file, media_type='video/mp4', filename="list_short.mp4")
    except Exception as e:
        cleanup_files(clean_list)
        raise HTTPException(status_code=400, detail=str(e))
