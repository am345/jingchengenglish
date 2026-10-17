"""Approved cleanup structure, preserving every source sample position."""
from pathlib import Path
import math
import tempfile
from .util import save, sha, run


def cleanup(config, source, output):
    import numpy as np
    import soundfile as sf
    import torch
    from scipy.signal import correlate, correlation_lags, resample_poly
    from df.enhance import init_df, enhance
    source=Path(source).resolve(strict=True);output=Path(output).resolve()
    if output.exists():raise ValueError("Use a new cleanup output; do not overwrite or repeatedly denoise")
    x,sr=sf.read(source,dtype="float32")
    if sr!=24000 or x.ndim!=1 or len(x)<sr or not np.isfinite(x).all():
        raise ValueError("Cleanup expects at least one second of finite 24kHz mono master audio")
    model_dir=config.path("audio.deepfilter_model").resolve(strict=True)
    checkpoint=model_dir/"checkpoints/model_120.ckpt.best"
    limit=float(config.get("audio.attenuation_limit_db",18))
    if not 0<=limit<=18:raise ValueError("Validated attenuation range is 0–18 dB")
    torch.set_num_threads(3)
    model,state,_=init_df(model_base_dir=str(model_dir),post_filter=False,log_level="WARNING",log_file=None)
    if state.sr()!=48000:raise ValueError("Expected DeepFilterNet3 48kHz")
    def rms(a):return float(np.sqrt(np.mean(np.asarray(a,dtype=np.float64)**2)+1e-20))
    def db(a):return 20*np.log10(max(rms(a),1e-12))
    def lag(a,b,bound=1200):
        if max(rms(a),rms(b))<1e-8:return 0
        cc=correlate(a,b,method="fft");ls=correlation_lags(len(a),len(b));ok=abs(ls)<=bound
        return int(ls[ok][np.argmax(cc[ok])])
    up=resample_poly(x,2,1).astype(np.float32)
    with torch.inference_mode():wet48=enhance(model,state,torch.from_numpy(up)[None],pad=True,atten_lim_db=None)[0].numpy()
    if len(wet48)!=len(up):raise ValueError("Enhancer changed sample count")
    wet=resample_poly(wet48,1,2).astype(np.float32)
    if len(wet)!=len(x):raise ValueError("Resampler changed sample count")
    dry=10**(-limit/20);y=(dry*x+(1-dry)*wet).astype(np.float32)
    a,b=480,min(4800,len(x)//480*480)
    loud=[i for i,v in enumerate(x[:b].reshape(-1,480)) if db(v)>=-44]
    if loud:b=min(b,(loud[0]-1)*480)
    safe=b-a>=1920 and db(x[a:b])<-45 and max(db(v) for v in x[a:b].reshape(-1,480))<-44
    stationary=False;latency=0
    if safe:
        with tempfile.TemporaryDirectory(prefix="jingcheng-clean-") as tmp:
            ip=Path(tmp)/"buffer.wav";op=Path(tmp)/"filtered.wav"
            prefix=np.tile(y[a:b],math.ceil(8160/(b-a)))[:8160]
            sf.write(ip,np.r_[prefix,y,np.zeros(3600)],sr,subtype="FLOAT")
            filt="asendcmd=c='0.02 afftdn sn start;0.30 afftdn sn stop',afftdn=nr=16:nf=-60:tn=0:gs=12"
            run([config.tool("ffmpeg"),"-v","error","-nostdin","-i",ip,"-af",filt,"-c:a","pcm_f32le",op])
            z,rate=sf.read(op,dtype="float32")
            if rate!=sr:raise ValueError("Filter changed sample rate")
            latency=lag(z[len(prefix):len(prefix)+len(y)],y)
            if not 0<=latency<1200:raise ValueError("Unexpected stationary-filter latency")
            y=z[len(prefix)+latency:len(prefix)+latency+len(x)]
            stationary=True
    if len(y)!=len(x) or not np.isfinite(y).all():raise ValueError("Cleanup sample integrity failed")
    measured=lag(y,x)
    quarters=[lag(y[ix],x[ix],240) for ix in np.array_split(np.arange(len(x)),4)]
    if abs(measured)>1 or max(map(abs,quarters))>1:raise ValueError("Cleanup timing check failed; no output accepted")
    output.parent.mkdir(parents=True,exist_ok=True);sf.write(output,y,sr,subtype="FLOAT")
    report={"source_sha256":sha(source),"output_sha256":sha(output),"model_sha256":sha(checkpoint),
            "samples":len(y),"sample_rate":sr,"same_sample_count":True,"fixed_lag_samples":measured,
            "quarter_lags":quarters,"stationary_applied":bool(stationary),"filter_latency_compensated_samples":latency,
            "attenuation_limit_db":limit,"time_stretch":False,"internal_pause_edit":False,"listening_accepted":False}
    save(str(output)+".cleanup.json",report)
    return report


def compose_master(config, manifest, output):
    """Place complete approved takes on a declared sample timeline; gaps are silence."""
    import numpy as np
    import soundfile as sf
    from .util import load, check_sha
    spec=load(manifest);output=Path(output).resolve()
    if output.exists():raise ValueError("Refuse to overwrite an audio master")
    sr=spec["sample_rate"];total=spec["total_samples"]
    if sr!=24000 or total!=2160000:raise ValueError("Production master is 90 seconds at 24kHz")
    if not spec["segments"]:raise ValueError("No complete utterances")
    y=np.zeros(total,dtype=np.float32);last=0;rows=[]
    for item in spec["segments"]:
        path=config.media(item["source"]);check_sha(path,item["sha256"])
        x,rate=sf.read(path,dtype="float32")
        start=item["start_sample"]
        if rate!=sr or x.ndim!=1 or not np.isfinite(x).all() or not len(x):raise ValueError("Invalid mono source PCM")
        if type(start) is not int or start<last or start+len(x)>total:raise ValueError("Overlapping or truncated utterance")
        y[start:start+len(x)]=x;last=start+len(x)
        rows.append({"source":item["source"],"sha256":sha(path),"start_sample":start,"end_sample":last,"samples":len(x)})
    output.parent.mkdir(parents=True,exist_ok=True);sf.write(output,y,sr,subtype="FLOAT")
    actual,rate=sf.read(output,dtype="float32")
    if rate!=sr or not np.array_equal(actual,y):raise ValueError("Master PCM round-trip failed")
    result={"output_sha256":sha(output),"samples":total,"sample_rate":sr,"segments":rows,
            "time_stretch":False,"utterance_samples_unchanged":True,"gap_policy":"silence at declared positions", "listening_accepted":False}
    save(str(output)+".manifest.json",result);return result
