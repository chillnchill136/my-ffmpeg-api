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

# === CẤU HÌNH FONT LORA (GOOGLE) ===
FONT_BOLD_PATH = "/tmp/Lora-Bold.ttf"
FONT_REG_PATH = "/tmp/Lora-Regular.ttf"

# Link GitHub Raw chính chủ
URL_BOLD = "https://github.com/google/fonts/raw/main/ofl/lora/static/Lora-Bold.ttf"
URL_REG = "https://github.com/google/fonts/raw/main/ofl/lora/static/Lora-Regular.ttf"

def download_font_force(url, save_path):
    if os.path.exists(save_path) and os.path.getsize(save_path) > 20000:
        return
    print(f"⬇️ Đang tải font: {os.path.basename(save_path)}...")
    try:
        r = requests.get(url, stream=True, verify=False, timeout=30)
        if r.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            print("✅ Tải font OK")
    except: pass

@app.on_event("startup")
async def startup_check():
    download_font_force(URL_BOLD, FONT_BOLD_PATH)
    download_font_force(URL_REG, FONT_REG_PATH)

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

# === HÀM LẤY KÍCH THƯỚC VIDEO GỐC ===
def get_video_dimensions(filepath):
    """Trả về (width, height) của video"""
    try:
        cmd = [
            "ffprobe", "-v", "error", 
            "-select_streams", "v:0", 
            "-show_entries", "stream=width,height", 
            "-of", "json", filepath
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(result.stdout)
        width = data['streams'][0]['width']
        height = data['streams'][0]['height']
        print(f"📐 Kích thước Video gốc: {width}x{height}")
        return width, height
    except Exception as e:
        print(f"⚠️ Lỗi probe video: {e}. Dùng mặc định 1080x1920")
        return 1080, 1920 # Fallback an toàn

# === VẼ TEXT (DYNAMIC SIZE) ===
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
    # Tạo Canvas khớp 100% với video gốc
    img = Image.new('RGBA', (target_w, target_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # === CÔNG THỨC TÍNH CỠ CHỮ TỰ ĐỘNG ===
    # Header chiếm khoảng 8% chiều rộng video
    # Body chiếm khoảng 5.5% chiều rộng video
    FONT_SIZE_HEADER = int(target_w * 0.08) 
    FONT_SIZE_BODY = int(target_w * 0.055)
    
    # Đảm bảo không quá bé
    if FONT_SIZE_HEADER < 20: FONT_SIZE_HEADER = 20
    if FONT_SIZE_BODY < 14: FONT_SIZE_BODY = 14

    print(f"🔠 Font Size Dynamic: Header={FONT_SIZE_HEADER}, Body={FONT_SIZE_BODY}")

    try:
        font_header = ImageFont.truetype(FONT_BOLD_PATH, FONT_SIZE_HEADER)
        font_body_bold = ImageFont.truetype(FONT_BOLD_PATH, FONT_SIZE_BODY)
        font_body_reg = ImageFont.truetype(FONT_REG_PATH, FONT_SIZE_BODY)
    except:
        font_header = ImageFont.load_default()
        font_body_bold = ImageFont.load_default()
        font_body_reg = ImageFont.load_default()

    clean_header = header.replace("\\n", "\n").replace("\\N", "\n")
    clean_content = content.replace("\\n", "\n").replace("\\N", "\n")

    # Box rộng 90% video, căn giữa
    box_width = int(target_w * 0.9)
    padding_x = int(target_w * 0.04) # Padding 4%
    max_text_width = box_width - (padding_x * 2)

    import textwrap
    header_lines = []
    # Tính toán số ký tự wrap dựa trên độ rộng (ước lượng)
    chars_per_line = int(max_text_width / (FONT_SIZE_HEADER * 0.55)) 
    for line in clean_header.split('\n'):
        header_lines.extend(textwrap.wrap(line.strip().upper(), width=chars_per_line))

    line_height_header = int(FONT_SIZE_HEADER * 1.2)
    line_height_body = int(FONT_SIZE_BODY * 1.35)
    spacing_header_body = int(target_h * 0.03) # Cách nhau 3% chiều cao
    padding_y = int(target_h * 0.04)
    
    h_header = len(header_lines) * line_height_header
    
    temp_y = 0
    body_items = clean_content.split('\n')
    dummy_draw = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    for item in body_items:
        if not item.strip(): continue
        temp_y = draw_highlighted_line(dummy_draw, 0, temp_y, item, font_body_bold, font_body_reg, max_text_width, line_height_body)
        temp_y += int(target_h * 0.015) # Spacing item
    
    h_body = temp_y
    box_height = padding_y + h_header + spacing_header_body + h_body + padding_y
    
    box_x = (target_w - box_width) // 2
    box_y = (target_h - box_height) // 2
    
    # Vẽ Box Trắng
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], fill=(255, 255, 255, 245), outline=None)
    border_w = int(target_w * 0.005) # Border dày 0.5%
    if border_w < 1: border_w = 1
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], outline=(200, 200, 200, 150), width=border_w)

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
        current_y += int(target_h * 0.015)

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
        font_path = "Arial"
        final_input_video = input_video
        if request.ping_pong:
            try: subprocess.run(["ffmpeg", "-threads", "1", "-i", input_video, "-filter_complex", "[0:v]split[main][rev];[rev]reverse[r];[main][r]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-y", pingpong_video], check=True)
            except: pass
            final_input_video = pingpong_video
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

# === SHORTS LIST (V42 - DYNAMIC RESOLUTION) ===
@app.post("/shorts_list")
def create_shorts_list(request: ShortsRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_bg.mp4"
    input_audio = f"{req_id}_a.mp3"
    overlay_img = f"{req_id}_over.png"
    output_file = f"{req_id}_short.mp4"
    files_to_clean = [input_video, input_audio, overlay_img, output_file]

    try:
        # 1. Tải file
        vid_ok = download_file_req(request.video_url, input_video)
        aud_ok = download_file_req(request.audio_url, input_audio)
        
        # 2. ĐỌC KÍCH THƯỚC VIDEO GỐC
        target_w, target_h = 1080, 1920 # Mặc định
        if vid_ok:
            target_w, target_h = get_video_dimensions(input_video)
        
        # 3. TẠO OVERLAY KHỚP KÍCH THƯỚC ĐÓ
        # Hàm này giờ đã thông minh, tự tính size chữ theo target_w
        create_list_overlay(request.header_text, request.list_content, overlay_img, target_w, target_h)

        # 4. GHÉP FINAL (KHÔNG RESIZE)
        # Giữ nguyên chất lượng video gốc
        print(f"-> Ghép Overlay vào video {target_w}x{target_h}...")
        
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
             # Fallback nền đen nếu không tải được video
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
        raise HTTPException(status_code=400, detail=f"Lỗi: {str(e)}")
