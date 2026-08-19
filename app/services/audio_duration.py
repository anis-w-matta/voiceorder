import av


def duration_seconds(path: str) -> float:
    with av.open(path, mode="r", metadata_errors="ignore") as container:
        if container.duration is not None:
            return container.duration / av.time_base
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is not None and stream.duration is not None:
            return float(stream.duration * stream.time_base)
    return 0.0