"""Native-frame media helpers: no FPS conversion, hold frames or synthetic RGB."""
from fractions import Fraction
import json
from pathlib import Path
import platform
import subprocess
from .util import check_sha, run, save, sha


def metadata(config, path, timestamps=False):
    args=[config.tool("ffprobe"),"-v","error","-show_streams","-show_format","-of","json"]
    if timestamps:
        args += ["-select_streams","v:0","-show_frames","-show_entries",
                 "stream:format:frame=best_effort_timestamp_time,pkt_duration_time,duration_time"]
    return json.loads(run([*args,path]).stdout)


def native_video(config, path):
    data=metadata(config,path,True)
    v=next(s for s in data["streams"] if s["codec_type"]=="video")
    if (v["width"],v["height"])!=(1280,720) or Fraction(v["avg_frame_rate"])!=24:
        raise ValueError("Production expects native 1280x720 at 24 fps; do not retime an incompatible source")
    pts=[float(f["best_effort_timestamp_time"]) for f in data["frames"]]
    if not pts or any(abs(t-pts[0]-i/24)>2e-5 for i,t in enumerate(pts)):
        raise ValueError("Non-continuous native presentation timestamps")
    return {"frames":len(pts),"fps":24,"width":1280,"height":720,"first_pts_seconds":pts[0]}


def rgba_from_alpha(rgb, alpha):
    import numpy as np
    if alpha.shape!=rgb.shape[:2]:
        raise ValueError("Matte shape differs from native RGB")
    return np.dstack([rgb,np.rint(np.clip(alpha,0,1)*255).astype(np.uint8)])


def matte(config, source, output):
    import numpy as np
    from PIL import Image
    source=Path(source).resolve(strict=True); output=Path(output).resolve()
    meta=native_video(config,source)
    if output.exists(): raise ValueError("Choose a new matte output directory")
    backend=config.get("matte.backend")
    if backend=="vision":
        if platform.system()!="Darwin": raise ValueError("Vision requires macOS; select rvm on Windows/Linux")
        binary=config.path("matte.vision_binary").resolve(strict=True)
        output.mkdir(parents=True)
        run([binary,source,output,meta["frames"]])
        backend_hash=sha(binary)
    elif backend=="rvm":
        import onnxruntime as ort
        model=config.path("matte.model"); check_sha(model,config.get("matte.model_sha256",""))
        provider=config.get("matte.provider","CPUExecutionProvider")
        if provider not in ort.get_available_providers(): raise ValueError(f"Unavailable ONNX provider: {provider}")
        session=ort.InferenceSession(str(model),providers=[provider])
        ratio=float(config.get("matte.downsample_ratio",0.375))
        if not 0<ratio<=1: raise ValueError("Invalid downsample_ratio")
        state=[np.zeros((1,1,1,1),dtype=np.float32) for _ in range(4)]
        output.mkdir(parents=True)
        n=meta["width"]*meta["height"]*3
        with (output/"decode.log").open("wb") as err:
            proc=subprocess.Popen([config.tool("ffmpeg"),"-v","error","-nostdin","-i",str(source),
                "-map","0:v:0","-an","-fps_mode","passthrough","-f","rawvideo","-pix_fmt","rgb24","pipe:1"],
                stdout=subprocess.PIPE,stderr=err)
            try:
                for i in range(meta["frames"]):
                    raw=bytearray()
                    while len(raw)<n:
                        chunk=proc.stdout.read(n-len(raw))
                        if not chunk: raise ValueError("Incomplete native frame")
                        raw.extend(chunk)
                    rgb=np.frombuffer(raw,dtype=np.uint8).reshape(meta["height"],meta["width"],3)
                    inputs={"src":np.transpose(rgb.astype(np.float32)/255,(2,0,1))[None],
                            "downsample_ratio":np.array([ratio],dtype=np.float32),
                            **{f"r{k+1}i":s for k,s in enumerate(state)}}
                    _,alpha,*state=session.run(None,inputs)
                    Image.fromarray(rgba_from_alpha(rgb,alpha[0,0])).save(output/f"{i:04d}.png")
                if proc.stdout.read(1): raise ValueError("Unexpected extra source frame")
                if proc.wait()!=0: raise ValueError("Native decode failed; inspect decode.log")
            finally:
                if proc.poll() is None: proc.kill(); proc.wait()
        backend_hash=sha(model)
    else: raise ValueError("matte.backend must be rvm or vision")
    if len(list(output.glob("[0-9][0-9][0-9][0-9].png")))!=meta["frames"]:
        raise ValueError("Missing native matte frames")
    report={**meta,"source":str(source),"source_sha256":sha(source),"backend":backend,
            "backend_sha256":backend_hash,"rgb_policy":"native RGB; alpha only" if backend=="rvm" else "native CoreImage RGB with Vision alpha",
            "time_stretch":False,"visual_review_required":True}
    save(output/"manifest.json",report)
    return report


def detect_faces(config, folder, step=6):
    import cv2
    folder=Path(folder)
    files=sorted(folder.glob("*.png"))
    if not files or step<1: raise ValueError("Missing PNG frames or invalid sampling step")
    backend=config.get("qa.face_backend")
    if backend=="vision":
        if platform.system()!="Darwin": raise ValueError("Vision face detection requires macOS")
        return [json.loads(s) for s in run([config.path("qa.vision_binary"),folder,step]).stdout.splitlines()]
    if backend!="yunet": raise ValueError("Unsupported face backend")
    model=config.path("qa.face_model");check_sha(model,config.get("qa.face_model_sha256",""))
    detector=cv2.FaceDetectorYN.create(str(model),"",(1280,720),0.9,0.3,5000)
    rows=[]
    for i,p in enumerate(files):
        if i%step and i!=len(files)-1: continue
        img=cv2.imread(str(p))
        if img is None or img.shape[:2]!=(720,1280): raise ValueError("Face input must be 1280x720")
        _,faces=detector.detect(img)
        row={"frame":i}
        if faces is not None and len(faces):
            f=max(faces,key=lambda x:float(x[2]*x[3]))
            row.update(bbox=[float(v) for v in f[:4]],score=float(f[-1]))
        rows.append(row)
    return rows
