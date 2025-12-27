import subprocess
import uuid
import os
import requests
import shutil
import glob
import urllib3
import textwrap
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

# Tắt cảnh báo SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

# === HÀM BỔ TRỢ ===
def cleanup_files(files):
    for f in files:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass

def download_file(url, filename):
    if not url: return False
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=60, verify=False)
        if r.status_code != 200: return False
        with open(filename, 'wb') as f:
            shutil.copyfileobj(r.raw, f)
        return True
    except: return False

# === LOGIC TÌM FONT THÔNG MINH (FONT HUNTER) ===
def find_best_font():
    """
    Tự động quét thư mục để tìm file font tốt nhất.
    """
    print("--- BẮT ĐẦU TÌM FONT ---")
    
    # 1. Liệt kê tất cả file .ttf hiện có để debug
    all_ttf = glob.glob("**/*.ttf", recursive=True)
    print(f"Các file TTF tìm thấy trên server: {all_ttf}")

    # 2. Ưu tiên tìm Lora Bold (bất kể chữ hoa thường)
    # Tìm file có chứa 'Lora' và 'Bold'
    candidates_bold = [f for f in all_ttf if 'lora' in f.lower() and 'bold' in f.lower()]
    candidates_reg = [f for f in all_ttf if 'lora' in f.lower() and 'regular' in f.lower()]

    path_bold = candidates_bold[0] if candidates_bold else None
    path_reg = candidates_reg[0] if candidates_reg else None

    # 3. Nếu không thấy Lora, tìm Arial
    if not path_bold:
        candidates_arial = [f for f in all_ttf if 'arial' in f.lower()]
        if candidates_arial:
            print("-> Không thấy Lora, dùng tạm Arial")
            path_bold = candidates_arial[0]
            path_reg = candidates_arial[0]

    # 4. Trả kết quả
    if path_bold:
        if not path_reg: path_reg = path_bold # Dùng Bold cho cả Reg nếu thiếu
        print(f"-> CHỐT DÙNG FONT: {path_bold} & {path_reg}")
        return path_bold, path_reg
    
    # 5. Cùng đường: Tự tải Arial về
    print("-> Không tìm thấy font nào cả! Đang tải Arial cứu hộ...")
    backup_font = "ArialRescue.ttf"
    if download_file("https://github.com/matomo-org/travis-scripts/raw/master/fonts/Arial-Bold.ttf", backup_font):
        return backup_font, backup_font
        
    return None, None

# === LOGIC VẼ TEXT (GIỮ NGUYÊN) ===
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

# === VẼ OVERLAY (V18) ===
def create_list_overlay(header, content, output_img_path):
    W, H = 1080, 1920
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # --- TỰ ĐỘNG TÌM FONT ---
    path_bold, path_reg = find_best_font()
    
    if not path_bold:
        # Nếu vào đây là lỗi nghiêm trọng -> List file ra log để user biết
        all_files = glob.glob("**/*", recursive=True)
        print("DANH SÁCH FILE TRÊN SERVER:", all_files)
        raise Exception("LỖI: Server không có file font nào (.ttf). Vui lòng check Log Railway.")

    FONT_SIZE_HEADER = 65  
    FONT_SIZE_BODY = 45    
    
    font_header = ImageFont.truetype(path_bold, FONT_SIZE_HEADER)
    font_body_bold = ImageFont.truetype(path_bold, FONT_SIZE_BODY)
    font_body_reg = ImageFont.truetype(path_reg, FONT_SIZE_BODY)

    clean_header = header.replace("\\n", "\n").replace("\\N", "\n")
    clean_content = content.replace("\\n", "\n").replace("\\N", "\n")

    box_width = 940 
    padding_x = 60 
    max_text_width = box_width - (padding_x * 2)

    header_lines = []
    for line in clean_header.split('\n'):
        header_lines.extend(textwrap.wrap(line.strip().upper(), width=20))

    line_height_header = int(FONT_SIZE_HEADER * 1.2)
    line_height_body = int(FONT_SIZE_BODY * 1.4)
    spacing_header_body = 50 
    padding_y = 60
    
    h_header = len(header_lines) * line_height_header
    
    temp_y = 0
    body_items = clean_content.split('\n')
    dummy_draw = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    for item in body_items:
        if not item.strip(): continue
        temp_y = draw_highlighted_line(dummy_draw, 0, temp_y, item, font_body_bold, font_body_reg, max_text_width, line_height_body)
        temp_y += 15 
    
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
        current_y += 15

    img.save(output_img_path)

