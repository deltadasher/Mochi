"""Download the official small English model; never upload audio."""
from pathlib import Path
import tempfile
import urllib.request
import zipfile

root = Path(__file__).resolve().parent / "models"
name = "vosk-model-small-en-us-0.15"
if not (root / name / "am" / "final.mdl").is_file():
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as temp:
        archive = Path(temp) / "model.zip"
        print("Downloading offline English voice model (about 40 MB)…", flush=True)
        urllib.request.urlretrieve(f"https://alphacephei.com/vosk/models/{name}.zip", archive)
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                target = (Path(temp) / entry.filename).resolve()
                if not target.is_relative_to(Path(temp).resolve()):
                    raise ValueError("Unsafe model archive path")
            package.extractall(temp)
        (Path(temp) / name).rename(root / name)
    print("Voice model ready.")
