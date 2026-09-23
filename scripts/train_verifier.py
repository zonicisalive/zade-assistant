"""Train a speaker-specific verifier for the zade wake word.
Record ~20 clips of you saying "Zade" into pos/ and ~20 clips of other speech into neg/ (16 kHz mono WAV).
Usage: uv run python scripts/train_verifier.py pos neg"""
import pathlib
import sys

import openwakeword

pos, neg = (sorted(str(p) for p in pathlib.Path(d).glob("*.wav")) for d in sys.argv[1:3])
out = pathlib.Path("~/.local/share/zade/zade_verifier.pkl").expanduser()
openwakeword.train_custom_verifier(
    positive_reference_clips=pos,
    negative_reference_clips=neg,
    output_path=str(out),
    model_name=str(pathlib.Path("~/.local/share/zade/zade.onnx").expanduser()),
)
print("wrote", out, '- set wake.verifier = "~/.local/share/zade/zade_verifier.pkl"')