# ==========================================
# CÁC API KHÁC (GIỮ NGUYÊN)
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
        download_file(request.video_url, input_video)
        download_file(request.audio_url, input_audio)
        path_bold, _ = find_best_font() # Dùng font hunter
        font_path = path_bold if path_bold else "Arial"

        final_input_video = input_video
        if request.ping_pong:
            try:
                subprocess.run(["ffmpeg", "-threads", "1", "-i", input_video, "-filter_complex", "[0:v]split[main][rev];[rev]reverse[r];[main][r]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-y", pingpong_video], check=True)
                final_input_video = pingpong_video
            except: pass

        has_sub = False
        if request.subtitle_content and len(request.subtitle_content.strip()) > 0:
            with open(subtitle_file, "w", encoding="utf-8") as f: f.write(request.subtitle_content)
            has_sub = True

        filters = [f"[0:v]format=yuv420p[v0]"]
        last_stream = "[v0]"
        if request.keyword:
            sanitized_text = request.keyword.replace(":", "\\:").replace("'", "")
            font_cmd = f"fontfile={font_path}:" if os.path.exists(font_path) else ""
            styling = "fontcolor=white:bordercolor=black:borderw=7:fontsize=130"
            filters.append(f"{last_stream}drawtext={font_cmd}text='{sanitized_text}':{styling}:x=(w-text_w)/2:y=(h-text_h)/2[v1]")
            last_stream = "[v1]"

        if has_sub:
            font_arg = font_path if os.path.exists(font_path) else "Arial"
            style = f"FontName={font_arg},FontSize=24,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,MarginV=30,Alignment=2"
            filters.append(f"{last_stream}subtitles={subtitle_file}:fontsdir=.:force_style='{style}'[v2]")
            last_stream = "[v2]"

        cmd = ["ffmpeg", "-threads", "1", "-stream_loop", "-1", "-i", final_input_video, "-i", input_audio, "-filter_complex", ";".join(filters), "-map", last_stream, "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", "-y", output_file]
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
        download_file(request.image_url, input_image)
        download_file(request.audio_url, input_audio)
        has_sub = False
        if request.subtitle_content and len(request.subtitle_content.strip()) > 0:
            with open(subtitle_file, "w", encoding="utf-8") as f: f.write(request.subtitle_content)
            has_sub = True
        cmd = ["ffmpeg", "-threads", "1", "-loop", "1", "-i", input_image, "-i", input_audio]
        if has_sub:
            path_bold, _ = find_best_font()
            font_name_sub = path_bold if path_bold else "Arial"
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
        download_file(request.video_url, input_video)
        download_file(request.audio_url, input_audio)
        create_list_overlay(request.header_text, request.list_content, overlay_img)
        cmd = ["ffmpeg", "-threads", "1", "-stream_loop", "-1", "-i", input_video, "-i", input_audio, "-i", overlay_img, "-filter_complex", f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920:(iw-ow)/2:(ih-oh)/2[bg];[bg][2:v]overlay=0:0[v]", "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-c:a", "aac", "-t", str(request.duration), "-y", output_file]
        subprocess.run(cmd, check=True)
        background_tasks.add_task(cleanup_files, files_to_clean)
        return FileResponse(output_file, media_type='video/mp4', filename="list_short.mp4")
    except Exception as e:
        cleanup_files(files_to_clean)
        raise HTTPException(status_code=400, detail=str(e))
