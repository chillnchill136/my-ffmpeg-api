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

# === TẢI FONT LORA (BOLD & REGULAR) ===
def get_lora_fonts():
    font_bold = "Lora-Bold.ttf"
    font_reg = "Lora-Regular.ttf"
    
    # Link tải Font Lora từ Google Fonts Github
    url_bold = "https://github.com/google/fonts/raw/main/ofl/lora/static/Lora-Bold.ttf"
    url_reg = "https://github.com/google/fonts/raw/main/ofl/lora/static/Lora-Regular.ttf"

    if not os.path.exists(font_bold):
        download_file(url_bold, font_bold, "Font Lora Bold")
    
    if not os.path.exists(font_reg):
        download_file(url_reg, font_reg, "Font Lora Regular")
        
    # Fallback nếu tải lỗi -> Dùng font hệ thống
    if not os.path.exists(font_bold):
        sys_fonts = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)
        if sys_fonts: return sys_fonts[0], sys_fonts[0]
        return None, None
        
    return font_bold, font_reg

# === HÀM VẼ DÒNG CÓ HIGHLIGHT (LOGIC MỚI) ===
def draw_highlighted_line(draw, x_start, y_start, text, font_bold, font_reg, max_width, line_height):
    """
    Vẽ một dòng text, tự động bôi đậm + đỏ phần trước dấu hai chấm (:).
    Tự động xuống dòng nếu tràn viền.
    """
    # Màu sắc
    COLOR_HIGHLIGHT = (204, 0, 0, 255) # Đỏ đậm
    COLOR_NORMAL = (0, 0, 0, 255)      # Đen

    # Tách phần Highlight và Normal
    if ":" in text:
        parts = text.split(":", 1)
        part_bold = parts[0] + ":"
        part_reg = parts[1]
    else:
        part_bold = ""
        part_reg = text

    current_x = x_start
    current_y = y_start
    
    # 1. Vẽ phần Bold (Highlight) trước
    if part_bold:
        # Tách từ để check wrap cho cả phần bold (phòng trường hợp bold quá dài)
        words = part_bold.split()
        for word in words:
            word_w = draw.textlength(word + " ", font=font_bold)
            # Check tràn viền
            if current_x + word_w > x_start + max_width:
                current_x = x_start # Xuống dòng
                current_y += line_height
            
            draw.text((current_x, current_y), word, font=font_bold, fill=COLOR_HIGHLIGHT)
            current_x += word_w + draw.textlength(" ", font=font_bold) # Cộng thêm khoảng trắng

    # 2. Vẽ phần Regular (Normal) tiếp theo
    if part_reg:
        words = part_reg.split()
        for word in words:
            # Thêm dấu cách trước từ (nếu không phải đầu dòng mới)
            space_w = draw.textlength(" ", font=font_reg)
            word_w = draw.textlength(word, font=font_reg)
            
            # Nếu đang ở giữa dòng, cần cộng thêm space
            total_w = word_w + (space_w if current_x > x_start else 0)

            # Check tràn viền
            if current_x + total_w > x_start + max_width:
                current_x = x_start # Xuống dòng
                current_y += line_height
                # Khi xuống dòng mới thì không vẽ space đầu dòng
                draw.text((current_x, current_y), word, font=font_reg, fill=COLOR_NORMAL)
                current_x += word_w
            else:
                # Vẽ tiếp trên dòng hiện tại
                if current_x > x_start:
                    current_x += space_w # Vẽ space
                draw.text((current_x, current_y), word, font=font_reg, fill=COLOR_NORMAL)
                current_x += word_w

    # Trả về Y của dòng tiếp theo (để vẽ item kế tiếp)
    return current_y + line_height

