"""Package reproducible utterances for an existing Linux CosyVoice server."""
import json
from pathlib import Path
import shutil
import tarfile
from .config import safe_id, relative_name
from .server import Server
from .util import load, save, sha


def prepare(config, spec_file, execute=False):
    spec = load(spec_file)
    ident = safe_id(spec["job_id"])
    cases = spec["cases"]
    ids = [safe_id(c["id"]) for c in cases]
    if not cases or len(set(ids)) != len(ids):
        raise ValueError("Empty or duplicate voice cases")
    n = min(config.get("voice.concurrency",2), len(cases))
    if n<1 or len(cases)>n*config.get("voice.max_cases_per_worker",12):
        raise ValueError("Voice batch exceeds configured budget")
    preset = load(config.path("voice.preset"))
    refs_path = config.path("voice.references")
    refs = {r["id"]:r for r in load(refs_path)["references"]}
    if {x["module"] for x in preset["checkpoints"]} != {"llm", "flow"}:
        raise ValueError("Both refined module hashes are required")
    for c in cases:
        if not isinstance(c.get("text"), str) or not c["text"].strip() or not isinstance(c.get("prefix"), str):
            raise ValueError("Every voice case requires complete text and a conditioning prefix")
        if c.get("mode") != "zero_shot" or c.get("reference")==c.get("acoustic_reference"):
            raise ValueError("This teacher preset requires separate LLM performance and acoustic references")
        for key in ["reference","acoustic_reference"]:
            r = refs[c[key]]
            relative_name(r["input_filename"])
            file = (refs_path.parent/r["input_filename"]).resolve(strict=True)
            if not file.is_relative_to(refs_path.parent) or sha(file)!=r["sha256"]:
                raise ValueError("Reference path/hash mismatch")
    server = Server(config,"voice")
    remote = server.under("job_root",ident)
    if not execute:
        return {"job_id":ident,"cases":len(cases),"workers":n,"remote":remote,"submitted":False}
    local = config.job(ident)/"voice"
    local.mkdir(parents=True,exist_ok=False)
    groups = [cases[i::n] for i in range(n)]
    for index, group in enumerate(groups,1):
        w = local/f"w{index:02d}"
        (w/"inputs").mkdir(parents=True)
        (w/"raw").mkdir(); (w/"qa").mkdir()
        save(w/"runtime.json",server.values["runtime"])
        save(w/"inputs/preset.json",preset)
        save(w/"inputs/trials.json",{"seed":spec.get("seed",2026),"cases":group})
        required = {c[k] for c in group for k in ["reference","acoustic_reference"]}
        selected = [refs[k] for k in sorted(required)]
        save(w/"inputs/references.json",{"references":selected})
        for r in selected:
            destination = w/"inputs"/r["input_filename"]
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(refs_path.parent/r["input_filename"],destination)
        shutil.copy2(config.root/"workers/cosyvoice_generate.py",w/"generate.py")
    package = local/"inputs.tar.gz"
    with tarfile.open(package,"w:gz") as tar:
        for w in sorted(local.glob("w*")):
            tar.add(w,arcname=w.name)
    server.python("from pathlib import Path;import sys;Path(sys.argv[1]).mkdir(parents=True,exist_ok=False)",remote)
    server.copy(package,remote+"/inputs.tar.gz")
    result = server.python("""from pathlib import Path
import hashlib,json,os,subprocess,sys,tarfile
w=Path(sys.argv[1]);a=w/'inputs.tar.gz'
assert hashlib.sha256(a.read_bytes()).hexdigest()==sys.argv[2]
with tarfile.open(a) as t:
 for m in t.getmembers():
  assert (w/m.name).resolve().is_relative_to(w.resolve()) and not (m.issym() or m.islnk())
 t.extractall(w)
env=dict(os.environ,OMP_NUM_THREADS='4',TOKENIZERS_PARALLELISM='false',HF_HUB_OFFLINE='1')
jobs=[]
for d in sorted(w.glob('w*')):
 with (d/'qa/worker.log').open('wb') as log:
  p=subprocess.Popen([sys.executable,str(d/'generate.py')],cwd=d,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 jobs.append({'worker':d.name,'pid':p.pid})
print(json.dumps({'jobs':jobs,'status':'running'}))
""",remote,sha(package))
    report = {"job_id":ident,"remote":remote,**json.loads(result)}
    save(local/"launch.json",report)
    return report


def status(config, ident):
    server = Server(config,"voice")
    remote = server.under("job_root",safe_id(ident))
    return json.loads(server.python("""from pathlib import Path
import json,sys
w=Path(sys.argv[1]);assert w.is_dir()
out=[]
for d in sorted(w.glob('w*')):
 p=d/'qa/generation.json'
 out.append({'worker':d.name,**(json.loads(p.read_text()) if p.exists() else {'status':'not_started'})})
print(json.dumps(out,ensure_ascii=False))
""",remote))


def fetch(config, ident, execute=False):
    server = Server(config,"voice")
    remote = server.under("job_root",safe_id(ident))
    local = config.job(ident)/"voice-results"
    if not execute:
        return {"remote":remote,"local":str(local),"downloaded":False}
    if local.exists(): raise ValueError("Choose a new result destination or inspect the previous download")
    expected = json.loads(server.python("""from pathlib import Path
import hashlib,json,sys,tarfile
w=Path(sys.argv[1]);files=[]
for d in sorted(w.glob('w*')):
 report=json.loads((d/'qa/generation.json').read_text())
 assert report['status']=='complete_unapproved',(d.name,report['status'])
 files.extend(d.glob('raw/*.wav'));files.append(d/'qa/generation.json')
assert files
a=w/'results.tar.gz'
with tarfile.open(a,'w:gz') as t:
 for f in files:t.add(f,arcname=str(f.relative_to(w)))
print(json.dumps({'bytes':a.stat().st_size,'sha256':hashlib.sha256(a.read_bytes()).hexdigest()}))
""",remote))
    local.mkdir(parents=True,exist_ok=False)
    package = local/"results.tar.gz"
    server.copy(package,remote+"/results.tar.gz",upload=False)
    if {"bytes":package.stat().st_size,"sha256":sha(package)}!=expected:
        raise ValueError("Audio results checksum mismatch")
    with tarfile.open(package) as tar:
        for m in tar.getmembers():
            target = (local/m.name).resolve()
            if not target.is_relative_to(local.resolve()) or m.issym() or m.islnk() or target.exists():
                raise ValueError("Unsafe or duplicate archive member")
        tar.extractall(local)
    save(local/"integrity.json",expected)
    return {"local":str(local),**expected,"voice_listening_accepted":False}
