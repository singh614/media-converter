import os
import subprocess
import uuid
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template
from werkzeug.utils import secure_filename

app = Flask(__name__)

UPLOAD_DIR = Path('uploads')
OUTPUT_DIR = Path('outputs')
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

AUDIO_EXTS = {'mp3','aac','flac','wav','ogg','m4a','opus','wma','aiff','alac','ac3','amr','mp2'}
VIDEO_EXTS = {'mp4','mkv','avi','mov','webm','flv','m4v','wmv','ts','mpg','mpeg','3gp','gif','rm'}
LOSSLESS_AUDIO = {'flac','wav','aiff','alac'}

AUDIO_CODEC_MAP = {
    'mp3':  ['-codec:a', 'libmp3lame'],
    'aac':  ['-codec:a', 'aac'],
    'm4a':  ['-codec:a', 'aac'],
    'flac': ['-codec:a', 'flac'],
    'wav':  ['-codec:a', 'pcm_s16le'],
    'ogg':  ['-codec:a', 'libvorbis'],
    'opus': ['-codec:a', 'libopus'],
    'wma':  ['-codec:a', 'wmav2'],
    'aiff': ['-codec:a', 'pcm_s16be'],
    'alac': ['-codec:a', 'alac'],
    'ac3':  ['-codec:a', 'ac3'],
    'mp2':  ['-codec:a', 'mp2'],
}

VIDEO_CODEC_MAP = {
    'mp4':  ['-c:v', 'libx264'],
    'mkv':  ['-c:v', 'libx264'],
    'avi':  ['-c:v', 'mpeg4'],
    'mov':  ['-c:v', 'libx264'],
    'webm': ['-c:v', 'libvpx-vp9', '-c:a', 'libopus'],
    'flv':  ['-c:v', 'libx264'],
    'm4v':  ['-c:v', 'libx264'],
    'wmv':  ['-c:v', 'wmv2', '-c:a', 'wmav2'],
    'ts':   ['-c:v', 'libx264'],
    'mpg':  ['-c:v', 'mpeg2video', '-c:a', 'mp2'],
    'mpeg': ['-c:v', 'mpeg2video', '-c:a', 'mp2'],
    '3gp':  ['-c:v', 'libx264', '-c:a', 'aac'],
}

HAS_AUDIO_CODEC = {'webm', 'wmv', 'mpg', 'mpeg', '3gp'}


def audio_args(ext, bitrate):
    args = list(AUDIO_CODEC_MAP.get(ext, []))
    if ext not in LOSSLESS_AUDIO:
        args += ['-b:a', bitrate]
    return args


