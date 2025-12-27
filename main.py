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
FONT_BOLD = "Lora-Bold.ttf"
FONT_REG = "Lora-Regular.ttf"
CDN_BOLD = "https://cdn.jsdelivr.net/gh/google/fonts/ofl/lora/static/Lora-Bold.ttf"
CDN_REG = "https://cdn.jsdelivr.net/gh/google/fonts/ofl/lora/static/Lora-Regular.ttf"

def download_asset(url, filename):
    """Hàm tải file dùng requests (tốt cho Airtable)"""
    if not url: return False
    print(f"-> Đang tải: {filename}...")
    try:
        # Headers giả lập Chrome để tránh bị chặn
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        # Timeout 60s, stream=True để tải file lớn
        with requests.get(url, headers=headers, stream=True, verify=False, timeout=60) as r:
            if r.status_code != 200:
                print(f"Lỗi HTTP {r.status_code} khi tải {url}")
                return False
            with open(filename, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        
        # Kiểm tra file rác (nhỏ hơn 50KB coi như lỗi)
        if os.path.exists(filename) and os.path.getsize(filename) > 50000:
            print("-> Tải thành công!")
            return True
        else:
            print("-> File tải về quá nhỏ (có thể là file lỗi).")
            return False
    except Exception as e:
        print(f"-> Exception khi tải: {e}")
        return False

@app.on_event("startup")
async def startup_check():
    if not os.path.exists(FONT_BOLD): download_asset(CDN_BOLD, FONT_BOLD)
    if not os.path.exists(FONT_REG): download_asset(CDN_REG, FONT_REG)

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

def get_ready_font():
    if os.path.exists(FONT_BOLD): 
        return FONT_BOLD, FONT_REG if os.path.exists(FONT_REG) else FONT_BOLD
    return None, None

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
    path_bold, path_reg = get_ready_font()
    FONT_SIZE_HEADER, FONT_SIZE_BODY = 38, 26
    try:
        if path_bold:
            font_header = ImageFont.truetype(path_bold, FONT_SIZE_HEADER)
            font_body_bold = ImageFont.truetype(path_bold, FONT_SIZE_BODY)
            font_body_reg = ImageFont.truetype(path_reg, FONT_SIZE_BODY)
        else: raise Exception
    except:
        font_header = font_body_bold = font_body_reg = ImageFont.load_default()

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
        download_asset(request.video_url, input_video)
        download_asset(request.audio_url, input_audio)
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

# === API SHORTS LIST (V34 - AIRTABLE SAFE MODE) ===
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
        # 1. TẢI FILE (Quan trọng: Xử lý link Airtable)
        vid_ready = download_asset(request.video_url, input_video)
        aud_ready = download_asset(request.audio_url, input_audio)
        
        # Tạo Overlay Text (Luôn làm)
        create_list_overlay(request.header_text, request.list_content, overlay_img)

        # 2. XỬ LÝ VIDEO NỀN (An toàn tuyệt đối)
        bg_processed = False
        
        if vid_ready:
            try:
                print("-> Đang resize video nền Airtable...")
                # Resize về 540p nhẹ hều
                subprocess.run([
                    "ffmpeg", "-threads", "1", "-y", 
                    "-i", input_video, 
                    "-t", str(request.duration),
                    "-vf", "scale=540:-2", 
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", 
                    "-pix_fmt", "yuv420p", "-an", 
                    normalized_bg
                ], check=True)
                bg_processed = True
            except:
                print("⚠️ Lỗi resize video nền -> Sẽ dùng nền đen.")
                bg_processed = False
        else:
            print("⚠️ Không tải được video nền -> Sẽ dùng nền đen.")

        # 3. NẾU KHÔNG CÓ VIDEO NỀN -> TẠO NỀN ĐEN
        if not bg_processed:
            print("-> Tạo nền đen thay thế...")
            subprocess.run([
                "ffmpeg", "-f", "lavfi", "-i", f"color=c=black:s=540x960:r=30", 
                "-t", str(request.duration),
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-y", 
                normalized_bg
            ], check=True)

        # 4. GHÉP FINAL (Chắc chắn thành công)
        print("-> Ghép Overlay...")
        cmd_merge = [
            "ffmpeg", "-threads", "4", "-y",
            "-stream_loop", "-1", 
            "-i", normalized_bg, 
            "-i", input_audio, 
            "-i", overlay_img, 
            "-filter_complex", "[0:v][2:v]overlay=0:0[v]", 
            "-map", "[v]", 
            "-map", "1:a", 
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", 
            "-t", str(request.duration), 
            output_file
        ]
        subprocess.run(cmd_merge, check=True)

        background_tasks.add_task(cleanup_files, files_to_clean)
        return FileResponse(output_file, media_type='video/mp4', filename="list_short.mp4")
    except Exception as e:
        cleanup_files(files_to_clean)
        # Chỉ trả lỗi nếu ngay cả việc tạo nền đen cũng chết (hiếm)
        raise HTTPException(status_code=400, detail=f"Fatal Error: {str(e)}")
