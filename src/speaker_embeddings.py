"""Speaker embedding store: voice fingerprints for automatic speaker identification.

Stores 256-dim voice embeddings (from pyannote/wespeaker) per confirmed speaker.
Compares unknown speakers against the store to suggest identities.
NEVER auto-assigns names — only suggests with confidence scores.
"""

import json
import time
from pathlib import Path

import numpy as np

from src.config import KNOWLEDGE_BASE_DIR

EMBEDDINGS_FILE = KNOWLEDGE_BASE_DIR / "people" / "_speaker_embeddings.json"
SCHEMA_VERSION = 1

# Thresholds: precision over recall
THRESHOLD_SUGGEST = 0.85  # >= this → "Предлагаю: Name (92%)"
THRESHOLD_MAYBE = 0.70    # >= this → "Возможно: Name (78%)"
# < 0.70 → no suggestion

_emb_model = None
_emb_inference = None


# ---------------------------------------------------------------------------
# Embedding model (lazy singleton)
# ---------------------------------------------------------------------------

def _get_embedding_inference():
    """Lazy-load wespeaker embedding model (one-time cost ~2-3s)."""
    global _emb_model, _emb_inference
    if _emb_inference is not None:
        return _emb_inference

    import os
    import torch
    from pyannote.audio import Model, Inference

    hf_token = os.getenv("HF_TOKEN", "")
    if not hf_token:
        raise ValueError("HF_TOKEN не задан — нужен для модели эмбеддингов")

    print("Загрузка модели голосовых эмбеддингов (wespeaker)...")
    _emb_model = Model.from_pretrained(
        "pyannote/wespeaker-voxceleb-resnet34-LM",
        use_auth_token=hf_token,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    _emb_model = _emb_model.to(device)
    _emb_inference = Inference(_emb_model, window="whole")
    print(f"Модель эмбеддингов загружена на {device}.")
    return _emb_inference


# ---------------------------------------------------------------------------
# Store I/O
# ---------------------------------------------------------------------------

def _load_store() -> dict:
    """Load embedding store from disk."""
    if EMBEDDINGS_FILE.exists():
        return json.loads(EMBEDDINGS_FILE.read_text(encoding="utf-8"))
    return {"version": SCHEMA_VERSION, "speakers": {}}


def _save_store(store: dict):
    """Persist store atomically (write tmp → rename)."""
    EMBEDDINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = EMBEDDINGS_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(store, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(EMBEDDINGS_FILE)


# ---------------------------------------------------------------------------
# Embedding extraction
# ---------------------------------------------------------------------------

def compute_embeddings_for_speakers(
    waveform,                           # torch.Tensor (1, num_samples) at 16kHz
    diarization_segments: list[dict],   # [{"start", "end", "speaker"}, ...]
    min_segment_duration: float = 1.0,  # skip short segments (noisy embeddings)
) -> dict[str, np.ndarray]:
    """Compute one 256-dim embedding per speaker from their audio segments.

    For each speaker: crop audio to their segments, compute per-segment
    embedding, average across segments. Returns {speaker_label: numpy(256,)}.
    """
    inference = _get_embedding_inference()
    sample_rate = 16000

    # Group segments by speaker
    speaker_segs: dict[str, list[dict]] = {}
    for seg in diarization_segments:
        sp = seg["speaker"]
        if sp == "UNKNOWN":
            continue
        speaker_segs.setdefault(sp, []).append(seg)

    result = {}
    for speaker, segs in speaker_segs.items():
        seg_embeddings = []
        for seg in segs:
            duration = seg["end"] - seg["start"]
            if duration < min_segment_duration:
                continue
            start_sample = int(seg["start"] * sample_rate)
            end_sample = int(seg["end"] * sample_rate)
            if end_sample > waveform.shape[1]:
                end_sample = waveform.shape[1]
            if end_sample - start_sample < sample_rate * min_segment_duration:
                continue
            clip = waveform[:, start_sample:end_sample]
            try:
                emb = inference({"waveform": clip, "sample_rate": sample_rate})
                seg_embeddings.append(emb)
            except Exception:
                continue

        if seg_embeddings:
            avg_emb = np.mean(seg_embeddings, axis=0)
            result[speaker] = avg_emb

    return result


# ---------------------------------------------------------------------------
# Cosine similarity & candidate search
# ---------------------------------------------------------------------------

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity in [-1, 1]. Returns 0.0 on zero-norm vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a < 1e-8 or norm_b < 1e-8:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def find_candidates(
    unknown_embedding: np.ndarray,
    top_n: int = 3,
) -> list[dict]:
    """Compare embedding against all known speakers in store.

    Returns up to top_n candidates with score >= THRESHOLD_MAYBE.
    Uses best-match strategy: max similarity across all stored embeddings.
    """
    store = _load_store()
    candidates = []

    for person_id, person_data in store.get("speakers", {}).items():
        stored_embeddings = person_data.get("embeddings", [])
        if not stored_embeddings:
            continue

        best_score = 0.0
        for emb_entry in stored_embeddings:
            stored_vec = np.array(emb_entry["vector"], dtype=np.float32)
            score = _cosine_similarity(unknown_embedding, stored_vec)
            if score > best_score:
                best_score = score

        if best_score >= THRESHOLD_MAYBE:
            candidates.append({
                "person_id": person_id,
                "canonical_name": person_data["canonical_name"],
                "score": round(best_score, 4),
                "confidence": "suggest" if best_score >= THRESHOLD_SUGGEST else "maybe",
                "embeddings_count": len(stored_embeddings),
            })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_n]


# ---------------------------------------------------------------------------
# Save confirmed embeddings
# ---------------------------------------------------------------------------

def save_speaker_embedding(
    person_id: str,
    canonical_name: str,
    embedding: np.ndarray,
    source_file: str,
    date: str,
    speaking_duration_sec: float,
    segments_count: int,
):
    """Add a verified embedding to the store.

    Idempotent: skips if source_file already recorded for this person.
    """
    store = _load_store()

    if person_id not in store["speakers"]:
        store["speakers"][person_id] = {
            "canonical_name": canonical_name,
            "embeddings": [],
        }

    entry = store["speakers"][person_id]
    entry["canonical_name"] = canonical_name

    # Idempotency check
    existing_sources = {e["source_file"] for e in entry["embeddings"]}
    if source_file in existing_sources:
        return

    entry["embeddings"].append({
        "vector": embedding.tolist(),
        "source_file": source_file,
        "date": date,
        "speaking_duration_sec": round(speaking_duration_sec, 1),
        "segments_count": segments_count,
    })
    _save_store(store)


# ---------------------------------------------------------------------------
# Calibration (leave-one-out)
# ---------------------------------------------------------------------------

def run_calibration_report(transcripts_dir: Path) -> str:
    """Leave-one-out cross-validation on all identified speakers.

    For each file with confirmed speakers and raw embeddings:
      - For each speaker: remove their embedding from this file, compare rest
      - Record hit/miss at each threshold

    Returns formatted text report.
    """
    store = _load_store()
    speakers_data = store.get("speakers", {})

    # Collect all (person_id, source_file, vector) triples
    all_entries = []
    for pid, pdata in speakers_data.items():
        for emb in pdata.get("embeddings", []):
            all_entries.append({
                "person_id": pid,
                "canonical_name": pdata["canonical_name"],
                "source_file": emb["source_file"],
                "vector": np.array(emb["vector"], dtype=np.float32),
            })

    if len(all_entries) < 5:
        return (
            f"Недостаточно данных для калибровки: {len(all_entries)} эмбеддингов.\n"
            "Нужно минимум 5 (от 3+ разных людей с 2+ записями)."
        )

    # People with 2+ embeddings (needed for leave-one-out)
    from collections import Counter
    person_counts = Counter(e["person_id"] for e in all_entries)
    eligible = {pid for pid, cnt in person_counts.items() if cnt >= 2}

    if len(eligible) < 2:
        return (
            f"Недостаточно людей с 2+ записями: {len(eligible)}.\n"
            "Калибровка требует минимум 2 таких человека."
        )

    thresholds = [0.70, 0.75, 0.80, 0.85, 0.90]
    results = {t: {"tp": 0, "fp": 0, "fn": 0} for t in thresholds}
    total_tests = 0

    for test_entry in all_entries:
        pid = test_entry["person_id"]
        if pid not in eligible:
            continue

        total_tests += 1
        test_vec = test_entry["vector"]
        test_source = test_entry["source_file"]

        # Compare against all OTHER embeddings (excluding this source_file for this person)
        best_candidates = []
        for other_pid, other_data in speakers_data.items():
            best_score = 0.0
            for emb in other_data.get("embeddings", []):
                # Skip the test entry itself
                if other_pid == pid and emb["source_file"] == test_source:
                    continue
                stored_vec = np.array(emb["vector"], dtype=np.float32)
                score = _cosine_similarity(test_vec, stored_vec)
                if score > best_score:
                    best_score = score
            if best_score > 0:
                best_candidates.append((other_pid, best_score))

        best_candidates.sort(key=lambda x: x[1], reverse=True)
        top_match = best_candidates[0] if best_candidates else (None, 0.0)

        for t in thresholds:
            if top_match[1] >= t:
                if top_match[0] == pid:
                    results[t]["tp"] += 1
                else:
                    results[t]["fp"] += 1
            else:
                results[t]["fn"] += 1

    # Format report
    lines = [
        f"{'='*60}",
        "  КАЛИБРОВКА ЭМБЕДДИНГОВ (leave-one-out)",
        f"{'='*60}",
        f"  Всего тестов: {total_tests}",
        f"  Людей с 2+ записями: {len(eligible)}",
        "",
        f"  {'Порог':<10} {'Precision':<12} {'Recall':<12} {'Предложений':<14} {'Верных':<10}",
        f"  {'-'*56}",
    ]

    for t in thresholds:
        r = results[t]
        suggested = r["tp"] + r["fp"]
        precision = r["tp"] / suggested if suggested > 0 else 0.0
        recall = r["tp"] / total_tests if total_tests > 0 else 0.0
        marker = " ◄ текущий" if t == THRESHOLD_SUGGEST else ""
        lines.append(
            f"  {t:<10.2f} {precision:<12.1%} {recall:<12.1%} "
            f"{suggested:<14} {r['tp']:<10}{marker}"
        )

    lines.extend(["", f"{'='*60}"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_store_stats() -> dict:
    """Summary statistics about the embedding store."""
    store = _load_store()
    speakers = store.get("speakers", {})
    return {
        "total_people": len(speakers),
        "total_embeddings": sum(
            len(p.get("embeddings", [])) for p in speakers.values()
        ),
        "people": [
            {
                "person_id": pid,
                "canonical_name": p["canonical_name"],
                "embeddings_count": len(p.get("embeddings", [])),
            }
            for pid, p in speakers.items()
        ],
    }
