import subprocess
import uuid
import os
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI()

class MergeRequest(BaseModel):
    video_url: str
    audio_url: str

def cleanup_files(files):
    """Xóa file tạm sau khi gửi xong"""
    for f in files:
        if os.path.exists(f):
            os.remove(f)

@app.post("/merge")
def merge_video_audio(request: MergeRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_v.mp4"
    input_audio = f"{req_id}_a.mp3"
    output_file = f"{req_id}_out.mp4"

    # Lệnh FFmpeg: map video stream 0, audio stream 1
    cmd = [
        "ffmpeg",
        "-i", request.video_url,
        "-i", request.audio_url,
        "-c:v", "copy",
        "-c:a", "aac",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        "-y",
        output_file
    ]

    try:
        subprocess.run(cmd, check=True)
        
        # Đặt lệnh xóa file sau khi gửi xong để sạch server
        background_tasks.add_task(cleanup_files, [input_video, input_audio, output_file])
        
        # TRẢ VỀ FILE VIDEO TRỰC TIẾP CHO N8N
        return FileResponse(
            path=output_file, 
            media_type='video/mp4', 
            filename="output_video.mp4"
        )

    except subprocess.CalledProcessError as e:
        return {"status": "error", "detail": str(e)}

@app.get("/")
def read_root():
    return {"Hello": "FFmpeg API is ready for Luangiai.vn"}
