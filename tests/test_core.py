import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from jingcheng.config import Config, relative_name, safe_id
from jingcheng.grok import submit
from jingcheng.media import rgba_from_alpha
from jingcheng.quality import calibration
from jingcheng.render import validate
from jingcheng.server import Server, pull_assets
from jingcheng.util import save, sha

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("eval_core",ROOT/"tools/qa/evaluate.py")
qa=importlib.util.module_from_spec(spec);spec.loader.exec_module(qa)

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="jc with spaces ")
        self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        (self.root/"config").mkdir();self.file=self.root/"config/local.toml"
        self.file.write_text((ROOT/"config/example.toml").read_text(),encoding="utf-8")
        self.c=Config(self.file)
    def test_config_is_independent_of_working_directory(self):
        original=os.getcwd()
        try:
            os.chdir(tempfile.gettempdir());c=Config(self.file)
            self.assertEqual(c.path("paths.assets"),self.root/"assets")
            self.assertEqual(c.media("run:my-job/frames/G01"),self.root/"runs/my-job/frames/G01")
        finally:os.chdir(original)
    def test_missing_environment_fails(self):
        self.c.data["paths"]["assets"]="${JINGCHENG_MISSING_TEST_VAR}/assets"
        with patch.dict(os.environ,{},clear=True), self.assertRaises(ValueError):self.c.path("paths.assets")
    def test_traversal_and_ids_fail(self):
        for s in ["../x","a/../../x","/x","a\\b","a//b","x:foo","a/./b"]:
            with self.subTest(s=s),self.assertRaises(ValueError):relative_name(s)
        with self.assertRaises(ValueError):safe_id("--delete")
    def test_symlink_escape_fails(self):
        root=self.c.path("paths.assets");root.mkdir()
        outside=self.root/"outside";outside.mkdir()
        try:(root/"link").symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest("Symlink privilege unavailable")
        with self.assertRaises(ValueError):self.c.asset("link/file")
    def test_ssh_quotes_remote_python_arguments(self):
        self.c.data["servers"]["voice"]["python"]="/srv/my env/bin/python"
        s=Server(self.c,"voice");arg="a; $(touch injected) ' literal"
        cmd=s.command("print('ok')",arg)
        self.assertEqual(shlex.split(cmd[-1]),["/srv/my env/bin/python","-c","print('ok')",arg])
        self.assertIn("StrictHostKeyChecking=yes",cmd)
    def test_asset_manifest_cannot_escape(self):
        file=self.root/"manifest.json";save(file,{"files":[{"path":"../bad","bytes":1,"sha256":"a"*64,"remote_relative_path":"a"}]})
        with self.assertRaises(ValueError):pull_assets(self.c,file,False)
        self.assertFalse((self.root/"bad").exists())
    def test_rgb_unchanged_by_matte(self):
        rgb=np.arange(72,dtype=np.uint8).reshape(4,6,3)
        alpha=np.linspace(-1,2,24).reshape(4,6)
        rgba=rgba_from_alpha(rgb,alpha)
        np.testing.assert_array_equal(rgba[:,:,:3],rgb)
        self.assertEqual(rgba[0,0,3],0);self.assertEqual(rgba[-1,-1,3],255)
    def test_complete_gate_limits(self):
        def rows(values):return [{"status":"computed","delay_ms":v,"search_boundary":False} for v in values]
        self.assertTrue(qa.summarize(rows([0,40,120]),True)["timing_gate_pass"])
        self.assertFalse(qa.summarize(rows([0,120]),True)["timing_gate_pass"])
        self.assertFalse(qa.summarize(rows([0,0,160]),True)["timing_gate_pass"])
        data=rows([0,40]);data.append({"status":"unresolved","delay_ms":None})
        report=qa.summarize(data,True)
        self.assertIsNone(report["mean_abs_ms"]);self.assertFalse(report["timing_gate_pass"])
        self.assertEqual(report["window_count"],3)
    def test_shift_sign_and_missing_support(self):
        rng=np.random.default_rng(17);a=rng.normal(size=(150,32)).astype(np.float32)
        v=np.zeros_like(a);v[3:]=a[:-3]
        windows=[{"start":1.,"end":2.6}]
        result=qa.evaluate_windows(v,a,windows)
        self.assertEqual(result[0]["delay_ms"],120)
        vv=np.ones(150,dtype=bool);vv[30]=False
        result=qa.evaluate_windows(v,a,windows,visual_valid=vv)
        self.assertEqual(result[0]["status"],"unresolved")
        self.assertEqual(len(result),1)
    def test_unknown_calibration_cannot_pass(self):
        self.assertFalse(calibration(self.c,"any")["valid"])
    def test_changed_calibration_evidence_rejected(self):
        evidence=[]
        for i in range(3):
            f=self.root/f"r{i}.json";save(f,{"value":i});evidence.append({"file":f.name,"sha256":sha(f)})
        p=self.root/"calibration.json";save(p,{"accepted":True,"method_fingerprint":"abc","evidence":evidence})
        self.c.data["qa"]["calibration_record"]=str(p)
        self.assertTrue(calibration(self.c,"abc")["valid"])
        save(self.root/"r0.json",{"changed":True})
        self.assertFalse(calibration(self.c,"abc")["valid"])
    def grok_spec(self):
        self.c.data["grok"].update(schema_confirmed=True,privacy_ready=True)
        audio=self.c.asset("audio/master.wav");audio.parent.mkdir(parents=True);audio.write_bytes(b"test audio")
        return {"batch_id":"batch1","approved_audio":{"reference":"asset:audio/master.wav","approved":True,"sha256":sha(audio)},
                "jobs":[{"id":"G01","arguments":{"duration":5,"aspect_ratio":"16:9","resolution_name":"720p","prompt":"Complete utterance"}}]}
    def test_batch_validation_before_any_paid_call(self):
        spec=self.grok_spec();spec["jobs"].append({"id":"G02","arguments":{"duration":99}})
        path=self.root/"jobs.json";save(path,spec)
        with patch("jingcheng.grok.subprocess.Popen") as proc,self.assertRaises(ValueError):submit(self.c,path,True)
        proc.assert_not_called()
    def test_cli_events_and_no_resubmit(self):
        spec=self.grok_spec();path=self.root/"jobs.json";save(path,spec)
        video=self.root/"native.mp4";video.write_bytes(b"test container")
        args={**spec["jobs"][0]["arguments"],"images":[]}
        events=[{"type":"tool_call","toolCallId":"1","rawInput":args},
                {"type":"tool_call_update","toolCallId":"1","status":"completed","rawOutput":{"path":str(video)}}]
        class Process:
            stdout=io.StringIO("".join(json.dumps(e)+"\n" for e in events))
            def wait(self):return 0
            def poll(self):return 0
            def kill(self):pass
        with patch("jingcheng.grok.subprocess.Popen",return_value=Process()) as proc:
            result=submit(self.c,path,True);self.assertTrue(result["complete"]);proc.assert_called_once()
        with patch("jingcheng.grok.subprocess.Popen") as proc:
            result=submit(self.c,path,True);self.assertFalse(result["complete"]);proc.assert_not_called()
    def test_master_preserves_whole_utterance_samples(self):
        import soundfile as sf
        from jingcheng.audio import compose_master
        source=self.c.asset("audio/take.wav");source.parent.mkdir(parents=True)
        samples=np.linspace(-0.5,0.5,2400,dtype=np.float32);sf.write(source,samples,24000,subtype="FLOAT")
        manifest=self.root/"master.json";save(manifest,{"sample_rate":24000,"total_samples":2160000,"segments":[
            {"source":"asset:audio/take.wav","sha256":sha(source),"start_sample":4800}]})
        output=self.root/"master.wav";compose_master(self.c,manifest,output)
        y,sr=sf.read(output,dtype="float32");np.testing.assert_array_equal(y[4800:7200],samples)
        self.assertEqual(len(y),2160000);self.assertFalse(y[:4800].any());self.assertFalse(y[7200:].any())
        data=json.loads(manifest.read_text());data["segments"].append(data["segments"][0]);save(manifest,data)
        with self.assertRaises(ValueError):compose_master(self.c,manifest,self.root/"overlap.wav")
    def test_render_rejects_per_shot_geometry(self):
        lesson=json.loads((ROOT/"examples/courseware-demo.json").read_text(encoding="utf-8"))
        lesson["clips"][0]["teacher"]={"scale":0.8}
        with self.assertRaises(ValueError):validate(self.c,lesson)

if __name__=="__main__":unittest.main()
