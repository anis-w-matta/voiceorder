import sys
from pathlib import Path

from app.services.gemini_transcriber import GeminiTranscriber

AUDIO_EXT = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".mp4", ".aac",
             ".wav", ".webm", ".amr", ".3gp", ".flac"}

if len(sys.argv) < 2:
    print("usage: python transcribe_file.py <audio_path_or_dir> [more...]")
    sys.exit(1)

paths = []
for arg in sys.argv[1:]:
    p = Path(arg)
    if p.is_dir():
        paths.extend(sorted(f for f in p.iterdir() if f.suffix.lower() in AUDIO_EXT))
    else:
        paths.append(p)

stt = GeminiTranscriber()

for path in paths:
    print(f"=== {path} ===")
    try:
        tr = stt.transcribe(str(path))
    except Exception as e:
        print(f"FAILED: {e}\n")
        continue

    print(f"language: {tr.language} ({', '.join(tr.languages) or 'n/a'})")
    print(f"duration: {tr.duration:.1f}s  confidence: {tr.confidence:.3f}")
    print(f"text: {tr.text}")
    for seg in tr.segments:
        print(f"[{seg.start:6.2f}-{seg.end:6.2f}] ({seg.avg_logprob:+.2f}) {seg.text}")
    print()
