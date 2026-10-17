"""Import an existing approved preset locally; discard legacy machine paths."""
import argparse
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jingcheng.config import Config
from jingcheng.util import load, save, check_sha
p=argparse.ArgumentParser();p.add_argument("preset");p.add_argument("--config");a=p.parse_args()
c=Config(a.config);source=Path(a.preset).resolve(strict=True);old=load(source)
dest=c.path("voice.preset");refs_path=c.path("voice.references")
if dest.exists() or refs_path.exists():raise SystemExit("Refuse to overwrite an installed preset")
references=[]
for key in ["llm_performance","flow_acoustic"]:
 r=old["references"][key];name=r.get("input_filename",Path(r["path"]).name)
 if Path(name).name!=name:raise SystemExit("Reference must have a plain filename")
 f=source.parent/name
 check_sha(f,r["sha256"])
 references.append({"id":r["id"],"input_filename":name,"sha256":r["sha256"],"prompt_text":r["prompt_text"]})
for r in references:
 target=refs_path.parent/r["input_filename"]
 if target.exists():check_sha(target,r["sha256"])
 else:target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source.parent/r["input_filename"],target)
preset={"schema_version":1,"id":old["id"],"base_model":{"family":"CosyVoice3","original_asset_sha256":old["base_model"]["original_asset_sha256"]},
 "checkpoints":[{"module":x["module"],"sha256":x["sha256"]} for x in old["checkpoints"]]}
save(dest,preset);save(refs_path,{"references":references});print(f"Imported local private preset: {dest}")
