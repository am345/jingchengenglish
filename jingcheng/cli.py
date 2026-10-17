"""Command-line controller; remote and paid operations default to a plan."""
import argparse
import json
from pathlib import Path
import shutil
import sys
from .config import Config


def main(argv=None):
    for stream in [sys.stdout, sys.stderr]:
        if hasattr(stream, "reconfigure"): stream.reconfigure(encoding="utf-8")
    p=argparse.ArgumentParser(prog="jc")
    p.add_argument("--config",help="Local TOML file (or JINGCHENG_CONFIG)")
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("init-config")
    sub.add_parser("doctor")
    s=sub.add_parser("server-check");s.add_argument("profile",choices=["voice","storage"])
    s=sub.add_parser("archive");s.add_argument("film");s.add_argument("--execute",action="store_true")
    s=sub.add_parser("assets");ss=s.add_subparsers(dest="action",required=True)
    t=ss.add_parser("pull");t.add_argument("manifest");t.add_argument("--execute",action="store_true")
    s=sub.add_parser("voice");ss=s.add_subparsers(dest="action",required=True)
    t=ss.add_parser("launch");t.add_argument("spec");t.add_argument("--execute",action="store_true")
    t=ss.add_parser("status");t.add_argument("job_id")
    t=ss.add_parser("fetch");t.add_argument("job_id");t.add_argument("--execute",action="store_true")
    s=sub.add_parser("grok");s.add_argument("manifest");s.add_argument("--execute",action="store_true")
    s=sub.add_parser("matte");s.add_argument("source");s.add_argument("output")
    s=sub.add_parser("faces");s.add_argument("folder");s.add_argument("--step",type=int,default=6)
    s=sub.add_parser("render");s.add_argument("lesson");s.add_argument("--execute",action="store_true")
    s=sub.add_parser("audio-master");s.add_argument("manifest");s.add_argument("output")
    s=sub.add_parser("clean-audio");s.add_argument("source");s.add_argument("output")
    s=sub.add_parser("qa-freeze");s.add_argument("timeline");s.add_argument("output")
    s=sub.add_parser("qa");s.add_argument("arguments",nargs=argparse.REMAINDER)
    args=p.parse_args(argv)
    try:
        if args.command=="init-config":
            root=Path(__file__).resolve().parents[1]
            dest=Path(args.config) if args.config else root/"config/local.toml"
            if dest.exists(): raise ValueError(f"Configuration already exists: {dest}")
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(root/"config/example.toml",dest)
            print(f"Created {dest.resolve()}. Edit local paths and SSH profiles before production.")
            return 0
        config=Config(args.config)
        if args.command=="doctor":
            from .doctor import inspect
            result=inspect(config)
        elif args.command=="server-check":
            from .server import probe
            result=probe(config,args.profile)
        elif args.command=="archive":
            from .server import archive
            result=archive(config,args.film,args.execute)
        elif args.command=="assets":
            from .server import pull_assets
            result=pull_assets(config,args.manifest,args.execute)
        elif args.command=="voice":
            from . import voice
            if args.action=="launch": result=voice.prepare(config,args.spec,args.execute)
            elif args.action=="status": result=voice.status(config,args.job_id)
            else: result=voice.fetch(config,args.job_id,args.execute)
        elif args.command=="grok":
            from .grok import submit
            result=submit(config,args.manifest,args.execute)
        elif args.command=="matte":
            from .media import matte
            result=matte(config,args.source,args.output)
        elif args.command=="faces":
            from .media import detect_faces
            for row in detect_faces(config,args.folder,args.step): print(json.dumps(row,ensure_ascii=False))
            return 0
        elif args.command=="render":
            from .render import render
            result=render(config,args.lesson,args.execute)
        elif args.command=="audio-master":
            from .audio import compose_master
            result=compose_master(config,args.manifest,args.output)
        elif args.command=="clean-audio":
            from .audio import cleanup
            result=cleanup(config,args.source,args.output)
        elif args.command=="qa-freeze":
            from .quality import freeze
            result=freeze(config,args.timeline,args.output)
        elif args.command=="qa":
            from .quality import evaluate
            return evaluate(config,args.arguments[1:] if args.arguments[:1]==["--"] else args.arguments)
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
        if args.command=="grok" and args.execute and not result["complete"]: return 2
        return 0
    except (ValueError,FileNotFoundError,KeyError) as e:
        print(f"Error: {e}",file=sys.stderr);return 2

if __name__=="__main__": raise SystemExit(main())
