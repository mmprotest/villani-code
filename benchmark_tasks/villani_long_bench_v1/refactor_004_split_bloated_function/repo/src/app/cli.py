from .pipeline import build_release_summary


def render_release(release: dict[str, object]) -> str:
    return build_release_summary(release)
