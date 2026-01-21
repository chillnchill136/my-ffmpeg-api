import subprocess
import uuid
import os
import shutil
import requests
import gc
import json
import random
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from PIL import Image, ImageDraw, ImageFont, features
import urllib3

# Tắt cảnh báo SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI()

# ==========================================
# 1. CẤU HÌNH FONT & HỆ THỐNG
# ==========================================
FONT_DIR = "/app/fonts"
if not os.path.exists(FONT_DIR): os.makedirs(FONT_DIR, exist_ok=True)

FONT_BOLD_PATH = os.path.join(FONT_DIR, "Lora-Bold.ttf")
FONT_REG_PATH = os.path.join(FONT_DIR, "Lora-Regular.ttf")

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
# 2. MODELS
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

class SceneItem(BaseModel):
    scene_id: int
    video_url: str
    audio_url: str

class SmartMergeRequest(BaseModel):
    scenes: list[SceneItem]
    final_filename: str = "smart_merge_output.mp4"

# ==========================================
# 3. HELPERS
# ==========================================
def cleanup_files(files):
    for f in files:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass
    gc.collect() 

def download_file(url, filename):
    print(f"⬇️ Đang tải file từ: {url}")
    if not url: return False
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://google.com',
            'Accept': '*/*'
        }
        with requests.get(url, headers=headers, stream=True, verify=False, timeout=60, allow_redirects=True) as r:
            if r.status_code != 200: 
                print(f"❌ Lỗi HTTP {r.status_code}")
                return False
            with open(filename, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        
        if os.path.exists(filename) and os.path.getsize(filename) > 100: 
            return True
        return False
    except Exception as e:
        print(f"❌ Exception download: {str(e)}")
        return False

# ==========================================
# 4. DRAWING LOGIC
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

def create_list_overlay(header, content, output_img_path):
    target_w, target_h = 1080, 1920
    img = Image.new('RGBA', (target_w, target_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    FONT_SIZE_HEADER = int(target_w * 0.07)
    FONT_SIZE_BODY = int(target_w * 0.05)
    font_header, font_body_bold, font_body_reg = get_font_objects(FONT_SIZE_HEADER, FONT_SIZE_BODY)
    
    box_width = int(target_w * 0.88)
    padding_x = int(target_w * 0.06)
    max_text_width = box_width - (padding_x * 2)
    
    clean_header = header.replace("\\n", "\n").replace("\\N", "\n")
    clean_content = content.replace("\\n", "\n").replace("\\N", "\n")
    
    import textwrap
    header_lines = []
    chars_per_line = int(max_text_width / (FONT_SIZE_HEADER * 0.65))
    for line in clean_header.split('\n'): 
        header_lines.extend(textwrap.wrap(line.strip().upper(), width=chars_per_line))
    
    line_height_header = int(FONT_SIZE_HEADER * 1.3)
    line_height_body = int(FONT_SIZE_BODY * 1.5)
    spacing_header_body = int(target_h * 0.035)
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
    border_w = int(target_w * 0.006)
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
        current_y += int(target_h * 0.015) 
        
    try:
        wm_text = "luangiai.vn"
        wm_size = int(target_w * 0.04) 
        font_wm = ImageFont.truetype(FONT_BOLD_PATH, wm_size)
        wm_w = draw.textlength(wm_text, font=font_wm)
        wm_x = (target_w - wm_w) // 2
        wm_y = target_h - int(target_h * 0.05) - wm_size
        draw.text((wm_x, wm_y), wm_text, font=font_wm, fill=(220, 220, 220, 80))
    except: pass
    
    img.save(output_img_path)

def get_random_hash_filter():
    filters = []
    if random.choice([True, False]): filters.append("hflip")
    filters.append("noise=c0s=2:allf=t")
    bri = random.uniform(-0.02, 0.02)
    sat = random.uniform(0.95, 1.05)
    filters.append(f"eq=brightness={bri:.3f}:saturation={sat:.3f}")
    return ",".join(filters)

# ==========================================
# 5. API 1 & 2
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
        if not download_file(request.video_url, input_video): raise Exception(f"DL Fail Video")
        if not download_file(request.audio_url, input_audio): raise Exception(f"DL Fail Audio")
        
        final_input_video = input_video
        if request.ping_pong:
            try:
                hash_filters = get_random_hash_filter()
                subprocess.run([
                    "ffmpeg", "-threads", "2", "-y",
                    "-i", input_video, 
                    "-filter_complex", 
                    f"[0:v]{hash_filters}[hashed];[hashed]split[main][rev];[rev]reverse[r];[main][r]concat=n=2:v=1:a=0[v]", 
                    "-map", "[v]", 
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", 
                    pingpong_video
                ], check=True)
                final_input_video = pingpong_video
            except Exception as e:
                print(f"PingPong Fail: {e}")

        cmd = [
            "ffmpeg", "-threads", "2", "-y",
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
        if not download_file(request.video_url, input_video): raise Exception(f"DL Fail Video")
        if not download_file(request.audio_url, input_audio): raise Exception(f"DL Fail Audio")
        
        create_list_overlay(request.header_text, request.list_content, overlay_img)

        bg_ready = False
        try:
            hash_filters = get_random_hash_filter()
            subprocess.run([
                "ffmpeg", "-threads", "2", "-y",
                "-i", input_video,
                "-filter_complex", 
                f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,{hash_filters}[processed];[processed]split[main][rev];[rev]reverse[r];[main][r]concat=n=2:v=1:a=0[v]",
                "-map", "[v]",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                processed_bg
            ], check=True)
            bg_ready = True
        except Exception as e:
            print(f"BG Process Fail: {e}")

        if bg_ready:
            subprocess.run([
                "ffmpeg", "-threads", "2", "-y",
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
        else: raise Exception("FFmpeg processing failed")

        background_tasks.add_task(cleanup_files, clean_list)
        return FileResponse(output_file, media_type='video/mp4', filename="list_short.mp4")
    except Exception as e:
        cleanup_files(clean_list)
        raise HTTPException(status_code=400, detail=str(e))

# ==========================================
# 6. API 3: /smart_merge (FIXED CONCAT LOGIC)
# ==========================================

@app.post("/smart_merge")
def smart_merge_endpoint(request: SmartMergeRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    output_file = f"{req_id}_smart_final.mp4"
    clean_list = [output_file]
    
    try:
        temp_dir = f"temp_{req_id}"
        if not os.path.exists(temp_dir): os.makedirs(temp_dir)
        
        def validate_media(file_path):
            cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
            try:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                return float(res.stdout.strip())
            except: return None

        inputs = []
        filter_complex = ""
        concat_str = "" # Dùng chuỗi chung để đảm bảo thứ tự
        
        for i, scene in enumerate(request.scenes):
            v_path = os.path.join(temp_dir, f"v_{i}.mp4")
            a_path = os.path.join(temp_dir, f"a_{i}.mp3")
            
            if not download_file(scene.video_url, v_path): raise Exception(f"Download Error at Scene {i+1} (Video)")
            if not download_file(scene.audio_url, a_path): raise Exception(f"Download Error at Scene {i+1} (Audio)")
            
            v_dur = validate_media(v_path)
            a_dur = validate_media(a_path)
            if v_dur is None: raise Exception(f"Corrupt Video File at Scene {i+1}")
            if a_dur is None: raise Exception(f"Corrupt Audio File at Scene {i+1}")
            
            # Input Video & Audio
            inputs.extend(['-stream_loop', '-1', '-i', v_path, '-i', a_path])
            
            v_idx = i * 2
            a_idx = i * 2 + 1
            
            # Xử lý Video
            filter_complex += (
                f"[{v_idx}:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920,setsar=1,fps=30,format=yuv420p,"
                f"trim=duration={a_dur},setpts=PTS-STARTPTS[v{i}];"
            )
            
            # Xử lý Audio
            filter_complex += f"[{a_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{i}];"
            
            # QUAN TRỌNG: Nối xen kẽ [v0][a0][v1][a1]... cho đúng chuẩn Concat
            concat_str += f"[v{i}][a{i}]"

        # Lệnh Concat
        filter_complex += f"{concat_str}concat=n={len(request.scenes)}:v=1:a=1[outv][outa]"
        
        cmd = ['ffmpeg', '-threads', '4', '-y'] + inputs + [
            '-filter_complex', filter_complex,
            '-map', '[outv]', '-map', '[outa]',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28', 
            '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart',
            output_file
        ]
        
        print(f"🚀 Processing {len(request.scenes)} scenes...")
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        if result.returncode != 0:
            print(f"❌ FFmpeg Error Output:\n{result.stderr}")
            raise Exception(f"FFmpeg Merge Failed. Check Railway Logs.")

        def cleanup_wrapper():
            cleanup_files(clean_list)
            try: shutil.rmtree(temp_dir) 
            except: pass

        background_tasks.add_task(cleanup_wrapper)
        return FileResponse(output_file, media_type='video/mp4', filename=request.final_filename)

    except Exception as e:
        try: shutil.rmtree(f"temp_{req_id}")
        except: pass
        cleanup_files(clean_list)
        print(f"❌ Smart Merge Error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
