"""Download hash-pinned public model weights into ignored assets."""
import argparse
from pathlib import Path
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jingcheng.config import Config
from jingcheng.util import load, check_sha
p=argparse.ArgumentParser();p.add_argument("name",choices=["rvm","yunet"]);p.add_argument("--config");a=p.parse_args()
c=Config(a.config);s=load(c.root/"config/models.json")[a.name];dest=c.asset(s["file"])
if dest.exists():
 check_sha(dest,s["sha256"]);print(f"Already verified: {dest}")
else:
 dest.parent.mkdir(parents=True,exist_ok=True);temp=dest.with_suffix(dest.suffix+".download")
 if temp.exists():raise SystemExit("Previous partial download exists; inspect it before retrying")
 with urllib.request.urlopen(s["url"],timeout=90) as response,temp.open("xb") as out:
  for chunk in iter(lambda:response.read(1048576),b""):out.write(chunk)
 check_sha(temp,s["sha256"]);temp.rename(dest);print(f"Downloaded and verified: {dest}")
