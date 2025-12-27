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

# === CẤU HÌNH FONT ===
# Lưu vào /tmp để tránh lỗi quyền
FONT_BOLD_PATH = "/tmp/Lora-Bold.ttf"
FONT_REG_PATH = "/tmp/Lora-Regular.ttf"

# Link gốc từ Google Fonts (Thường ổn định hơn)
URL_BOLD = "https://github.com/google/fonts/raw/main/ofl/lora/static/Lora-Bold.ttf"
URL_REG = "https://github.com/google/fonts/raw/main/ofl/lora/static/Lora-Regular.ttf"

def download_font_force(url, save_path):
    """Tải font với cơ chế thử lại mạnh mẽ"""
    print(f"-> Đang tải font: {save_path}...")
    try:
        # verify=False để bỏ qua lỗi SSL nếu có
        r = requests.get(url, stream=True, verify=False, timeout=30)
        if r.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            size = os.path.getsize(save_path)
            print(f"   => Tải xong! Kích thước: {size} bytes")
            if size < 10000: # Nếu file quá nhỏ (<10KB) là lỗi
                print("   => CẢNH BÁO: File quá nhỏ, có thể bị lỗi.")
                return False
            return True
        else:
            print(f"   => Lỗi HTTP: {r.status_code}")
    except Exception as e:
        print(f"   => Lỗi Exception: {e}")
    return False

def get_system_font_fallback():
    """Tìm font có sẵn trên Linux hỗ trợ Tiếng Việt"""
    # Các font phổ biến trên Linux Docker
    common_fonts = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
    ]
    for font in common_fonts:
        if os.path.exists(font):
            print(f"-> Tìm thấy font hệ thống thay thế: {font}")
            return font
    return None

# === KHỞI ĐỘNG: TẢI FONT NGAY ===
@app.on_event("startup")
async def startup_check():
    print("--- STARTUP: DOWNLOAD FONTS ---")
    download_font_force(URL_BOLD, FONT_BOLD_PATH)
    download_font_force(URL_REG, FONT_REG_PATH)

# ... (Các Class Model giữ nguyên) ...
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
        # Dùng requests thay curl để an toàn hơn
        headers = {'User-Agent': 'Mozilla/5.0'}
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

# ... (Hàm vẽ Text Overlay được nâng cấp) ...
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

def create_list_overlay(header, content, output_img_path):
    W, H = 540, 960 
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # 1. QUYẾT ĐỊNH FONT (QUAN TRỌNG NHẤT)
    # Ưu tiên Lora tải về -> Sau đó đến Font hệ thống -> Cuối cùng mới là Default
    final_font_bold = None
    final_font_reg = None
    
    # Check Lora
    if os.path.exists(FONT_BOLD_PATH) and os.path.getsize(FONT_BOLD_PATH) > 10000:
        final_font_bold = FONT_BOLD_PATH
        final_font_reg = FONT_REG_PATH
        print("-> Dùng Font: Lora (Downloaded)")
    else:
        # Check System Font
        sys_font = get_system_font_fallback()
        if sys_font:
            final_font_bold = sys_font
            final_font_reg = sys_font
            print(f"-> Dùng Font: System ({sys_font})")
    
    # Cấu hình Size to
    FONT_SIZE_HEADER = 48  
    FONT_SIZE_BODY = 32    
    
    try:
        if final_font_bold:
            font_header = ImageFont.truetype(final_font_bold, FONT_SIZE_HEADER)
            font_body_bold = ImageFont.truetype(final_font_bold, FONT_SIZE_BODY)
            font_body_reg = ImageFont.truetype(final_font_reg, FONT_SIZE_BODY)
        else:
            print("-> CẢNH BÁO: Không tìm thấy font nào, dùng default (sẽ lỗi tiếng Việt)")
            font_header = ImageFont.load_default()
            font_body_bold = ImageFont.load_default()
            font_body_reg = ImageFont.load_default()
    except Exception as e:
        print(f"Lỗi load font: {e}")
        font_header = ImageFont.load_default()
        font_body_bold = ImageFont.load_default()
        font_body_reg = ImageFont.load_default()

    clean_header = header.replace("\\n", "\n").replace("\\N", "\n")
    clean_content = content.replace("\\n", "\n").replace("\\N", "\n")

    box_width = 480
    padding_x = 25 
    max_text_width = box_width - (padding_x * 2)

    import textwrap
    header_lines = []
    for line in clean_header.split('\n'):
        header_lines.extend(textwrap.wrap(line.strip().upper(), width=18))

    line_height_header = int(FONT_SIZE_HEADER * 1.2)
    line_height_body = int(FONT_SIZE_BODY * 1.4)
    spacing_header_body = 30 
    padding_y = 35
    
    h_header = len(header_lines) * line_height_header
    
    temp_y = 0
    body_items = clean_content.split('\n')
    dummy_draw = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    for item in body_items:
        if not item.strip(): continue
        temp_y = draw_highlighted_line(dummy_draw, 0, temp_y, item, font_body_bold, font_body_reg, max_text_width, line_height_body)
        temp_y += 12 
    
    h_body = temp_y
    box_height = padding_y + h_header + spacing_header_body + h_body + padding_y
    
    box_x = (W - box_width) // 2
    box_y = (H - box_height) // 2
    
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], fill=(255, 255, 255, 245), outline=None)
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], outline=(200, 200, 200, 150), width=3)

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
        current_y += 12

    img.save(output_img_path)

@app.post("/merge")
def merge_video_audio(request: MergeRequest, background_tasks: BackgroundTasks):
    # GIỮ NGUYÊN (Merge video cũ)
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
        
        # Merge API cũng cần font tốt
        font_path = "Arial"
        if os.path.exists(FONT_BOLD_PATH): font_path = FONT_BOLD_PATH
        elif get_system_font_fallback(): font_path = get_system_font_fallback()

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
        # Tải File (Có cơ chế Safe cho Airtable)
        vid_ok = download_file_req(request.video_url, input_video)
        aud_ok = download_file_req(request.audio_url, input_audio)
        
        # Vẽ Overlay (Code V36 Font Fix)
        create_list_overlay(request.header_text, request.list_content, overlay_img)

        # Xử lý Video Nền
        bg_ready = False
        if vid_ok:
            try:
                # Resize nhẹ
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
                "ffmpeg", "-f", "lavfi", "-i", "color=c=black:s=540x960:r=30", 
                "-t", str(request.duration),
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-y", 
                normalized_bg
            ], check=True)

        # Ghép
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
