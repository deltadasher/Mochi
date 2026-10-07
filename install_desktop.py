#!/usr/bin/env python3
"""Register a source installation with Linux application launchers, without sudo."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

APP_ID = 'io.github.deltadasher.Mochi'


def escape_value(value):
    return (str(value).replace('\\', '\\\\').replace('\n', '\\n')
            .replace('\r', '\\r').replace('\t', '\\t'))


def exec_argument(value):
    # Exec quoting is applied before desktop-file string escaping.
    value = str(value).replace('%', '%%')
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, '\\' + character)
    return escape_value('"' + value + '"')


def desktop_entry(app_dir):
    launcher = app_dir / 'run.sh'
    return '\n'.join([
        '[Desktop Entry]', 'Type=Application', 'Name=Mochi',
        'GenericName=Gameplay Clip Recorder',
        'Comment=Save your last 30 or 60 seconds with a hotkey or your voice',
        # Pass the script as an argument so unusual checkout names are safe.
        'Exec=/usr/bin/bash -- ' + exec_argument(launcher),
        'TryExec=' + escape_value(launcher),
        'Path=' + escape_value(app_dir),
        'Icon=' + APP_ID, 'Terminal=false', 'StartupNotify=false',
        'StartupWMClass=Mochi', 'Categories=AudioVideo;Recorder;',
        'Keywords=clip;clipping;replay;record;recorder;gameplay;gaming;voice;screen;',
        '',
    ])


def data_home():
    configured = os.environ.get('XDG_DATA_HOME')
    if configured and Path(configured).is_absolute():
        return Path(configured)
    return Path.home() / '.local' / 'share'


def install(app_dir, destination=None):
    app_dir = Path(app_dir).resolve()
    launcher = app_dir / 'run.sh'
    icon = app_dir / 'assets' / 'mochi-app.svg'
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise ValueError(f'Missing executable launcher: {launcher}')
    if not icon.is_file():
        raise ValueError(f'Missing application icon: {icon}')
    if not (app_dir / '.venv' / 'bin' / 'python').is_file():
        raise ValueError('Run ./setup.sh first to create the Python environment.')
    destination = Path(destination) if destination is not None else data_home()
    applications = destination / 'applications'
    icons = destination / 'icons' / 'hicolor' / 'scalable' / 'apps'
    applications.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(icon, icons / (APP_ID + '.svg'))
    entry = applications / (APP_ID + '.desktop')
    entry.write_text(desktop_entry(app_dir), encoding='utf-8')
    entry.chmod(0o644)
    updater = shutil.which('update-desktop-database')
    if updater:
        subprocess.run([updater, str(applications)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path, default=Path(__file__).resolve().parent,
                        help='Existing Mochi source installation to register')
    args = parser.parse_args()
    try:
        entry = install(args.app_dir)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Mochi launcher: {error}\n')
    print(f'Installed {entry}\nSearch for Mochi in your application launcher.')


if __name__ == '__main__':
    main()
