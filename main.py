import subprocess
import uuid
import os
import shutil
import urllib3
import requests
import gc
import json
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

# Tắt cảnh báo SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI()

# === CẤU HÌNH FONT ===
# Lưu vào /tmp/
FONT_DIR = "/tmp"
FONT_BOLD_PATH = os.path.join(FONT_DIR, "Lora-Bold.ttf")
FONT_REG_PATH = os.path.join(FONT_DIR, "Lora-Regular.ttf")

# Link Google GitHub chuẩn
URL_BOLD = "https://github.com/google/fonts/raw/main/ofl/lora/static/Lora-Bold.ttf"
URL_REG = "https://github.com/google/fonts/raw/main/ofl/lora/static/Lora-Regular.ttf"

def check_font_file(path):
    """Kiểm tra xem file font có hợp lệ không"""
    if not os.path.exists(path):
        print(f"❌ File không tồn tại: {path}")
        return False
    
    size = os.path.getsize(path)
    if size < 10000: # Nhỏ hơn 10KB chắc chắn là lỗi
        print(f"❌ File quá nhỏ ({size} bytes): {path}")
        return False
        
    print(f"✅ File OK ({size} bytes): {path}")
    return True

def download_font_force():
    print("--- BẮT ĐẦU TẢI FONT ---")
    try:
        # Tải Bold
        print(f"⬇️ Downloading: {URL_BOLD}")
        r = requests.get(URL_BOLD, timeout=30)
        with open(FONT_BOLD_PATH, 'wb') as f:
            f.write(r.content)
        
        # Tải Regular
        print(f"⬇️ Downloading: {URL_REG}")
        r = requests.get(URL_REG, timeout=30)
        with open(FONT_REG_PATH, 'wb') as f:
            f.write(r.content)
            
        # Kiểm tra lại
        check_font_file(FONT_BOLD_PATH)
        check_font_file(FONT_REG_PATH)
        
    except Exception as e:
        print(f"❌ LỖI TẢI FONT: {e}")

@app.on_event("startup")
async def startup_check():
    # 1. Check thư viện hệ thống
    try:
        import PIL
        print(f"ℹ️ Pillow Version: {PIL.__version__}")
        # Thử load freetype
        ImageFont.core.get_version()
        print("✅ Hỗ trợ FreeType (Có thể đọc file .ttf)")
    except Exception as e:
        print(f"⛔ CẢNH BÁO CỰC MẠNH: Server thiếu thư viện FreeType! Không thể dùng font custom. Lỗi: {e}")

    # 2. Tải font
    download_font_force()

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

def download_file_req(url, filename):
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

