"""Bounded media inspection helper, run off the UI thread."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

path = Path(sys.argv[1])
cache = Path(sys.argv[2]); cache.mkdir(parents=True, exist_ok=True)
key = hashlib.sha256(f'{path}:{path.stat().st_mtime_ns}:{path.stat().st_size}'.encode()).hexdigest()
thumb = cache / (key + '.jpg')
result = {'path': str(path)}
try:
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'json', str(path)], capture_output=True, text=True, timeout=8, check=True)
    result['duration'] = float(json.loads(probe.stdout)['format']['duration'])
    if not thumb.exists():
        subprocess.run(['ffmpeg', '-v', 'error', '-ss', '0', '-i', str(path), '-frames:v', '1',
            '-vf', 'scale=480:-2', '-threads', '1', '-y', str(thumb)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12, check=True)
    result['thumbnail'] = str(thumb)
except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
    result['error'] = str(error)
print(json.dumps(result))
