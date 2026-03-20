"""Speaker diarization using pyannote.audio — identify who speaks when."""

import time
from pathlib import Path

import numpy as np
import torch

_pipeline = None


def _load_audio_pyav(audio_path: str | Path, sample_rate: int = 16000) -> torch.Tensor:
    """Load audio file using PyAV (supports m4a/aac, mp3, wav, etc.)."""
    import av

    container = av.open(str(audio_path))
    audio_stream = container.streams.audio[0]

    frames = []
    resampler = av.audio.resampler.AudioResampler(
        format="s16", layout="mono", rate=sample_rate
    )
    for frame in container.decode(audio_stream):
        resampled = resampler.resample(frame)
        for r in resampled:
            arr = r.to_ndarray().flatten().astype(np.float32) / 32768.0
            frames.append(arr)
    container.close()

    waveform = np.concatenate(frames)
    return torch.from_numpy(waveform).unsqueeze(0)  # (1, samples)


def get_diarization_pipeline():
    """Load pyannote diarization pipeline (lazy, loads once)."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    import os
    hf_token = os.getenv("HF_TOKEN", "")
    if not hf_token:
        raise ValueError("HF_TOKEN не задан в .env (нужен для pyannote.audio)")

    print("Загрузка модели диаризации (pyannote)...")
    from pyannote.audio import Pipeline

    _pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        token=hf_token,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    _pipeline = _pipeline.to(device)
    print(f"Модель диаризации загружена на {device}.")
    return _pipeline


def diarize_audio(audio_path: str | Path) -> list[dict]:
    """Run speaker diarization on audio file.

    Returns list of segments: [{"start": float, "end": float, "speaker": str}, ...]
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Аудиофайл не найден: {audio_path}")

    # Use all available CPU threads for faster diarization (fallback when GPU not available)
    import os as _os
    torch.set_num_threads(_os.cpu_count() or 16)

    pipeline = get_diarization_pipeline()

    print(f"Диаризация: {audio_path.name}")
    start_time = time.time()

    waveform = _load_audio_pyav(audio_path)
    audio_input = {"waveform": waveform, "sample_rate": 16000}

    result = pipeline(audio_input)

    # pyannote 4.x: result has .speaker_diarization attribute
    annotation = getattr(result, "speaker_diarization", result)

    segments = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        segments.append({
            "start": turn.start,
            "end": turn.end,
            "speaker": speaker,
        })

    elapsed = time.time() - start_time
    speakers = sorted(set(s["speaker"] for s in segments))
    print(f"Диаризация завершена за {elapsed:.1f}с | Спикеров: {len(speakers)}")

    return segments


def merge_whisper_with_speakers(
    whisper_segments: list[dict],
    diarization_segments: list[dict],
) -> list[dict]:
    """Merge whisper transcript segments with speaker labels.

    For each whisper segment, finds the speaker with most overlap.
    Returns list of: [{"start", "end", "text", "speaker"}, ...]
    """
    merged = []
    for wseg in whisper_segments:
        ws, we = wseg["start"], wseg["end"]

        # Find speaker with maximum overlap
        best_speaker = "UNKNOWN"
        best_overlap = 0.0

        for dseg in diarization_segments:
            ds, de = dseg["start"], dseg["end"]
            overlap = max(0, min(we, de) - max(ws, ds))
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = dseg["speaker"]

        merged.append({
            "start": wseg["start"],
            "end": wseg["end"],
            "text": wseg["text"],
            "speaker": best_speaker,
        })

    return merged


def format_diarized_transcript(
    merged_segments: list[dict],
    speaker_names: dict[str, str] | None = None,
) -> str:
    """Format merged segments as readable transcript with speaker labels.

    speaker_names: optional mapping like {"SPEAKER_00": "Владимир Геннадьевич"}
    """
    if speaker_names is None:
        speaker_names = {}

    lines = []
    for seg in merged_segments:
        start_min = int(seg["start"] // 60)
        start_sec = int(seg["start"] % 60)
        speaker = speaker_names.get(seg["speaker"], seg["speaker"])
        lines.append(f"[{start_min:02d}:{start_sec:02d}] **{speaker}:** {seg['text']}")

    return "\n".join(lines)


def diarize_audio_with_waveform(
    audio_path: str | Path,
) -> tuple[list[dict], "torch.Tensor"]:
    """Like diarize_audio(), but also returns the raw waveform tensor.

    Returns (segments, waveform) where waveform is (1, num_samples) at 16kHz.
    Use this when you need to extract speaker embeddings from the same audio.
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Аудиофайл не найден: {audio_path}")

    import os as _os
    torch.set_num_threads(_os.cpu_count() or 16)

    pipeline = get_diarization_pipeline()

    print(f"Диаризация: {audio_path.name}")
    start_time = time.time()

    waveform = _load_audio_pyav(audio_path)
    audio_input = {"waveform": waveform, "sample_rate": 16000}

    result = pipeline(audio_input)
    annotation = getattr(result, "speaker_diarization", result)

    segments = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        segments.append({
            "start": turn.start,
            "end": turn.end,
            "speaker": speaker,
        })

    elapsed = time.time() - start_time
    speakers = sorted(set(s["speaker"] for s in segments))
    print(f"Диаризация завершена за {elapsed:.1f}с | Спикеров: {len(speakers)}")

    return segments, waveform


def compute_speaker_stats(
    diarization_segments: list[dict],
) -> dict[str, dict]:
    """Compute speaking duration and segment count per speaker label.

    Returns {speaker_label: {"duration_sec": float, "segments_count": int}}
    """
    stats: dict[str, dict] = {}
    for seg in diarization_segments:
        sp = seg["speaker"]
        if sp not in stats:
            stats[sp] = {"duration_sec": 0.0, "segments_count": 0}
        stats[sp]["duration_sec"] += seg["end"] - seg["start"]
        stats[sp]["segments_count"] += 1
    return stats


def get_speakers_list(merged_segments: list[dict]) -> list[str]:
    """Get sorted list of unique speaker IDs from merged segments."""
    return sorted(set(seg["speaker"] for seg in merged_segments))


def build_speakers_header(
    speakers: list[str],
    speaker_names: dict[str, str] | None = None,
) -> str:
    """Build a participant list section for the markdown output."""
    if speaker_names is None:
        speaker_names = {}

    lines = ["## Участники (заполни имена)"]
    for sp in speakers:
        name = speaker_names.get(sp, "???")
        lines.append(f"- {sp} = {name}")
    return "\n".join(lines)
