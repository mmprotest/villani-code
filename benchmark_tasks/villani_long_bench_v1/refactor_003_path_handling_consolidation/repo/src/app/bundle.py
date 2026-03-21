from pathlib import Path


def _resolve_workspace_path(root: str, candidate: str) -> str:
    root_path = Path(root).resolve()
    candidate_path = Path(candidate)
    resolved = (root_path / candidate_path).resolve() if not candidate_path.is_absolute() else candidate_path.resolve()
    try:
        return resolved.relative_to(root_path).as_posix()
    except ValueError:
        return resolved.as_posix()



def plan_bundle(root: str, candidates: list[str]) -> list[str]:
    return [_resolve_workspace_path(root, candidate) for candidate in candidates]
