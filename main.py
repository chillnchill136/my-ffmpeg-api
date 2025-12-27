import subprocess
import uuid
import os
import shutil
import requests
import gc
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont, features

app = FastAPI()

# === CẤU HÌNH FONT ===
# Lưu font vào thư mục chuẩn của Linux Font
FONT_DIR = "/app/fonts"
if not os.path.exists(FONT_DIR): os.makedirs(FONT_DIR, exist_ok=True)

FONT_PATH = os.path.join(FONT_DIR, "Lora-Bold.ttf")
# Link Google Font Chính Chủ
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/lora/static/Lora-Bold.ttf"

def download_font():
    """Tải font Lora về và Check kỹ"""
    print(f"--- ĐANG TẢI FONT VỀ {FONT_PATH} ---")
    try:
        r = requests.get(FONT_URL, timeout=30)
        with open(FONT_PATH, 'wb') as f:
            f.write(r.content)
        
        size = os.path.getsize(FONT_PATH)
        print(f"✅ Đã tải xong. Size: {size} bytes")
        
        # TEST LOAD NGAY LẬP TỨC
        try:
            test_font = ImageFont.truetype(FONT_PATH, 50)
            print("🎉🎉🎉 LOAD THÀNH CÔNG FONT LORA! FREETYPE ĐANG HOẠT ĐỘNG! 🎉🎉🎉")
        except OSError as e:
            print(f"💀💀💀 CHẾT RỒI: CÓ FILE NHƯNG KHÔNG ĐỌC ĐƯỢC. LỖI FREETYPE: {e}")
            
    except Exception as e:
        print(f"❌ Lỗi tải mạng: {e}")

@app.on_event("startup")
async def startup_check():
    # Check thư viện hệ thống
    print(f"🖥️ FREETYPE SUPPORT: {features.check('freetype2')}")
    download_font()

class ShortsRequest(BaseModel):
    video_url: str
    audio_url: str
    header_text: str = "TEST FONT LORA" 
    list_content: str = ""        
    duration: int = 5             

def cleanup_files(files):
    for f in files:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass
    gc.collect() 

def download_file(url, filename):
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            if r.status_code == 200:
                with open(filename, 'wb') as f:
                    shutil.copyfileobj(r.raw, f)
                return True
    except: pass
    return False

def create_overlay(header, content, output_img):
    # Tạo ảnh Full HD
    img = Image.new('RGBA', (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # LOAD FONT LORA
    try:
        # Size 70 cho dễ nhìn
        font = ImageFont.truetype(FONT_PATH, 70)
        font_status = "LORA OK"
    except:
        font = ImageFont.load_default()
        font_status = "DEFAULT FONT (ERROR)"
        print("⚠️ Overlay đang dùng Font Default xấu xí!")

    # Vẽ chữ để test
    # Màu đỏ, viền trắng
    draw.text((100, 300), f"FONT STATUS: {font_status}", font=font, fill="red")
    
    # Vẽ Header (Tiếng Việt)
    draw.text((100, 500), header, font=font, fill="#F05A28") # Màu cam brand
    
    # Vẽ Nội dung
    draw.text((100, 700), content, font=font, fill="white")

    img.save(output_img)

@app.post("/shorts_list")
def create_shorts_list(request: ShortsRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_v.mp4"
    input_audio = f"{req_id}_a.mp3"
    overlay_img = f"{req_id}_over.png"
    output_file = f"{req_id}_out.mp4"
    
    clean_list = [input_video, input_audio, overlay_img, output_file]

    try:
        download_file(request.video_url, input_video)
        download_file(request.audio_url, input_audio)
        
        # Tạo Overlay test font
        create_overlay(request.header_text, request.list_content, overlay_img)

        # Lệnh FFmpeg đơn giản nhất để test (Không resize, không crop)
        # Chỉ dán ảnh đè lên video gốc
        subprocess.run([
            "ffmpeg", "-y",
            "-i", input_video,
            "-i", input_audio,
            "-i", overlay_img,
            "-filter_complex", "[0:v][2:v]overlay=0:0[v]",
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            "-t", str(request.duration),
            output_file
        ], check=True)

        background_tasks.add_task(cleanup_files, clean_list)
        return FileResponse(output_file, media_type='video/mp4', filename="test_font.mp4")
    except Exception as e:
        cleanup_files(clean_list)
        raise HTTPException(status_code=400, detail=str(e))
