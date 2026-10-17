"""Stage portable inputs and render exactly one contiguous source per shot."""
import os
from pathlib import Path
import shutil
import subprocess
from .config import safe_id
from .media import metadata
from .util import check_sha, load, save, sha


def validate(config, lesson):
    if (lesson["fps"],lesson["width"],lesson["height"])!=(24,1280,720):
        raise ValueError("This production layout is frozen at 1280x720, 24 fps")
    total=lesson["duration_frames"];demo=lesson.get("demo") is True
    if type(total) is not int or total<=0 or (not demo and total!=2160):
        raise ValueError("Production export must contain 2160 frames (90 seconds)")
    safe_id(lesson["id"])
    teacher=lesson.get("teacher",{"left":0,"top":0,"scale":1})
    if not 0<float(teacher["scale"])<=1.5:raise ValueError("Invalid fixed teacher transform")
    previous=0;sources=set();ids=set()
    for clip in lesson["clips"]:
        ident=safe_id(clip["id"])
        if ident in ids:raise ValueError("Duplicate clip ID")
        ids.add(ident)
        start,end=clip["start_frame"],clip["end_frame"]
        if type(start) is not int or type(end) is not int or not previous<=start<end<=total:
            raise ValueError("Invalid or overlapping clip timeline")
        previous=end
        if not demo or clip.get("teacher_frames"):
            folder=config.media(clip["teacher_frames"])
            info=load(folder/"manifest.json")
            first=clip.get("source_start_frame",0)
            if type(first) is not int or first<0 or first+end-start>info["frames"] or info["fps"]!=24:
                raise ValueError("Insufficient continuous native source; do not repeat or stretch frames")
            if info["source_sha256"] in sources:raise ValueError("Repeated source across shots; use a single continuous segment")
            sources.add(info["source_sha256"])
            for i in range(first,first+end-start):
                if not (folder/f"{i:04d}.png").is_file():raise ValueError("Missing native source PNG")
        if any(k in clip for k in ["teacher","scale","patches","speed","rate"]):
            raise ValueError("Teacher geometry is lesson-global; internal patches/rate changes are unsupported")
    if not lesson["clips"]:raise ValueError("Lesson requires courseware clips")
    if not demo:
        audio=config.media(lesson["audio"]);digest=sha(audio)
        if not lesson.get("audio_review",{}).get("approved") or lesson["audio_review"].get("sha256")!=digest:
            raise ValueError("Audition and approve this exact audio before video production")
        check_sha(audio,lesson["audio_sha256"])
        audio_meta=metadata(config,audio)
        stream=next(x for x in audio_meta["streams"] if x["codec_type"]=="audio")
        # Approved 90-second master includes deliberate silence. Do not invent it during export.
        duration=float(stream.get("duration",audio_meta["format"]["duration"]))
        if abs(duration-total/24)>1/int(stream["sample_rate"])+1e-5:
            raise ValueError("Approved master must already match the 90-second timeline; no automatic stretching or padding")
    return lesson


def stage(source,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    try:os.link(source,dest)
    except OSError:shutil.copyfile(source,dest)


def render(config, manifest, execute=False):
    lesson=validate(config,load(manifest))
    output=config.job(lesson["id"])/"render"
    if not execute:return {"validated":True,"output":str(output),"submitted":False}
    output.mkdir(parents=True,exist_ok=False)
    public=output/"public";public.mkdir()
    props={**lesson,"clips":[dict(c) for c in lesson["clips"]]}
    provenance=[]
    for c in props["clips"]:
        if c.get("teacher_frames"):
            folder=config.media(c["teacher_frames"]);info=load(folder/"manifest.json")
            start=c.get("source_start_frame",0);count=c["end_frame"]-c["start_frame"]
            c["teacher_prefix"]="teacher/"+c["id"]
            c["source_start_frame"]=0
            for i in range(count):stage(folder/f"{start+i:04d}.png",public/c["teacher_prefix"]/f"{i:04d}.png")
            provenance.append({"clip":c["id"],"source_sha256":info["source_sha256"],
                               "native_start_frame":start,"frames":count,"matte_manifest_sha256":sha(folder/"manifest.json")})
    font=config.path("render.font",required=False)
    if font:
        props["font_file"]="font"+font.suffix;stage(font,public/props["font_file"])
    props_path=output/"props.json";save(props_path,props)
    silent=output/"silent.mp4"
    cmd=[config.tool("node"),str(config.root/"remotion/render.mjs"),str(props_path),str(public),str(silent),
         str(config.get("render.concurrency",2)),str(config.get("render.crf",14))]
    with (output/"render.log").open("w",encoding="utf-8") as log:
        subprocess.run(cmd,cwd=config.root/"remotion",stdout=log,stderr=subprocess.STDOUT,check=True)
    film=output/"film.mp4"
    if lesson.get("demo"):
        silent.rename(film)
    else:
        audio=config.media(lesson["audio"])
        ameta=metadata(config,audio)
        codec=next(s for s in ameta["streams"] if s["codec_type"]=="audio")["codec_name"]
        audio_args=["-c:a","copy"] if codec=="aac" else ["-c:a","aac","-b:a","192k"]
        subprocess.run([config.tool("ffmpeg"),"-v","error","-nostdin","-i",str(silent),"-i",str(audio),
                        "-map","0:v:0","-map","1:a:0","-c:v","copy",*audio_args,"-movflags","+faststart",str(film)],check=True)
    timeline={"clips":[{"id":c["id"],"start_frame":c["start_frame"],"end_frame":c["end_frame"],
              "required_frames":c["end_frame"]-c["start_frame"]} for c in lesson["clips"] if c.get("teacher_frames")]}
    save(output/"timeline.json",timeline)
    report={"film":str(film),"sha256":sha(film),"manifest_sha256":sha(manifest),"native_sources":provenance,
            "speed_changes":False,"fps":24,"frames":lesson["duration_frames"],"quality_gate_pass":None,
            "font_sha256":sha(font) if font else None,"review_required":True}
    save(output/"render-report.json",report)
    return report
