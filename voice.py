"""Offline voice worker: explicit microphone, signal meter, streaming trigger."""
import argparse
import array
import json
import math
import os
import selectors
import signal
import subprocess
import sys
import time

from core import PhraseTrigger, normalized_words


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}), flush=True)


def pcm_level(audio, gain=1):
    samples = array.array('h', audio)
    if sys.byteorder != 'little':
        samples.byteswap()
    rms = math.sqrt(sum(s * s for s in samples) / max(1, len(samples)))
    db = 20 * math.log10(max(rms, 0.032768) / 32768)
    if gain != 1:
        samples = array.array('h', (max(-32768, min(32767, int(s * gain))) for s in samples))
        if sys.byteorder != 'little':
            samples.byteswap()
        audio = samples.tobytes()
    return audio, db


def make_recognizer(model, phrase):
    from vosk import KaldiRecognizer
    words = normalized_words(phrase)
    missing = [word for word in words if model.vosk_model_find_word(word) < 0]
    if not words or missing:
        raise ValueError('Choose a phrase with known English words. Unknown: ' + ', '.join(missing))
    grammar = [' '.join(words), '[unk]']
    if words == ['mochi', 'clip', 'that']:
        for variant in ['mo chi clip that', 'mo chee clip that']:
            if all(model.vosk_model_find_word(word) >= 0 for word in variant.split()):
                grammar.append(variant)
    recognizer = KaldiRecognizer(model, 16000, json.dumps(grammar))
    recognizer.SetWords(True)
    return recognizer


def main(model_path, target='auto', gain=3, phrase='mochi clip that'):
    from vosk import Model, SetLogLevel
    SetLogLevel(-1)
    recognizer = make_recognizer(Model(model_path), phrase)
    trigger = PhraseTrigger(phrase)
    capture = None
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        capture = subprocess.Popen(['pw-record', '--raw', '--rate', '16000',
            '--channels', '1', '--format', 's16', '--latency', '50ms',
            '--target', target, '-'], stdout=subprocess.PIPE, stderr=sys.stderr, bufsize=0)
        listening = False
        last_level = 0
        last_text = ''
        pending = b''
        with selectors.DefaultSelector() as selector:
            selector.register(capture.stdout, selectors.EVENT_READ)
            while True:
                if not selector.select(timeout=8):
                    raise RuntimeError('No audio arrived. Check that the selected microphone is connected.')
                data = os.read(capture.stdout.fileno(), 3200)
                if not data:
                    raise RuntimeError('Microphone stream ended. Check the selected input and retry.')
                pending += data
                aligned = len(pending) // 2 * 2
                if not aligned:
                    continue
                raw, pending = pending[:aligned], pending[aligned:]
                audio, db = pcm_level(raw, gain)
                now = time.monotonic()
                if not listening:
                    emit('listening')
                    listening = True
                if now - last_level >= .12:
                    emit('level', db=round(db, 1))
                    last_level = now
                final = recognizer.AcceptWaveform(audio)
                result = json.loads(recognizer.Result() if final else recognizer.PartialResult())
                text = result.get('text' if final else 'partial', '')
                if text and (text != last_text or final):
                    emit('heard', text=text, final=bool(final))
                    last_text = text
                if trigger.feed(text, now, bool(final)):
                    emit('clip')
    finally:
        if capture is not None:
            capture.terminate()
            try:
                capture.wait(timeout=3)
            except subprocess.TimeoutExpired:
                capture.kill()
                capture.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('model')
    parser.add_argument('--target', default='auto')
    parser.add_argument('--gain', type=float, default=3)
    parser.add_argument('--phrase', default='mochi clip that')
    args = parser.parse_args()
    try:
        main(args.model, args.target, min(8, max(1, args.gain)), args.phrase)
    except Exception as error:
        emit('error', message=str(error))
        sys.exit(1)
