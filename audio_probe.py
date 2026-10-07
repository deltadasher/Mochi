"""List physical/virtual input sources; exclude desktop monitor sources."""
import json
import subprocess

def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=4).stdout
try:
    default = run('pactl', 'get-default-source').strip()
    sources = json.loads(run('pactl', '-f', 'json', 'list', 'sources'))
    inputs = []
    for source in sources:
        if source.get('monitor_source') or source['name'].endswith('.monitor'):
            continue
        volumes = [v['value_percent'] for v in source.get('volume', {}).values()]
        inputs.append({'name': source['name'], 'label': source['description'],
                       'muted': source.get('mute', False), 'volume': volumes[0] if volumes else '?',
                       'default': source['name'] == default})
    print(json.dumps({'inputs': inputs, 'default': default}))
except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
    print(json.dumps({'error': str(error), 'inputs': []}))