def build_cmd(input_path, output_path, mode, params):
    in_ext = input_path.suffix.lstrip('.').lower()
    out_ext = output_path.suffix.lstrip('.').lower()
    bitrate = params.get('audio_bitrate', '128k')

    if mode == 'extract':
        cmd = ['ffmpeg', '-i', str(input_path), '-y', '-vn']
        cmd += audio_args(out_ext, bitrate)
        cmd.append(str(output_path))
        return cmd

    if mode == 'compress_audio':
        cmd = ['ffmpeg', '-i', str(input_path), '-y', '-vn']
        cmd += audio_args(out_ext, bitrate)
        cmd.append(str(output_path))
        return cmd

    if mode == 'compress_video':
        crf = params.get('crf', '28')
        preset = params.get('preset', 'medium')
        resolution = params.get('resolution', '')
        cmd = ['ffmpeg', '-i', str(input_path), '-y',
               '-c:v', 'libx264', '-crf', str(crf), '-preset', preset]
        if resolution:
            cmd += ['-vf', f'scale={resolution}:-2']
        cmd += ['-c:a', 'aac', '-b:a', bitrate]
        cmd.append(str(output_path))
        return cmd

    # convert mode
    if out_ext == 'gif':
        fps = params.get('gif_fps', '10')
        scale = params.get('gif_scale', '480')
        return [
            'ffmpeg', '-i', str(input_path), '-y',
            '-filter_complex',
            f'[0:v]fps={fps},scale={scale}:-1:flags=lanczos,split[s0][s1];'
            f'[s0]palettegen=max_colors=256[p];[s1][p]paletteuse=dither=bayer',
            '-loop', '0', str(output_path)
        ]

    in_is_audio = in_ext in AUDIO_EXTS
    out_is_audio = out_ext in AUDIO_EXTS

    cmd = ['ffmpeg', '-i', str(input_path), '-y']

    if out_is_audio:
        cmd += ['-vn']
        cmd += audio_args(out_ext, bitrate)
    else:
        v_args = list(VIDEO_CODEC_MAP.get(out_ext, ['-c:v', 'libx264']))
        cmd += v_args
        if out_ext not in HAS_AUDIO_CODEC and not in_is_audio:
            cmd += ['-c:a', 'aac', '-b:a', bitrate]

    cmd.append(str(output_path))
    return cmd


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Empty filename'}), 400

    mode = request.form.get('mode', 'convert')
    output_format = request.form.get('output_format', '').lower().strip()
    params = {
        'audio_bitrate': request.form.get('audio_bitrate', '128k'),
        'crf':           request.form.get('crf', '28'),
        'preset':        request.form.get('preset', 'medium'),
        'resolution':    request.form.get('resolution', ''),
        'gif_fps':       request.form.get('gif_fps', '10'),
        'gif_scale':     request.form.get('gif_scale', '480'),
    }

    original_name = secure_filename(f.filename)
    in_ext = Path(original_name).suffix.lower().lstrip('.')
    uid = str(uuid.uuid4())[:8]
    input_path = UPLOAD_DIR / f'{uid}.{in_ext}'
    f.save(str(input_path))

    if mode == 'extract':
        out_ext = output_format or 'mp3'
    elif mode == 'compress_audio':
        out_ext = output_format or in_ext or 'mp3'
    elif mode == 'compress_video':
        out_ext = output_format or in_ext or 'mp4'
    else:
        out_ext = output_format or in_ext

    output_path = OUTPUT_DIR / f'{uid}.{out_ext}'
    stem = Path(original_name).stem

    cmd = build_cmd(input_path, output_path, mode, params)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        input_path.unlink(missing_ok=True)

        if result.returncode != 0:
            err = result.stderr
            return jsonify({'error': err[-2000:] if len(err) > 2000 else err}), 500

        size = output_path.stat().st_size
        return jsonify({
            'success': True,
            'file_id': uid,
            'ext': out_ext,
            'download_name': f'{stem}.{out_ext}',
            'size': size,
        })
    except subprocess.TimeoutExpired:
        input_path.unlink(missing_ok=True)
        return jsonify({'error': 'Conversion timed out (2-hour limit)'}), 504
    except FileNotFoundError:
        input_path.unlink(missing_ok=True)
        return jsonify({'error': 'FFmpeg not found. Install it: brew install ffmpeg'}), 500
    except Exception as e:
        input_path.unlink(missing_ok=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/download/<uid>/<filename>')
def download(uid, filename):
    ext = Path(filename).suffix.lstrip('.')
    path = OUTPUT_DIR / f'{uid}.{ext}'
    if not path.exists():
        return jsonify({'error': 'File not found or already downloaded'}), 404

    abs_path = str(path.resolve())

    def cleanup():
        try:
            os.unlink(abs_path)
        except Exception:
            pass

    response = send_file(abs_path, as_attachment=True, download_name=filename)
    response.call_on_close(cleanup)
    return response


@app.route('/api/ffmpeg-check')
def ffmpeg_check():
    try:
        r = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            version = r.stdout.split('\n')[0]
            return jsonify({'available': True, 'version': version})
        return jsonify({'available': False})
    except FileNotFoundError:
        return jsonify({'available': False, 'error': 'FFmpeg not found'})


if __name__ == '__main__':
    import sys
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5, check=True)
        print('FFmpeg found — OK')
    except (FileNotFoundError, subprocess.CalledProcessError):
        print('ERROR: FFmpeg not found. Install it: brew install ffmpeg')
        sys.exit(1)

    print('Media Converter running at http://localhost:5000')
    app.run(debug=False, port=5000, host='127.0.0.1')
