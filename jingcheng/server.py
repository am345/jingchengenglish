"""SSH transport. Credentials live in SSH config/agent or an ignored local config."""
from __future__ import annotations
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import uuid
from .config import expand, relative_name
from .util import sha, save


class Server:
    def __init__(self, config, profile):
        self.config = config
        self.values = config.get(f"servers.{profile}")
        if not self.values:
            raise ValueError(f"Server profile missing: {profile}")
        self.host = self.values["host"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@:-]*", self.host):
            raise ValueError("Invalid SSH host/alias")
        self.options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                        "-o", f"ConnectTimeout={int(self.values.get('connect_timeout', 12))}",
                        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2"]
        key = self.values.get("identity_file")
        if key:
            p = Path(expand(key))
            if not p.is_absolute():
                p = config.filename.parent / p
            self.options += ["-i", str(p.resolve()), "-o", "IdentitiesOnly=yes"]

    def command(self, code, *args):
        parts = [self.values.get("python", "python3"), "-c", code, *map(str, args)]
        return [self.config.tool("ssh"), *self.options, "-p", str(self.values.get("port", 22)),
                self.host, shlex.join(parts)]

    def python(self, code, *args, timeout=45):
        return subprocess.run(self.command(code, *args), check=True, capture_output=True,
                              text=True, timeout=timeout).stdout

    def copy(self, local, remote, *, upload=True):
        if not remote.startswith("/") or "\n" in remote:
            raise ValueError("Remote transfer path must be absolute")
        pair = [str(Path(local).resolve()), f"{self.host}:{remote}"]
        if not upload:
            pair.reverse()
        subprocess.run([self.config.tool("scp"), *self.options, "-P",
                        str(self.values.get("port", 22)), *pair], check=True, timeout=1800)

    def under(self, key, name):
        root = PurePosixPath(self.values[key])
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError(f"Remote {key} must be an absolute POSIX path")
        return str(root / relative_name(name))


VERIFY = """import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1]);h=hashlib.sha256()
with p.open('rb') as f:
 for b in iter(lambda:f.read(1048576),b''):h.update(b)
print(json.dumps({'bytes':p.stat().st_size,'sha256':h.hexdigest()}))
"""


def probe(config, name):
    s = Server(config, name)
    return json.loads(s.python(
        "import json,platform,shutil;print(json.dumps({'platform':platform.system(),"
        "'python':platform.python_version(),'nvidia_smi':bool(shutil.which('nvidia-smi'))}))"))


def archive(config, film, execute=False):
    film = Path(film).resolve(strict=True)
    digest, size = sha(film), film.stat().st_size
    s = Server(config, "storage")
    destination = s.under("root", f"final-videos/{digest}/film{film.suffix.lower()}")
    report = dict(local_file=str(film), remote_path=destination, host=s.host,
                  sha256=digest, bytes=size, verified=False, local_file_retained=True)
    if not execute:
        return report
    temporary = destination + ".uploading-" + uuid.uuid4().hex
    s.python("from pathlib import Path;import sys;p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True)",
             temporary)
    s.copy(film, temporary)
    expected = {"sha256": digest, "bytes": size}
    if json.loads(s.python(VERIFY, temporary)) != expected:
        raise ValueError("Remote upload verification failed; local original retained")
    s.python("""from pathlib import Path
import sys,hashlib
src,dest=map(Path,sys.argv[1:3])
if dest.exists():
 h=hashlib.sha256()
 with dest.open('rb') as f:
  for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
 assert h.hexdigest()==sys.argv[3]
 src.unlink()
else:src.rename(dest)
""", temporary, destination, digest)
    if json.loads(s.python(VERIFY, destination)) != expected:
        raise ValueError("Final remote checksum mismatch")
    report.update(verified=True, restore="Use jc assets pull with this remote path, byte count and SHA-256.")
    save(str(film) + ".remote.json", report)
    return report


def pull_assets(config, manifest, execute=False):
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    s = Server(config, data.get("server", "storage"))
    rows, seen = [], set()
    for entry in data["files"]:
        name = relative_name(entry["path"])
        if name in seen:
            raise ValueError("Duplicate asset target")
        seen.add(name)
        dest = config.asset(name)
        expected = {"bytes": entry["bytes"], "sha256": entry["sha256"]}
        if not re.fullmatch(r"[0-9a-f]{64}", expected["sha256"]) or type(expected["bytes"]) is not int or expected["bytes"] < 0:
            raise ValueError("Every asset requires a byte count and full SHA-256")
        remote = s.under("root", entry["remote_relative_path"])
        if dest.exists():
            if {"bytes": dest.stat().st_size, "sha256": sha(dest)} != expected:
                raise ValueError(f"Existing asset differs; refusing overwrite: {dest}")
            rows.append({"path": name, "status": "verified_existing"})
            continue
        if execute:
            dest.parent.mkdir(parents=True, exist_ok=True)
            temp = dest.with_name(dest.name + ".download-" + uuid.uuid4().hex)
            s.copy(temp, remote, upload=False)
            if {"bytes": temp.stat().st_size, "sha256": sha(temp)} != expected:
                raise ValueError(f"Downloaded asset differs: {name}")
            temp.replace(dest)
        rows.append({"path": name, "status": "downloaded_verified" if execute else "planned"})
    return rows
