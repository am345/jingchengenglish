"""Whole-utterance teacher prosody controls, never retimed or spliced."""
from pathlib import Path
from contextlib import contextmanager
import fcntl, hashlib, json, os, random, signal, sys, time, traceback

W = Path(__file__).resolve().parent
RUNTIME = json.loads((W/'runtime.json').read_text())
REPO, MODEL = Path(RUNTIME['cosyvoice_repo']), Path(RUNTIME['model_dir'])
os.environ.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false')
os.environ.pop('onnx_path', None)
sys.path[:0] = [str(REPO), str(REPO/'third_party/Matcha-TTS')]
import numpy as np
import soundfile as sf
import torch
from cosyvoice.cli.cosyvoice import AutoModel
torch.set_num_threads(int(RUNTIME.get("threads", 4)))
torch.set_num_interop_threads(1)

def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda: f.read(8*1024*1024), b''): h.update(b)
    return h.hexdigest()

def seed(n):
    random.seed(n); np.random.seed(n); torch.manual_seed(n); torch.cuda.manual_seed_all(n)

@contextmanager
def no_frontend():
    old = {k: (k in sys.modules, sys.modules.get(k)) for k in ['ttsfrd', 'wetext']}
    try:
        for k in old: sys.modules[k] = None
        yield
    finally:
        for k, (exists, v) in old.items():
            if exists: sys.modules[k] = v
            else: sys.modules.pop(k, None)

label = sys.argv[1] if len(sys.argv)>1 else 'generation'
spec_file = W/'inputs'/('trials.json' if label=='generation' else f'{label}_trials.json')
target = W/'qa'/f'{label}.json'
assert not target.exists(), 'Do not overwrite a previous run'
report = {'status':'starting', 'started_unix':time.time(), 'files':[],
          'new_training':False, 'speed':1.0, 'time_stretch':False,
          'post_synthesis_editing':False, 'target_duration_constraint':None,
          'user_accepted':False, 'assistant_listening_performed':False}

def save():
    tmp=target.with_suffix('.tmp'); tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n'); tmp.replace(target)

def timeout(*_): raise TimeoutError('Bounded experiment exceeded 20 minutes')

