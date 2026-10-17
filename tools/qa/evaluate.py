#!/usr/bin/env python3
"""Evaluate existing candidate/final media against caller-frozen original windows.

No rendering, generation, repair, speed change or window selection is performed.
The final mode reads all shots from one final export and its actual AAC/PTS.
Candidate mode reads one complete rendered shot with its actual intended AAC;
its mean is descriptive and never becomes an additional per-shot <60 ms gate.
"""
import argparse
import csv
import datetime
from fractions import Fraction
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True
from jingcheng.config import Config
from jingcheng.quality import runtime_paths, method_fingerprint, calibration
from jingcheng.util import sha
EXPECTED_WEIGHTS = "961e8696f888fce4f3f3a6c3d5b3267cf5b343100b238e79b2659bff2c605442"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def run(runtime, tool, *args, capture_bytes=False):
    env = dict(os.environ)
    command = [sys.executable, '-m', 'jingcheng', '--config', runtime['config'], 'faces', str(args[0]), '--step', str(args[1])] if tool == 'face_detector' else [runtime[tool], *map(str, args)]
    proc = subprocess.run(command, env=env, capture_output=True, text=not capture_bytes)
    if proc.returncode:
        error = proc.stderr.decode(errors='replace') if capture_bytes else proc.stderr
        raise RuntimeError(f'{tool} failed ({proc.returncode}): {error[-6000:]}')
    return proc


def load_config(timeline_path, gate_path, frozen_sha, runtime):
    require(bool(re.fullmatch(r'[0-9a-f]{64}', frozen_sha)), '--gate-sha256 must be the previously frozen SHA-256')
    require(sha(gate_path) == frozen_sha, 'Frozen quality_gate.json hash changed')
    gate = json.loads(gate_path.read_text())
    require(gate.get('timeline_sha256') == sha(timeline_path), 'Gate must bind this exact frozen timeline_sha256')
    timeline = json.loads(timeline_path.read_text())
    clips = {}
    previous_end = 0
    for item in timeline['clips']:
        clip = dict(item)
        ident = clip['id']
        require(bool(re.fullmatch(r'[A-Za-z0-9_-]+', ident)), 'Unsafe clip id')
        require(ident not in clips, f'Duplicate clip: {ident}')
        start, end = clip['start_frame'], clip['end_frame']
        require(type(start) is int and type(end) is int, 'Frame indices must be integers')
        require(previous_end <= start < end <= 2160, f'Invalid/overlapping 90-second frame span: {ident}')
        require(clip['required_frames'] == end - start, f'required_frames differs: {ident}')
        previous_end = end
        clips[ident] = clip
    require(bool(clips), 'Timeline has no clips')
    expected = {'analysis_fps': 25, 'audio_hz': 16000, 'shift_step_ms': 40,
                'search_range_ms': [-320, 320], 'maximum_absolute_estimated_delay_ms': 120,
                'mean_absolute_estimated_delay_ms_strictly_below': 60}
    for key, value in expected.items():
        require(gate.get(key) == value, f'Unexpected frozen {key}: {gate.get(key)!r}')
    for key, value in [('window_seconds', 1.6), ('window_length_seconds', 1.6),
                       ('window_step_seconds', 1.0), ('stride_seconds', 1.0)]:
        require(gate.get(key, value) == value, f'Unexpected {key}')
    require(gate.get('model_weights_sha256', EXPECTED_WEIGHTS) == EXPECTED_WEIGHTS == sha(runtime['model_weights']), 'SyncNet weights changed')
    require(gate.get('method_fingerprint') == runtime['method_fingerprint'], 'Detector, evaluator or model changed; freeze a new protocol before comparing candidates')
    if 'approved_master_sha256' in gate:
        master = timeline_path.parent / timeline.get('approved_master', 'inputs/approved_master.wav')
        require(sha(master) == gate['approved_master_sha256'], 'The frozen approved master changed')
    require(set(gate['windows']) == set(clips), 'Every timeline clip must have its own frozen windows')
    count = 0
    for ident, windows in gate['windows'].items():
        require(bool(windows), f'No frozen windows for {ident}')
        duration = clips[ident]['required_frames'] / 24
        last_start = None
        for window in windows:
            start, end = window['start'], window['end']
            require(type(start) in (int, float) and type(end) in (int, float), 'Window seconds must be numbers')
            require(math.isfinite(start) and math.isfinite(end), 'Nonfinite frozen boundary')
            require(0 <= start < end <= duration + 1e-9, f'Window outside {ident}')
            require(end - start <= 1.6 + 1e-9, f'Window longer than 1.6 seconds: {ident}')
            if end - start < 1.6 - 1e-9:
                require(window is windows[-1] and gate.get('allow_short_final_window') is True,
                        'A shortened last window must be explicitly frozen as allow_short_final_window=true')
            if last_start is not None and abs(start - last_start - 1) >= 1e-9:
                require(window is windows[-1] and window.get('role') == 'declared_tail'
                        and last_start < start < last_start + 1
                        and abs(start * 25 - round(start * 25)) < 1e-9,
                        'Only a predeclared final 40 ms-grid tail window may differ from the 1-second step')
            last_start = start
            count += 1
    require(type(gate['window_count']) is int and count == gate['window_count'], 'Frozen window_count differs')
    return gate, clips


