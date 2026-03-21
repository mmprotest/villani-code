from .bundle import plan_bundle
from .scanner import collect_manifest


def preview_paths(root: str, candidates: list[str]) -> dict[str, object]:
    return {
        'bundle': plan_bundle(root, candidates),
        'manifest': collect_manifest(root, candidates),
    }