def main():
    signal.signal(signal.SIGALRM,timeout); signal.alarm(int(RUNTIME.get("timeout_seconds", 1200)))
    preset=json.loads((W/'inputs/preset.json').read_text())
    refs={r['id']:r for r in json.loads((W/'inputs/references.json').read_text())['references']}
    spec=json.loads(spec_file.read_text()); assert len(spec['cases'])<=12
    weights={'llm':Path(RUNTIME['llm_checkpoint']), 'flow':Path(RUNTIME['flow_checkpoint'])}
    for c in preset['checkpoints']: assert sha(weights[c['module']])==c['sha256']
    for name in preset['base_model']['original_asset_sha256']:
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        assert sha(MODEL/name)==preset['base_model']['original_asset_sha256'][name],name
    required_refs={c['reference'] for c in spec['cases']} | {c['acoustic_reference'] for c in spec['cases'] if 'acoustic_reference' in c}
    for rid in required_refs:
        p=(W/'inputs'/refs[rid]['input_filename']).resolve()
        assert p.is_relative_to((W/'inputs').resolve())
        assert sha(p)==refs[rid]['sha256']; refs[rid]['remote_path']=str(p)
    report.update(status='loading',spec_sha256=sha(spec_file),script_sha256=sha(__file__),
                  checkpoint_sha256={k:sha(p) for k,p in weights.items()})
    save(); seed(spec['seed'])
    with no_frontend(): cosy=AutoModel(model_dir=str(MODEL),load_trt=False,load_vllm=False,fp16=False)
    assert type(cosy).__name__=='CosyVoice3' and cosy.model.device.type=='cuda'
    for name,path in weights.items():
        state=torch.load(path,map_location='cpu',weights_only=True)
        getattr(cosy.model,name).load_state_dict(state,strict=True); del state
    for m in [cosy.model.llm,cosy.model.flow,cosy.model.hift]:
        m.to(device=cosy.model.device,dtype=torch.float32).eval().requires_grad_(False)
    report['strict_load_verified']=True
    errors=[]; tokens=[]; job0=cosy.model.llm_job; wave0=cosy.model.token2wav
    def job(*a,**kw):
        try:
            with torch.inference_mode(): job0(*a,**kw)
        except BaseException as e:
            errors.append((e,traceback.format_exc())); cosy.model.llm_end_dict[a[-1]]=True
    def wave(*a,**kw):
        tok=kw.get('token',a[0] if a else None); tokens.append(tok.detach().cpu().long().tolist())
        return wave0(*a,**kw)
    cosy.model.llm_job=job; cosy.model.token2wav=wave
    for case in spec['cases']:
        ref=refs[case['reference']]; seed(case.get('seed',spec['seed']))
        errors.clear(); tokens.clear(); tick=time.monotonic()
        report.update(status='generating',active=case['id']); save()
        with torch.inference_mode():
            if case['mode']=='zero_shot':
                inp=cosy.frontend.frontend_zero_shot(case['text'],case['prefix']+ref['prompt_text'],ref['remote_path'],cosy.sample_rate,'')
                assert inp['llm_prompt_speech_token'].numel()>0
            else:
                assert case['mode']=='instruct2'
                inp=cosy.frontend.frontend_instruct2(case['text'],case['prefix'],ref['remote_path'],cosy.sample_rate,'')
                assert 'llm_prompt_speech_token' not in inp
            if 'acoustic_reference' in case:
                acoustic=refs[case['acoustic_reference']]
                acoustic_input=cosy.frontend.frontend_zero_shot(case['text'],case['prefix']+acoustic['prompt_text'],acoustic['remote_path'],cosy.sample_rate,'')
                for key in ['flow_prompt_speech_token','flow_prompt_speech_token_len','prompt_speech_feat','prompt_speech_feat_len','flow_embedding']:
                    inp[key]=acoustic_input[key]
            conditioning={k: int(inp[k].numel()) if k in inp else 0
                          for k in ['llm_prompt_speech_token','flow_prompt_speech_token','prompt_speech_feat']}
            pieces=[r['tts_speech'].detach().cpu().float().numpy().reshape(-1)
                    for r in cosy.model.tts(**inp,stream=False,speed=1.0)]
        if errors: raise RuntimeError(errors[0][1]) from errors[0][0]
        assert len(pieces)==1
        y=pieces[0]; assert np.isfinite(y).all() and len(y)>0
        out=W/'raw'/f"{case['id']}.wav"; assert not out.exists()
        sf.write(out,y,cosy.sample_rate,subtype='FLOAT')
        z,s=sf.read(out,dtype='float32'); assert s==cosy.sample_rate and np.array_equal(y,z)
        row={**case,'path':str(out),'sha256':sha(out),'sample_rate':s,'samples':len(y),
             'seconds':len(y)/s,'peak_abs':float(abs(y).max()),
             'samples_abs_ge_1':int((abs(y)>=1).sum()),'wall_seconds':time.monotonic()-tick,
             'conditioning_elements':conditioning,'reference_sha256':ref['sha256'],
             'acoustic_reference_sha256':refs[case.get('acoustic_reference',case['reference'])]['sha256'],
             'speech_tokens':list(tokens)}
        report['files'].append(row); save()
        print(json.dumps({k:row[k] for k in ['id','seconds','peak_abs','conditioning_elements']},ensure_ascii=False),flush=True)
    report.update(status='complete_unapproved',active=None,elapsed_seconds=time.time()-report['started_unix'])
    signal.alarm(0)

with (W/f'{label}.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); save()
    try: main()
    except BaseException as e:
        report.update(status='failed',error=str(e),traceback=traceback.format_exc()); raise
    finally: save()
