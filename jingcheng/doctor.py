import importlib.util
import os
from pathlib import Path
import platform
import shutil
import sys
from .util import sha


def inspect(config):
    tools={}
    for name in ["ffmpeg","ffprobe","node","ssh","scp","grok"]:
        value=config.tool(name)
        tools[name]={"path":value,"available":bool(shutil.which(value) or Path(value).is_file())}
    models={}
    for prefix,key in [("matte","model"),("qa","face_model"),("qa","syncnet_weights")]:
        path=config.path(prefix+"."+key)
        hashkey="syncnet_sha256" if key=="syncnet_weights" else key+"_sha256"
        expected=config.get(prefix+"."+hashkey,"")
        exists=path.is_file()
        models[prefix+"."+key]={"path":str(path),"exists":exists,
                                "hash_verified":bool(exists and expected and sha(path)==expected)}
    deps={m:importlib.util.find_spec(m) is not None for m in
          ["numpy","PIL","scipy","soundfile","onnxruntime","cv2","torch","python_speech_features"]}
    return {"python":sys.version.split()[0],"platform":platform.system(),"config":str(config.filename),
            "project_root":str(config.root),"tools":tools,"python_modules":deps,"models":models,
            "renderer_installed":(config.root/"remotion/node_modules").is_dir(),
            "voice_private_assets_present":all(config.path(p).is_file() for p in ["voice.preset","voice.references"]),
            "qa_calibration_configured":bool(config.get("qa.calibration_record")),
            "grok_readiness_flags":{"schema_confirmed":config.get("grok.schema_confirmed"),"privacy_ready":config.get("grok.privacy_ready")},
            "note":"Read-only local inspection. Login, server reachability, reference quality and rendered quality are not inferred."}
