import subprocess
import uuid
import os
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel

app = FastAPI()

class MergeRequest(BaseModel):
    video_url: str
    audio_url: str

def cleanup_files(files):
    for f in files:
        if os.path.exists(f):
            os.remove(f)

@app.post("/merge")
def merge_video_audio(request: MergeRequest, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())
    input_video = f"{req_id}_v.mp4"
    input_audio = f"{req_id}_a.mp3"
    output_file = f"{req_id}_out.mp4"

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
        background_tasks.add_task(cleanup_files, [input_video, input_audio, output_file])
        return {"status": "success", "message": "Merged!", "file": output_file}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "detail": str(e)}

@app.get("/")
def read_root():
    return {"Hello": "FFmpeg API is running!"}
