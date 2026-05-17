import os, uuid, subprocess, shutil, threading
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB
UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ALLOWED_EXT = {'.mp3', '.wav', '.mp4', '.flac', '.ogg', '.m4a'}
jobs = {}

# Always return JSON errors, never Flask HTML pages
@app.errorhandler(RequestEntityTooLarge)
def too_large(e):
    return jsonify(error='File too large. Max 500 MB.'), 413

@app.errorhandler(400)
def bad_req(e):
    return jsonify(error=str(e)), 400

@app.errorhandler(404)
def not_found(e):
    return jsonify(error='Not found'), 404

@app.errorhandler(500)
def srv_err(e):
    return jsonify(error=f'Server error: {e}'), 500

def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXT

def safe_name(filename):
    """secure_filename strips unicode -> may return ''. Fall back to uuid."""
    name = secure_filename(filename)
    if not name:
        suffix = Path(filename).suffix.lower() or '.audio'
        name = 'upload' + suffix
    return name

def set_job(job_id, **kw):
    jobs[job_id].update(kw)

def run_separation(job_id, audio_path, want_midi, out_format):
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Step 1: normalise to WAV
        set_job(job_id, status='processing', step='Converting audio...', progress=5)
        wav_path = job_dir / 'input.wav'
        r = subprocess.run(
            ['ffmpeg', '-y', '-i', str(audio_path),
             '-ac', '2', '-ar', '44100', '-vn', str(wav_path)],
            capture_output=True, text=True
        )
        if r.returncode != 0:
            raise RuntimeError('ffmpeg conversion failed: ' + r.stderr[-300:])

        # Step 2: Demucs separation
        set_job(job_id, step='Separating stems (this may take 1-3 min)...', progress=15)
        demucs_out = job_dir / 'demucs'
        demucs_out.mkdir(exist_ok=True)

        # Try htdemucs (4-stem: vocals, drums, bass, other)
        r2 = subprocess.run(
            ['python3', '-m', 'demucs', '--name', 'htdemucs',
             '-o', str(demucs_out), str(wav_path)],
            capture_output=True, text=True
        )
        stem_wavs = list(demucs_out.rglob('*.wav'))

        # Fallback: mdx_extra or default model
        if not stem_wavs or r2.returncode != 0:
            set_job(job_id, step='Trying fallback model...', progress=20)
            shutil.rmtree(str(demucs_out), ignore_errors=True)
            demucs_out.mkdir(exist_ok=True)
            r3 = subprocess.run(
                ['python3', '-m', 'demucs',
                 '-o', str(demucs_out), str(wav_path)],
                capture_output=True, text=True
            )
            stem_wavs = list(demucs_out.rglob('*.wav'))

        if not stem_wavs:
            raise RuntimeError(
                'Demucs produced no stems. '
                'stderr: ' + (r2.stderr or r3.stderr or '')[-400:]
            )

        set_job(job_id, step='Collecting stems...', progress=60)

        # Map demucs stem names -> UI names
        STEM_MAP = {
            'vocals': 'vocals',
            'bass':   'bass',
            'drums':  'melody',
            'other':  'others',
            'no_vocals': 'others',
            'accompaniment': 'others',
        }
        stems_found = {}
        for f in stem_wavs:
            key = f.stem.lower()
            for label in STEM_MAP:
                if label in key:
                    ui = STEM_MAP[label]
                    if ui not in stems_found:
                        stems_found[ui] = f
                    break

        if not stems_found:
            # last resort: name them sequentially
            names = ['vocals', 'bass', 'melody', 'others']
            for i, f in enumerate(stem_wavs):
                stems_found[names[i % len(names)]] = f

        # Step 3: encode to output format
        set_job(job_id, step='Encoding stems...', progress=70)
        ext = '.' + out_format
        stem_files = {}
        for ui_name, src in stems_found.items():
            dst = job_dir / (ui_name + ext)
            if out_format == 'wav':
                shutil.copy(str(src), str(dst))
            else:
                subprocess.run(
                    ['ffmpeg', '-y', '-i', str(src), str(dst)],
                    capture_output=True
                )
            stem_files[ui_name] = dst.name

        # Step 4: optional MIDI
        midi_file = None
        if want_midi:
            set_job(job_id, step='Extracting MIDI...', progress=82)
            try:
                import librosa, pretty_midi, numpy as np
                src = stems_found.get('melody') or stems_found.get('vocals') or list(stems_found.values())[0]
                y, sr = librosa.load(str(src), sr=22050, mono=True)
                f0, voiced, _ = librosa.pyin(
                    y, fmin=librosa.note_to_hz('C2'),
                    fmax=librosa.note_to_hz('C7'),
                    sr=sr, hop_length=512, fill_na=None
                )
                times = librosa.frames_to_time(
                    np.arange(len(f0 if f0 is not None else [])),
                    sr=sr, hop_length=512
                )
                pm = pretty_midi.PrettyMIDI(initial_tempo=120)
                inst = pretty_midi.Instrument(program=0)
                cur, t0 = None, 0
                for i, (freq, is_v) in enumerate(zip(
                    f0 if f0 is not None else [],
                    voiced if voiced is not None else []
                )):
                    t = times[i]
                    if is_v and freq is not None and not np.isnan(float(freq)):
                        mn = int(round(librosa.hz_to_midi(float(freq))))
                        mn = max(0, min(127, mn))
                        if cur != mn:
                            if cur is not None and t - t0 > 0.05:
                                inst.notes.append(pretty_midi.Note(80, cur, t0, t))
                            cur, t0 = mn, t
                    else:
                        if cur is not None and t - t0 > 0.05:
                            inst.notes.append(pretty_midi.Note(80, cur, t0, t))
                        cur = None
                pm.instruments.append(inst)
                mp = job_dir / 'melody.mid'
                pm.write(str(mp))
                midi_file = mp.name
            except Exception as me:
                set_job(job_id, midi_error=str(me))

        set_job(job_id, status='done', step='Complete!', progress=100,
                stems=stem_files, midi=midi_file, job_dir=str(job_dir))

    except Exception as e:
        set_job(job_id, status='error', error=str(e), progress=0)
    finally:
        try:
            audio_path.unlink()
        except Exception:
            pass


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload():
    try:
        if 'file' not in request.files:
            return jsonify(error='No file attached.'), 400
        f = request.files['file']
        if not f.filename:
            return jsonify(error='Filename is empty.'), 400
        if not allowed_file(f.filename):
            ext = Path(f.filename).suffix or '(none)'
            return jsonify(error=f'Format "{ext}" not supported. Use MP3, WAV, MP4, FLAC, OGG or M4A.'), 400

        want_midi  = request.form.get('midi', 'false').lower() == 'true'
        out_format = request.form.get('format', 'wav').lower()
        if out_format not in ('wav', 'mp4', 'mp3'):
            out_format = 'wav'

        job_id = str(uuid.uuid4())
        jobs[job_id] = {'status': 'queued', 'step': 'Queued', 'progress': 0}

        fname = safe_name(f.filename)
        audio_path = UPLOAD_DIR / f'{job_id}_{fname}'
        f.save(str(audio_path))

        t = threading.Thread(
            target=run_separation,
            args=(job_id, audio_path, want_midi, out_format),
            daemon=True
        )
        t.start()
        return jsonify(job_id=job_id)

    except RequestEntityTooLarge:
        return jsonify(error='File too large. Max 500 MB.'), 413
    except Exception as e:
        return jsonify(error=f'Upload error: {str(e)}'), 500


@app.route('/status/<job_id>')
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify(error='Job not found'), 404
    return jsonify(job)


@app.route('/download/<job_id>/<filename>')
def download(job_id, filename):
    job = jobs.get(job_id)
    if not job or job.get('status') != 'done':
        return jsonify(error='Not ready'), 404
    job_dir = Path(job['job_dir'])
    safe = secure_filename(filename)
    fpath = job_dir / safe
    if not fpath.exists():
        return jsonify(error='File not found'), 404
    return send_file(str(fpath), as_attachment=True, download_name=safe)


if __name__ == '__main__':
    app.run(debug=False, port=7860, host='0.0.0.0')
