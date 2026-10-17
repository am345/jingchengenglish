"""Frozen native-AAC/PTS evaluator bridge, with detector-specific calibration."""
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from .util import load, save, sha, check_sha


def method_fingerprint(config):
    backend=config.get("qa.face_backend")
    if backend not in {"vision","yunet"}: raise ValueError("Unknown QA face backend")
    detector=config.path("qa.vision_binary" if backend=="vision" else "qa.face_model")
    model=config.path("qa.syncnet_weights")
    check_sha(model,config.get("qa.syncnet_sha256",""))
    if backend=="yunet": check_sha(detector,config.get("qa.face_model_sha256",""))
    inputs={"backend":backend,"detector":sha(detector),"weights":sha(model),
            "evaluator":sha(config.root/"tools/qa/evaluate.py"),
            "model_code":sha(config.root/"vendor/syncnet/SyncNetModel.py"),
            "face_code":sha(config.root/"jingcheng/media.py"),
            "dependencies":{name:importlib.metadata.version(name) for name in ["numpy","scipy","torch","python_speech_features"]},
            "opencv":importlib.metadata.version("opencv-python-headless") if backend=="yunet" else None,
            "media_tools":{name:sha(config.tool(name)) for name in ["ffmpeg","ffprobe"]},
            "protocol":"25fps-40ms-window1.6-stride1-v1"}
    return hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()


def runtime_paths(config):
    backend=config.get("qa.face_backend")
    return {"ffmpeg":str(Path(config.tool("ffmpeg")).resolve()),
            "ffprobe":str(Path(config.tool("ffprobe")).resolve()),
            "face_detector":str(config.root/"jingcheng/media.py"),
            "face_model":str(config.path("qa.vision_binary" if backend=="vision" else "qa.face_model")),
            "face_backend":backend,"config":str(config.filename),
            "model_code":str(config.root/"vendor/syncnet/SyncNetModel.py"),
            "model_weights":str(config.path("qa.syncnet_weights")),
            "method_fingerprint":method_fingerprint(config)}


def calibration(config, fingerprint):
    p=config.path("qa.calibration_record",required=False)
    if p is None or not p.is_file(): return {"valid":False,"reason":"No reviewed calibration record for this backend"}
    record=load(p)
    valid=record.get("method_fingerprint")==fingerprint and record.get("accepted") is True
    evidence=record.get("evidence",[])
    valid=valid and len(evidence)>=3
    for e in evidence:
        f=(p.parent/e["file"]).resolve()
        valid=valid and f.is_file() and sha(f)==e["sha256"]
    return {"valid":bool(valid),"record_sha256":sha(p),"reason":None if valid else "Changed method, missing evidence or unreviewed calibration"}


def freeze(config, timeline_path, output):
    timeline_path=Path(timeline_path).resolve(strict=True);output=Path(output).resolve()
    if output.exists():raise ValueError("Frozen gates are immutable; choose a new filename")
    timeline=load(timeline_path)
    windows={};previous=0
    for c in timeline["clips"]:
        start,end=c["start_frame"],c["end_frame"]
        if type(start) is not int or type(end) is not int or not previous<=start<end<=2160:
            raise ValueError("Invalid ordered 90-second clip spans")
        if c["id"] in windows or c["required_frames"]!=end-start:raise ValueError("Duplicate ID or frame count mismatch")
        previous=end;duration=(end-start)/24
        if duration<2.4: raise ValueError("Fixed SyncNet support needs a longer continuous utterance")
        w=[{"start":float(i),"end":float(i)+1.6} for i in range(math.floor(duration-1.6)+1)]
        # Only declare a tail before measurement; never move it after seeing scores.
        tail=math.floor((duration-1.6)*25+1e-7)/25
        if tail>w[-1]["start"]+1e-7:
            w.append({"start":tail,"end":tail+1.6,"role":"declared_tail"})
        windows[c["id"]]=w
    if not windows:raise ValueError("Empty timeline")
    gate={"timeline_sha256":sha(timeline_path),"model_weights_sha256":config.get("qa.syncnet_sha256"),
          "method_fingerprint":method_fingerprint(config),"analysis_fps":25,"audio_hz":16000,"shift_step_ms":40,
          "search_range_ms":[-320,320],"maximum_absolute_estimated_delay_ms":120,
          "mean_absolute_estimated_delay_ms_strictly_below":60,"window_seconds":1.6,"window_step_seconds":1.0,
          "windows":windows,"window_count":sum(map(len,windows.values()))}
    save(output,gate)
    return {"gate":str(output),"sha256":sha(output),"window_count":gate["window_count"],"method_fingerprint":gate["method_fingerprint"]}


def evaluate(config, arguments):
    env={**os.environ,"JINGCHENG_CONFIG":str(config.filename),"PYTHONPATH":str(config.root)}
    return subprocess.run([sys.executable,str(config.root/"tools/qa/evaluate.py"),*arguments],env=env).returncode
