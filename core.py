"""Mochi's recorder protocol and speech matching. No shell command execution."""
import json
import re
import socket
from pathlib import Path


def recorder_command(folder, socket_path, source="portal", microphone="default_input"):
    mic = microphone if microphone == "default_input" else "device:" + microphone
    return ["gpu-screen-recorder", "-w", source, "-f", "60", "-c", "mp4",
            "-k", "h264", "-ac", "aac", "-a", "default_output|" + mic,
            "-r", "60", "-replay-storage", "ram", "-o", str(folder),
            "-ipc", str(socket_path)]


def request(socket_path, name, data=None, timeout=30):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(socket_path))
        client.sendall((json.dumps({"id": 1, "name": name, "data": data}) + "\n").encode())
        response = bytearray()
        while b"\n" not in response:
            chunk = client.recv(4096)
            if not chunk:
                raise RuntimeError("Recorder closed the connection without confirming the save.")
            response.extend(chunk)
            if len(response) > 65536:
                raise RuntimeError("Unexpected recorder response.")
    result = json.loads(response.split(b"\n", 1)[0])
    if result.get("result") != "ok":
        raise RuntimeError(str(result.get("data", "Recorder rejected the request.")))
    return result.get("data")


def normalized_words(text):
    return re.findall(r"[a-z]+", text.lower())


def is_clip_phrase(text, phrase="mochi clip that"):
    words = normalized_words(text)
    target = normalized_words(phrase)
    alternatives = [target]
    if target == ["mochi", "clip", "that"]:
        alternatives += [["mo", "chi", "clip", "that"], ["mo", "chee", "clip", "that"]]
    return any(words[i:i + len(t)] == t for t in alternatives if t
               for i in range(len(words) - len(t) + 1))


class PhraseTrigger:
    """Trigger stable partial results once per utterance, with a cooldown."""
    def __init__(self, phrase="mochi clip that", cooldown=4):
        self.phrase = phrase
        self.cooldown = cooldown
        self.last_fire = float('-inf')
        self.candidate_since = None
        self.fired = False

    def feed(self, text, now, final=False):
        matched = is_clip_phrase(text, self.phrase)
        if not matched:
            self.candidate_since = None
        elif self.candidate_since is None:
            self.candidate_since = now
        stable = matched and (final or now - self.candidate_since >= 0.20)
        fire = stable and not self.fired and now - self.last_fire >= self.cooldown
        if fire:
            self.fired = True
            self.last_fire = now
        if final:
            self.fired = False
            self.candidate_since = None
        return fire


def validate_clip(path):
    if not isinstance(path, str) or not Path(path).is_file() or Path(path).stat().st_size == 0:
        raise RuntimeError("Recorder did not return a completed clip file.")
    return path
