def make_job_result(name: str, duration_seconds: int, warnings: list[str] | None = None) -> dict[str, object]:
    return {
        'name': name,
        'duration_seconds': duration_seconds,
        'warnings': list(warnings or []),
    }
