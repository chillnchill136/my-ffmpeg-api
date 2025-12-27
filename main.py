import subprocess
import uuid
import os
import requests
import shutil
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
            return False
        with open(filename, 'wb') as f:
            response.raw.decode_content = True
            shutil.copyfileobj(response.raw, f)
        return True
    except Exception as e:
        print(f"Lỗi: {e}")
        return False

# === TẢI FONT ===
def get_valid_font_path():
    font_filename = "MyFont-Bold.ttf"
    if os.path.exists(font_filename) and os.path.getsize(font_filename) > 10000:
        return font_filename

    font_urls = [
        "https://github.com/google/fonts/raw/main/apache/roboto/static/Roboto-Bold.ttf",
        "https://github.com/matomo-org/travis-scripts/raw/master/fonts/Arial-Bold.ttf",
    ]
    for url in font_urls:
        if download_file(url, font_filename, "Font Candidate"):
            return font_filename
    
    system_fonts = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)
    if system_fonts: return system_fonts[0]
    return None

# === HÀM CẮT DÒNG THEO PIXEL (LOGIC MỚI) ===
def wrap_text_by_pixel(text, font, max_width, draw):
    """
    Cắt dòng dựa trên độ rộng thực tế (pixel) thay vì số lượng ký tự.
    """
    lines = []
    # Tách các đoạn văn bản có sẵn (do người dùng nhập \n)
    paragraphs = text.split('\n')
    
    for paragraph in paragraphs:
        words = paragraph.split() # Tách từng từ
        if not words:
            continue # Bỏ qua dòng trống
            
        current_line = words[0]
        
        for word in words[1:]:
            # Thử ghép từ tiếp theo vào
            test_line = current_line + " " + word
            # Đo độ rộng thực tế
            w = draw.textlength(test_line, font=font)
            
            if w <= max_width:
                current_line = test_line # Vẫn đủ chỗ -> Ghép tiếp
            else:
                lines.append(current_line) # Hết chỗ -> Lưu dòng cũ
                current_line = word # Bắt đầu dòng mới với từ hiện tại
        
        lines.append(current_line) # Lưu dòng cuối cùng của đoạn
        
    return lines

# === HÀM VẼ ẢNH OVERLAY (V12 - FIX TRÀN VIỀN TUYỆT ĐỐI) ===
def create_list_overlay(header, content, output_img_path):
    W, H = 1080, 1920
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    font_path = get_valid_font_path()
    if not font_path: raise Exception("Lỗi Font")

    # 1. CẤU HÌNH FONT & SIZE
    FONT_SIZE_HEADER = 65  
    FONT_SIZE_BODY = 45    
    
    try:
        font_header = ImageFont.truetype(font_path, FONT_SIZE_HEADER)
        font_body = ImageFont.truetype(font_path, FONT_SIZE_BODY)
    except:
        font_header = ImageFont.load_default()
        font_body = ImageFont.load_default()

    # 2. XỬ LÝ TEXT INPUT (Chuyển \n string thành newline thật)
    clean_header = header.replace("\\n", "\n").replace("\\N", "\n")
    clean_content = content.replace("\\n", "\n").replace("\\N", "\n")

    # 3. CẤU HÌNH KÍCH THƯỚC BOX & WRAP
    box_width = 940 
    padding_x = 60 # Lề trái phải
    
    # Tính toán độ rộng tối đa cho phép của chữ
    # Max Width = Chiều rộng Box - (Lề trái + Lề phải)
    max_text_width_header = box_width - (40 * 2) 
    max_text_width_body = box_width - (padding_x + 30) # Body lùi vào 60px nên trừ hao thêm

    # 4. THỰC HIỆN WRAP (PIXEL PERFECT)
    header_lines = wrap_text_by_pixel(clean_header.upper(), font_header, max_text_width_header, draw)
    body_lines = wrap_text_by_pixel(clean_content, font_body, max_text_width_body, draw)

    # 5. TÍNH TOÁN CHIỀU CAO BOX
    line_height_header = int(FONT_SIZE_HEADER * 1.2)
    line_height_body = int(FONT_SIZE_BODY * 1.3)
    spacing_header_body = 50 
    padding_y = 60
    
    total_header_height = len(header_lines) * line_height_header
    total_body_height = len(body_lines) * line_height_body
    
    box_height = padding_y + total_header_height + spacing_header_body + total_body_height + padding_y
    
    # Tọa độ vẽ Box
    box_x = (W - box_width) // 2
    box_y = (H - box_height) // 2
    
    # 6. VẼ BOX TRẮNG MỜ
    draw.rectangle(
        [(box_x, box_y), (box_x + box_width, box_y + box_height)],
        fill=(255, 255, 255, 245), 
        outline=None
    )
    # Viền nhẹ
    draw.rectangle(
        [(box_x, box_y), (box_x + box_width, box_y + box_height)],
        outline=(200, 200, 200, 150),
        width=3
    )

    # 7. VẼ HEADER (ĐỎ ĐẬM, CĂN GIỮA)
    current_y = box_y + padding_y
    for line in header_lines:
        text_w = draw.textlength(line, font=font_header)
        text_x = box_x + (box_width - text_w) // 2 
        draw.text((text_x, current_y), line, font=font_header, fill=(204, 0, 0, 255))
        current_y += line_height_header

    # 8. VẼ BODY (ĐEN, CĂN TRÁI, THỤT LỀ)
    current_y += spacing_header_body
    
    for line in body_lines:
        draw.text(
            (box_x + padding_x, current_y), 
            line, 
            font=font_body, 
            fill=(0, 0, 0, 255)
        )
        current_y += line_height_body

    img.save(output_img_path)

# ==========================================
# CÁC API GIỮ NGUYÊN
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