def summarize(rows, full_film=False):
    import numpy as np
    available = [r['delay_ms'] for r in rows if r.get('delay_ms') is not None]
    complete = bool(rows) and all(r['status'] == 'computed' and not r.get('search_boundary') for r in rows)
    values = np.asarray(available, dtype=np.float64)
    mean = float(np.abs(values).mean()) if complete else None
    maximum = int(np.abs(values).max()) if complete else None
    median = float(np.median(values)) if complete else None
    return {'window_count': len(rows),
            'resolved_window_count': sum(r['status'] == 'computed' and not r.get('search_boundary') for r in rows),
            'unresolved_window_count': sum(r['status'] != 'computed' or bool(r.get('search_boundary')) for r in rows),
            'search_boundary_count': sum(bool(r.get('search_boundary')) for r in rows),
            'mean_abs_ms': mean, 'max_abs_ms': maximum,
            'median_signed_delay_ms': median,
            'mean_abs_deviation_from_median_ms': float(np.abs(values - median).mean()) if complete else None,
            'over_120_count': sum(abs(value) > 120 for value in available),
            'all_windows_resolved': complete,
            'maximum_window_gate_pass': bool(complete and maximum <= 120),
            'timing_gate_pass': bool(complete and maximum <= 120 and mean < 60) if full_film else None,
            'mean_gate_scope': 'All original full-film windows only; per-clip means are descriptive',
            'available_estimates_descriptive_only': {
                'count': len(available), 'mean_abs_ms': float(np.abs(values).mean()) if len(values) else None,
                'max_abs_ms': int(np.abs(values).max()) if len(values) else None},
            'thresholds': {'full_film_mean_absolute_ms_lt': 60, 'every_window_absolute_ms_le': 120}}


def evaluate_windows(visual, audio, windows, visual_valid=None, audio_valid=None, rms=None):
    import numpy as np
    import torch
    n = min(len(visual), len(audio))
    indices = np.arange(8, n - 8)
    shifts = np.arange(-8, 9)
    vv = np.ones(n, dtype=bool) if visual_valid is None else visual_valid[:n]
    av = np.ones(n, dtype=bool) if audio_valid is None else audio_valid[:n]
    v, a = torch.from_numpy(visual[:n]), torch.from_numpy(audio[:n])
    distances = np.stack([torch.nn.functional.pairwise_distance(v[indices], a[indices - int(s)]).numpy()
                          for s in shifts], axis=1) if len(indices) else np.zeros((0, len(shifts)))
    common_valid = vv[indices].copy()
    for shift in shifts:
        common_valid &= av[indices - int(shift)]
    rows = []
    for window in windows:
        mask = (indices / 25 >= window['start']) & (indices / 25 < window['end'])
        count = int(mask.sum())
        reasons, flags = [], []
        row = {**window, 'status': 'unresolved', 'delay_ms': None, 'abs_delay_ms': None,
               'count': count, 'search_boundary': False, 'flags': flags,
               'actual_feature_start_seconds': float(indices[mask][0] / 25) if count else None,
               'actual_feature_end_seconds_exclusive': float((indices[mask][-1] + 1) / 25) if count else None}
        if count < 20:
            reasons.append('Insufficient common feature support in the unchanged original window')
        elif not np.isfinite(distances[mask]).all():
            reasons.append('Nonfinite model distances')
        else:
            curve = distances[mask].mean(axis=0)
            index = int(curve.argmin())
            delay = int(shifts[index] * 40)
            margin = float(np.median(curve) - curve[index])
            row.update(delay_ms=delay, abs_delay_ms=abs(delay), search_boundary=abs(delay) == 320,
                       zero_distance=float(curve[8]), best_distance=float(curve[index]),
                       median_minus_best_distance=margin, distance_curve=curve.tolist())
            if not common_valid[mask].all():
                reasons.append('Original window intersects unresolved face detection or missing audio PTS support; no features were dropped')
            if row['search_boundary']:
                reasons.append('Best estimate touches the frozen ±320 ms search boundary')
            if margin <= 0.01:
                flags.append('weak_distance_margin_diagnostic_only_not_a_validated_threshold')
            if rms is not None:
                energy = float(rms(window['start'], window['end']))
                row['audio_rms'] = energy
                if energy == 0:
                    reasons.append('Whole original window is digital silence; no usable speech timing evidence')
                elif energy < 1e-4:
                    flags.append('low_audio_energy_diagnostic_only')
            row['status'] = 'unresolved' if reasons else 'computed'
        row['reason'] = '; '.join(reasons) if reasons else None
        rows.append(row)
    return rows