# === HÀM VẼ ẢNH OVERLAY (V13 - LORA & HIGHLIGHT) ===
def create_list_overlay(header, content, output_img_path):
    W, H = 1080, 1920
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    path_bold, path_reg = get_lora_fonts()
    if not path_bold: raise Exception("Lỗi Font")

    # 1. CẤU HÌNH FONT & SIZE
    FONT_SIZE_HEADER = 65  
    FONT_SIZE_BODY = 45    
    
    try:
        font_header = ImageFont.truetype(path_bold, FONT_SIZE_HEADER) # Header dùng Bold
        font_body_bold = ImageFont.truetype(path_bold, FONT_SIZE_BODY) # Body phần Highlight
        font_body_reg = ImageFont.truetype(path_reg, FONT_SIZE_BODY)   # Body phần thường
    except:
        font_header = ImageFont.load_default()
        font_body_bold = ImageFont.load_default()
        font_body_reg = ImageFont.load_default()

    # 2. XỬ LÝ TEXT INPUT
    clean_header = header.replace("\\n", "\n").replace("\\N", "\n")
    clean_content = content.replace("\\n", "\n").replace("\\N", "\n")

    # 3. CẤU HÌNH BOX
    box_width = 940 
    padding_x = 60 
    max_text_width = box_width - (padding_x * 2)

    # 4. TÍNH TOÁN HEADER (Wrap đơn giản)
    import textwrap
    header_lines = []
    for line in clean_header.split('\n'):
        header_lines.extend(textwrap.wrap(line.strip().upper(), width=20))

    # 5. TÍNH TOÁN BODY HEIGHT (Giả lập để vẽ Box)
    # Vì logic vẽ body phức tạp (mixed font), ta vẽ nháp hoặc ước lượng
    # Ở đây để đơn giản và chính xác, ta sẽ vẽ thật lên 1 ảnh nháp hoặc tính toán trong lúc vẽ
    # NHƯNG để vẽ Box trước, ta cần biết Height.
    # Giải pháp: Vẽ Body lên 1 layer tạm để đo chiều cao, hoặc tính toán logic.
    
    line_height_header = int(FONT_SIZE_HEADER * 1.2)
    line_height_body = int(FONT_SIZE_BODY * 1.35)
    spacing_header_body = 50 
    padding_y = 60
    
    # Tính height header
    h_header = len(header_lines) * line_height_header
    
    # Tính height body (Chạy thử logic draw để đếm dòng)
    temp_y = 0
    body_items = clean_content.split('\n')
    for item in body_items:
        if not item.strip(): continue
        # Dùng hàm draw_highlighted_line nhưng không vẽ (chỉ tính toán Y)
        # Lưu ý: Hàm draw bên trên vẽ thật. Để tối ưu code, ta chấp nhận vẽ Box sau (nhưng Box phải nằm dưới text).
        # -> Cách tốt nhất: Vẽ Box vào `img`, sau đó vẽ Text đè lên. 
        # -> Cần tính Height trước.
        
        # Ước lượng số dòng: (Độ dài text * độ rộng trung bình ký tự) / max_width
        # Cách này không chính xác. Ta dùng ImageDraw dummy.
        dummy_draw = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
        temp_y = draw_highlighted_line(dummy_draw, 0, temp_y, item, font_body_bold, font_body_reg, max_text_width, line_height_body)
    
    h_body = temp_y # Tổng chiều cao body
    
    box_height = padding_y + h_header + spacing_header_body + h_body + padding_y
    
    # Tọa độ Box
    box_x = (W - box_width) // 2
    box_y = (H - box_height) // 2
    
    # 6. VẼ BOX TRẮNG
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], fill=(255, 255, 255, 245), outline=None)
    draw.rectangle([(box_x, box_y), (box_x + box_width, box_y + box_height)], outline=(200, 200, 200, 150), width=3)

    # 7. VẼ HEADER
    current_y = box_y + padding_y
    for line in header_lines:
        text_w = draw.textlength(line, font=font_header)
        text_x = box_x + (box_width - text_w) // 2 
        draw.text((text_x, current_y), line, font=font_header, fill=(204, 0, 0, 255))
        current_y += line_height_header

    # 8. VẼ BODY (REAL)
    current_y += spacing_header_body
    start_x = box_x + padding_x
    
    for item in body_items:
        if not item.strip(): continue
        # Gọi hàm vẽ thần thánh
        current_y = draw_highlighted_line(draw, start_x, current_y, item, font_body_bold, font_body_reg, max_text_width, line_height_body)
        # Thêm chút khoảng cách giữa các mục (paragraph spacing)
        current_y += 10 

    img.save(output_img_path)

# ==========================================
# CÁC API KHÁC GIỮ NGUYÊN
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
        # merge dùng font mặc định (Arial) cho sub
        font_path = "ArialBold.ttf" 
        if not os.path.exists(font_path):
             download_file("https://github.com/matomo-org/travis-scripts/raw/master/fonts/Arial-Bold.ttf", font_path, "Font")

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
            # Font sub dùng Arial
            font_cmd = f"fontfile={font_path}:" if os.path.exists(font_path) else ""
            styling = "fontcolor=white:bordercolor=black:borderw=7:fontsize=130"
            filters.append(f"{last_stream}drawtext={font_cmd}text='{sanitized_text}':{styling}:x=(w-text_w)/2:y=(h-text_h)/2[v1]")
            last_stream = "[v1]"

        if has_sub:
            font_arg = font_path if os.path.exists(font_path) else "Arial"
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
        
        has_sub = False
        if request.subtitle_content and len(request.subtitle_content.strip()) > 0:
            with open(subtitle_file, "w", encoding="utf-8") as f:
                f.write(request.subtitle_content)
            has_sub = True

        cmd = ["ffmpeg", "-threads", "1", "-loop", "1", "-i", input_image, "-i", input_audio]
        if has_sub:
            # Podcast dùng Arial cho sub dễ đọc
            font_path = "ArialBold.ttf"
            if not os.path.exists(font_path):
                 download_file("https://github.com/matomo-org/travis-scripts/raw/master/fonts/Arial-Bold.ttf", font_path, "Font")
            
            font_name_sub = font_path if os.path.exists(font_path) else "Arial"
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