# === VẼ TEXT (BỎ Fallback - Lỗi là Crash để biết nguyên nhân) ===
def get_font_objects(size_header, size_body):
    # BẮT BUỘC DÙNG FILE TẢI VỀ. KHÔNG DEFAULT.
    try:
        font_header = ImageFont.truetype(FONT_BOLD_PATH, size_header)
        font_body_bold = ImageFont.truetype(FONT_BOLD_PATH, size_body)
        font_body_reg = ImageFont.truetype(FONT_REG_PATH, size_body)
        return font_header, font_body_bold, font_body_reg
    except OSError as e:
        print(f"❌❌❌ LỖI NGHIÊM TRỌNG: KHÔNG ĐỌC ĐƯỢC FILE FONT! {e}")
        # Thử dùng font hệ thống nếu có
        try:
            sys_font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            print(f"-> Thử cứu bằng font hệ thống: {sys_font}")
            font_header = ImageFont.truetype(sys_font, size_header)
            font_body_bold = ImageFont.truetype(sys_font, size_body)
            font_body_reg = ImageFont.truetype(sys_font, size_body)
            return font_header, font_body_bold, font_body_reg
        except:
            print("💀 Chết thật rồi. Không có font nào dùng được.")
            # Ném lỗi để user biết, không dùng default nữa
            raise Exception("Server Error: Missing Font Libraries. Please check logs.")

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
    
    # SIZE CỰC ĐẠI ĐỂ TEST
    FONT_SIZE_HEADER = int(target_w * 0.08) # 8% width
    FONT_SIZE_BODY = int(target_w * 0.06)   # 6% width
    
    # Load Font (Sẽ crash nếu lỗi)
    font_header, font_body_bold, font_body_reg = get_font_objects(FONT_SIZE_HEADER, FONT_SIZE_BODY)

    clean_header = header.replace("\\n", "\n").replace("\\N", "\n")
    clean_content = content.replace("\\n", "\n").replace("\\N", "\n")

    box_width = int(target_w * 0.9)
    padding_x = int(target_w * 0.05)
    max_text_width = box_width - (padding_x * 2)

    import textwrap
    header_lines = []
    # Wrap text ngắn hơn để test xuống dòng
    chars_per_line = int(max_text_width / (FONT_SIZE_HEADER * 0.6))
    for line in clean_header.split('\n'):
        header_lines.extend(textwrap.wrap(line.strip().upper(), width=chars_per_line))

    line_height_header = int(FONT_SIZE_HEADER * 1.3)
    line_height_body = int(FONT_SIZE_BODY * 1.5)
    spacing_header_body = int(target_h * 0.03) 
    padding_y = int(target_h * 0.05)
    
    h_header = len(header_lines) * line_height_header
    
    temp_y = 0
    body_items = clean_content.split('\n')
    dummy_draw = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    for item in body_items:
        if not item.strip(): continue
        temp_y = draw_highlighted_line(dummy_draw, 0, temp_y, item, font_body_bold, font_body_reg, max_text_width, line_height_body)
        temp_y += int(target_h * 0.02) 
    
    h_body = temp_y
    box_height = padding_y + h_header + spacing_header_body + h_body + padding_y
    
    box_x = (target_w - box_width) // 2
    box_y = (target_h - box_height) // 2
    
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], fill=(255, 255, 255, 245), outline=None)
    border_w = int(target_w * 0.005)
    if border_w < 2: border_w = 2
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
        current_y += int(target_h * 0.02)

    img.save(output_img_path)

@app.post("/merge")
def merge_video_audio(request: MergeRequest, background_tasks: BackgroundTasks):
    return HTTPException(status_code=400, detail="Use shorts_list for now")

@app.post("/podcast")
def create_podcast(request: MergeRequest, background_tasks: BackgroundTasks):
    return HTTPException(status_code=200, detail="OK")

@app.post("/shorts_list")
def create_shorts_list(request: ShortsRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_bg.mp4"
    input_audio = f"{req_id}_a.mp3"
    overlay_img = f"{req_id}_over.png"
    output_file = f"{req_id}_short.mp4"
    files_to_clean = [input_video, input_audio, overlay_img, output_file]

    try:
        vid_ok = download_file_req(request.video_url, input_video)
        aud_ok = download_file_req(request.audio_url, input_audio)
        
        target_w, target_h = 1080, 1920 
        if vid_ok:
            target_w, target_h = get_video_dimensions(input_video)
        
        # Overlay sẽ CRASH nếu không load được font (Thay vì hiện chữ xấu)
        create_list_overlay(request.header_text, request.list_content, overlay_img, target_w, target_h)

        print(f"-> Ghép Overlay (Video gốc: {target_w}x{target_h})...")
        if vid_ok:
            subprocess.run([
                "ffmpeg", "-threads", "4", 
                "-i", input_video,   
                "-i", input_audio,   
                "-i", overlay_img,   
                "-filter_complex", "[0:v][2:v]overlay=0:0[v]", 
                "-map", "[v]", "-map", "1:a", 
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", 
                "-t", str(request.duration), "-y", 
                output_file
            ], check=True)
        else:
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

        background_tasks.add_task(cleanup_files, files_to_clean)
        return FileResponse(output_file, media_type='video/mp4', filename="list_short.mp4")
    except Exception as e:
        cleanup_files(files_to_clean)
        # NẾU CÓ LỖI FONT, NÓ SẼ HIỆN RA Ở ĐÂY TRONG LOG N8N
        raise HTTPException(status_code=400, detail=f"Lỗi: {str(e)}")