def video_metadata(runtime, film):
    probe = json.loads(run(runtime, 'ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', film).stdout)
    video = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
    audio = next(stream for stream in probe['streams'] if stream['codec_type'] == 'audio')
    require((video['width'], video['height']) == (1280, 720), 'Expected the actual 1280x720 rendered candidate/export')
    require(audio['codec_name'] == 'aac', 'Input must contain its actual intended AAC; no separate WAV substitution')
    data = json.loads(run(runtime, 'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames',
                          '-show_entries', 'frame=pts,best_effort_timestamp,pkt_duration,duration', '-of', 'json', film).stdout)
    time_base = Fraction(video['time_base'])
    pts = []
    for frame in data['frames']:
        timestamp = frame.get('best_effort_timestamp', frame.get('pts'))
        duration = frame.get('duration', frame.get('pkt_duration'))
        require(timestamp is not None and duration is not None, 'Actual frame PTS/duration is unavailable')
        pts.append((Fraction(int(timestamp)) * time_base, Fraction(int(duration)) * time_base))
    require(bool(pts) and all(d > 0 for _, d in pts), 'Empty or invalid decoded frame timestamps')
    require(all(t == pts[0][0] + Fraction(i, 24) and d == Fraction(1, 24) for i, (t, d) in enumerate(pts)),
            'Expected continuous original 24 fps presentation cadence; evaluator will not retime it')
    return probe, pts


def decode_audio(runtime, film, out, probe):
    import soundfile as sf
    wav = out / 'actual_export_decoded.wav'
    proc = run(runtime, 'ffmpeg', '-hide_banner', '-nostdin', '-copyts', '-i', film, '-map', '0:a:0',
               '-vn', '-af', 'ashowinfo', '-c:a', 'pcm_f32le', '-y', wav)
    # ashowinfo observes the actual decoder output after AAC priming/edit-list handling.
    blocks, cursor = [], 0
    pattern = r'\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:\s*([\d.eE+-]+).*?\brate:\s*(\d+)\s+nb_samples:\s*(\d+)'
    for match in re.finditer(pattern, proc.stderr):
        number, pts, displayed, rate, count = match.groups()
        rate, count, pts = int(rate), int(count), int(pts)
        require(abs(pts / rate - float(displayed)) <= max(1 / rate, abs(float(displayed)) * 1e-5),
                'Unexpected audio filter time base; do not silently reset PTS')
        blocks.append({'n': int(number), 'pts_samples': pts, 'sample_rate': rate,
                       'sample_count': count, 'decoded_start_sample': cursor})
        cursor += count
    decoded, sr = sf.read(wav, dtype='float32', always_2d=True)
    require(bool(blocks) and all(b['sample_rate'] == sr for b in blocks), 'Missing/changing actual audio time base')
    require(cursor == len(decoded), 'Decoded PCM sample count differs from its recorded PTS blocks')
    require(all(b['pts_samples'] >= a['pts_samples'] + a['sample_count'] for a, b in zip(blocks, blocks[1:])),
            'Overlapping/nonmonotonic audio PTS needs investigation')
    meta = {'sample_rate': sr, 'channels': decoded.shape[1], 'sample_count': len(decoded),
            'first_decoded_pts_seconds': blocks[0]['pts_samples'] / sr,
            'last_decoded_end_seconds': (blocks[-1]['pts_samples'] + blocks[-1]['sample_count']) / sr,
            'audio_stream_start_time': next(s for s in probe['streams'] if s['codec_type'] == 'audio').get('start_time'),
            'decoded_wav_sha256': sha(wav), 'blocks': blocks,
            'rounding': 'round(absolute video PTS * source sample rate); duration rounded separately; no independent stream zeroing'}
    write_json(out / 'audio_presentation_times.json', meta)
    return decoded, sr, blocks, meta


def audio_at_pts(decoded, sr, blocks, start, duration):
    import numpy as np
    begin = round(start * sr)
    size = round(duration * sr)
    mono = np.zeros(size, dtype=np.float32)
    covered = np.zeros(size, dtype=bool)
    for block in blocks:
        lo = max(begin, block['pts_samples'])
        hi = min(begin + size, block['pts_samples'] + block['sample_count'])
        if hi <= lo:
            continue
        source = block['decoded_start_sample'] + lo - block['pts_samples']
        mono[lo - begin:hi - begin] = decoded[source:source + hi - lo].mean(axis=1)
        covered[lo - begin:hi - begin] = True
    return mono, covered


class FeatureExtractor:
    def __init__(self, runtime):
        import torch
        self.torch, self.runtime = torch, runtime
        import python_speech_features
        self.psf = python_speech_features
        spec = importlib.util.spec_from_file_location('portable_frozen_syncnet', runtime['model_code'])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        self.model = module.S().eval()
        self.model.load_state_dict(torch.load(runtime['model_weights'], map_location='cpu', weights_only=True), strict=True)

    def extract(self, film, clip, media_start_frame, pts, decoded, sr, blocks, out):
        import numpy as np
        from PIL import Image, ImageDraw
        from scipy import signal
        from scipy.signal import resample_poly
        torch = self.torch
        out.mkdir(parents=True, exist_ok=True)
        n = clip['required_frames']
        selected = pts[media_start_frame:media_start_frame + n]
        start = selected[0][0]
        duration = selected[-1][0] + selected[-1][1] - start
        local_times = np.array([float(t - start) for t, _ in selected])
        audio, audio_covered = audio_at_pts(decoded, sr, blocks, start, duration)
        cache = out / 'features.npz'
        if cache.exists():
            data = np.load(cache)
            return data['visual'], data['audio'], data['visual_valid'], data['audio_valid'], audio
        frames = out / 'frames'
        frames.mkdir(exist_ok=True)
        run(self.runtime, 'ffmpeg', '-v', 'error', '-nostdin', '-copyts', '-i', film, '-map', '0:v:0',
            '-vf', f'select=between(n\\,{media_start_frame}\\,{media_start_frame + n - 1})',
            '-frames:v', n, '-fps_mode', 'passthrough', '-start_number', '0', '-y', frames / '%04d.png')
        require(len(list(frames.glob('*.png'))) == n, 'Decoded analysis frame count differs')
        proc = run(self.runtime, 'face_detector', frames, 6)
        (out / 'geometry.jsonl').write_text(proc.stdout)
        geometry = [json.loads(row) for row in proc.stdout.splitlines() if row.strip()]
        found = [row for row in geometry if 'bbox' in row]
        require(bool(found), 'No usable face detections; every original window remains unresolved')
        count = min(int(duration * 25), int(len(audio) * 25 // sr))
        last = count - 5
        require(last > 16, 'Insufficient source duration for the fixed model support')
        sample_times = np.arange(count) / 25
        indices = np.array([int(np.argmin(abs(local_times - t))) for t in sample_times])
        detected_times = np.array([local_times[row['frame']] for row in found])
        boxes = np.array([row['bbox'] for row in found])
        cx = signal.medfilt(np.interp(sample_times, detected_times, boxes[:, 0] + boxes[:, 2] / 2), 13)
        cy = signal.medfilt(np.interp(sample_times, detected_times, boxes[:, 1] + boxes[:, 3] / 2), 13)
        bs = signal.medfilt(np.interp(sample_times, detected_times, np.maximum(boxes[:, 2], boxes[:, 3]) / 2), 13)
        sample_valid = np.ones(count, dtype=bool)
        for index, row in enumerate(geometry):
            if 'bbox' in row:
                continue
            left = geometry[max(0, index - 1)]['frame']
            right = geometry[min(len(geometry) - 1, index + 1)]['frame']
            sample_valid[(indices >= left) & (indices <= right)] = False
        if len(found) / max(len(geometry), 1) <= 0.8:
            sample_valid[:] = False
        imgs = []
        for index, native in enumerate(indices):
            with Image.open(frames / f'{native:04d}.png') as image:
                box = (int(cx[index] - bs[index] * 1.4), int(cy[index] - bs[index]),
                       int(cx[index] + bs[index] * 1.4), int(cy[index] + bs[index] * 1.8))
                crop = image.convert('RGB').crop(box).resize((224, 224), Image.Resampling.BILINEAR)
                imgs.append(np.asarray(crop)[:, :, ::-1].copy())
        divisor = math.gcd(sr, 16000)
        resampled = resample_poly(audio, 16000 // divisor, sr // divisor)
        mfcc = self.psf.mfcc(np.rint(np.clip(resampled, -1, 1) * 32767).astype(np.int16), 16000).T
        cc = torch.from_numpy(mfcc[None, None].astype(np.float32))
        tensor = torch.from_numpy(np.stack(imgs).transpose(3, 0, 1, 2).copy()).float()[None]
        with torch.inference_mode():
            af = torch.cat([self.model.forward_aud(torch.cat([cc[:, :, :, v * 4:v * 4 + 20]
                            for v in range(i, min(last, i + 16))])) for i in range(0, last, 16)])
            vf = torch.cat([self.model.forward_lip(torch.cat([tensor[:, :, v:v + 5]
                            for v in range(i, min(last, i + 8))])) for i in range(0, last, 8)])
        vv = np.array([sample_valid[v:v + 5].all() for v in range(last)])
        av = np.array([audio_covered[round(v / 25 * sr):round((v / 25 + 0.215) * sr)].all() for v in range(last)])
        np.savez_compressed(cache, visual=vf.numpy(), audio=af.numpy(), visual_valid=vv, audio_valid=av)
        write_json(out / 'manifest.json', {'clip': clip['id'], 'media_start_frame': media_start_frame,
                   'timeline_start_frame': clip['start_frame'], 'source_frames': n,
                   'actual_video_start_seconds': float(start), 'duration_seconds': float(duration),
                   'analysis_samples': count, 'feature_count': last,
                   'face_detections': len(found), 'face_detection_samples': len(geometry),
                   'missing_audio_samples': int((~audio_covered).sum()),
                   'crop': runtime_crop(self.runtime),
                   'missing_face_policy': 'Keep all original windows; unresolved when support intersects a failed sampled detector interval',
                   'analysis_nearest_native_frame_indices': indices.tolist()})
        sample = np.linspace(0, count - 1, 8, dtype=int)
        sheet = Image.new('RGB', (896, 500), '#1c2421')
        draw = ImageDraw.Draw(sheet)
        for i, index in enumerate(sample):
            x, y = (i % 4) * 224, (i // 4) * 250
            sheet.paste(Image.fromarray(imgs[index][:, :, ::-1]), (x, y + 26))
            draw.text((x + 4, y + 5), f'{clip["id"]} media {float(start) + index / 25:.2f}s', fill='white')
        sheet.save(out / 'crop_review.jpg', quality=92)
        return vf.numpy(), af.numpy(), vv, av, audio


def runtime_crop(runtime):
    return runtime['face_backend'] + ' bbox +13 median; x ±1.4 radius, y -1.0/+1.8 radius; 224x224 BGR'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeline', type=Path, required=True)
    parser.add_argument('--gate', type=Path, required=True)
    parser.add_argument('--gate-sha256', required=True)
    parser.add_argument('--mode', choices=['candidate', 'final'], default='final')
    parser.add_argument('--film', type=Path)
    parser.add_argument('--clip-id', help='Candidate only: complete original shot id')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--check-config', action='store_true')
    parser.add_argument('--keep-analysis-frames', action='store_true')
    parser.add_argument('--fail-on-gate', action='store_true', help='Exit 3 for a failed final gate or candidate window upper bound')
    args = parser.parse_args()
    config = Config()
    runtime = runtime_paths(config)
    gate, clips = load_config(args.timeline, args.gate, args.gate_sha256, runtime)
    if args.check_config:
        print(json.dumps({'configuration_valid': True, 'window_count': gate['window_count'],
                          'clips': {k: [v['start_frame'], v['end_frame']] for k, v in clips.items()},
                          'timeline_sha256': sha(args.timeline), 'gate_sha256': args.gate_sha256}, ensure_ascii=False))
        return 0
    require(args.film is not None, '--film is required unless --check-config is used')
    if args.mode == 'candidate':
        require(args.clip_id in clips, '--clip-id must name a frozen complete shot')
        selected = {args.clip_id: clips[args.clip_id]}
    else:
        require(args.clip_id is None, 'Final evaluation must contain every original window; --clip-id is not allowed')
        selected = clips
    import numpy as np
    import torch
    began = time.monotonic()
    film = args.film.resolve(strict=True)
    digest = sha(film)
    out = args.output_dir.resolve() if args.output_dir else config.path('paths.runs') / 'qa' / f'{args.mode}_{digest[:12]}'
    out.mkdir(parents=True, exist_ok=True)
    provenance = {'film': str(film), 'film_sha256': digest, 'mode': args.mode, 'clip_id': args.clip_id,
                  'timeline_sha256': sha(args.timeline), 'gate_sha256': args.gate_sha256,
                  'evaluator_sha256': sha(__file__), 'runtime_paths': runtime,
                  'runtime_file_hashes': {k: sha(runtime[k]) for k in ['ffmpeg', 'ffprobe', 'face_detector', 'model_code', 'model_weights', 'face_model']},
                  'torch_version': torch.__version__, 'numpy_version': np.__version__,
                  'device': 'cpu', 'threads': 4}
    manifest = out / 'evaluation_provenance.json'
    if manifest.exists():
        require(json.loads(manifest.read_text()) == provenance, 'Existing analysis cache provenance differs; use a new output directory')
    else:
        write_json(manifest, provenance)
    probe, pts = video_metadata(runtime, film)
    expected_frames = 2160 if args.mode == 'final' else clips[args.clip_id]['required_frames']
    require(len(pts) == expected_frames, f'Expected {expected_frames} actual frames, received {len(pts)}')
    write_json(out / 'probe.json', probe)
    write_json(out / 'video_presentation_times.json', [[float(t), float(d)] for t, d in pts])
    decoded, sr, blocks, audio_meta = decode_audio(runtime, film, out, probe)
    extractor = FeatureExtractor(runtime)
    csv_rows, clip_reports, errors = [], [], []
    for ident, clip in selected.items():
        media_start = 0 if args.mode == 'candidate' else clip['start_frame']
        clip_out = out / ident
        try:
            visual, audio, vv, av, segment_audio = extractor.extract(film, clip, media_start, pts, decoded, sr, blocks, clip_out)
            def rms(lo, hi):
                signal = segment_audio[round(lo * sr):round(hi * sr)]
                return float(np.sqrt(np.mean(signal.astype(np.float64) ** 2))) if len(signal) else 0.0
            rows = evaluate_windows(visual, audio, gate['windows'][ident], vv, av, rms)
            feature_hash = sha(clip_out / 'features.npz')
        except Exception as exc:
            reason = f'{type(exc).__name__}: {exc}'
            errors.append({'clip': ident, 'reason': reason})
            rows = [{**window, 'status': 'unresolved', 'delay_ms': None, 'abs_delay_ms': None,
                     'count': 0, 'search_boundary': False, 'flags': [], 'reason': reason}
                    for window in gate['windows'][ident]]
            feature_hash = None
        media_origin = float(pts[media_start][0])
        for index, row in enumerate(rows):
            csv_rows.append({'clip': ident, 'window_index': index,
                             'timeline_start_seconds': clip['start_frame'] / 24 + row['start'],
                             'timeline_end_seconds': clip['start_frame'] / 24 + row['end'],
                             'actual_media_start_seconds': media_origin + row['start'],
                             'actual_media_end_seconds': media_origin + row['end'], **row})
        report = {'clip': ident, 'features_sha256': feature_hash, **summarize(rows), 'rows': rows}
        clip_reports.append(report)
        write_json(out / f'{ident}_gate_report.json', report)
        print(ident, json.dumps({key: report[key] for key in ['mean_abs_ms', 'max_abs_ms', 'unresolved_window_count']}, ensure_ascii=False), flush=True)
        if not args.keep_analysis_frames and (clip_out / 'frames').exists():
            shutil.rmtree(clip_out / 'frames')
    expected_windows = sum(len(gate['windows'][ident]) for ident in selected)
    require(len(csv_rows) == expected_windows, 'An original frozen window was lost')
    require(sha(film) == digest and sha(args.gate) == args.gate_sha256 and sha(args.timeline) == provenance['timeline_sha256'],
            'Film or frozen configuration changed while measuring')
    summary = {'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'mode': args.mode,
               'scope': 'complete_final_export_all_original_windows' if args.mode == 'final' else 'one_complete_shot_screening_only',
               'film': str(film), 'sha256': digest, 'provenance': str(manifest),
               'video_frames': len(pts), 'actual_duration_seconds': float(pts[-1][0] + pts[-1][1] - pts[0][0]),
               'actual_video_start_pts_seconds': float(pts[0][0]), 'actual_audio_first_pts_seconds': audio_meta['first_decoded_pts_seconds'],
               'frozen_window_count': gate['window_count'], **summarize(csv_rows, full_film=args.mode == 'final'),
               'weighted_within_clip_mean_abs_deviation_ms': (
                   sum(r['window_count'] * r['mean_abs_deviation_from_median_ms'] for r in clip_reports) / expected_windows
                   if all(r['mean_abs_deviation_from_median_ms'] is not None for r in clip_reports) else None),
               'clips': clip_reports, 'errors': errors, 'wall_seconds': time.monotonic() - began,
               'method': {'model': 'Established local SyncNet v2', 'model_weights_sha256': EXPECTED_WEIGHTS,
                          'analysis_fps': 25, 'audio_hz': 16000, 'window_seconds': 1.6, 'window_step_seconds': 1,
                          'shift_step_ms': 40, 'search_range_ms': [-320, 320],
                          'distance': 'mean(pairwise_distance(visual[i], audio[i - shift])) over shared fixed support',
                          'signed_delay': 'Positive suggests audio later; negative earlier. Diagnostic only: no shift applied.',
                          'audio': 'Actual AAC decoder samples aligned using their original absolute PTS; no separate WAV input',
                          'picture': 'Actual presentation frames sampled nearest the unchanged 25 fps analysis grid',
                          'window_selection_or_exclusions': False, 'media_modified': False},
               'independent_checks': {'audio_integrity_vs_approved_master': 'not_performed_by_this_tool',
                                      'complete_pronunciation': 'not_performed_by_this_tool',
                                      'continuous_native_source_frames': 'not_proven_by_export_cadence',
                                      'motion_and_actual_seams': 'not_performed_by_this_tool',
                                      'realtime_audiovisual_listening': 'not_performed_by_this_tool',
                                      'user_acceptance': 'not_inferred'},
               'limitations': ['Model scores on a 40 ms grid are estimates, not phoneme or perceptual ground truth.',
                              'Boundary, missing-face and insufficient-support windows remain unresolved and prevent a full gate pass.',
                              'Additional first-word, last-word and seam review is required outside the original model windows.',
                              'Candidate scores cannot replace a fresh complete final AAC/PTS evaluation.',
                              'No per-Gxx mean <60 ms condition is imposed.']}
    calibration_status = calibration(config, runtime['method_fingerprint'])
    summary['calibration'] = calibration_status
    summary['uncalibrated_timing_gate_pass'] = summary['timing_gate_pass']
    if args.mode == 'final':
        summary['timing_gate_pass'] = bool(summary['timing_gate_pass'] and calibration_status['valid'])
    write_json(out / 'gate_report.json', summary)
    fields = ['clip', 'window_index', 'timeline_start_seconds', 'timeline_end_seconds',
              'actual_media_start_seconds', 'actual_media_end_seconds', 'start', 'end', 'status',
              'delay_ms', 'abs_delay_ms', 'count', 'search_boundary', 'zero_distance', 'best_distance',
              'median_minus_best_distance', 'audio_rms', 'reason', 'flags']
    with (out / 'window_offsets.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(csv_rows)
    print('RESULT', json.dumps({key: summary[key] for key in ['mode', 'film', 'window_count', 'mean_abs_ms', 'max_abs_ms',
                                                            'unresolved_window_count', 'timing_gate_pass', 'wall_seconds']}, ensure_ascii=False), flush=True)
    check = summary['timing_gate_pass'] if args.mode == 'final' else summary['maximum_window_gate_pass']
    return 3 if args.fail_on_gate and not check else 0


if __name__ == '__main__':
    raise SystemExit(main())
