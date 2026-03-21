def build_release_summary(release: dict[str, object]) -> str:
    lines = [f"release={release['name']} env={release['environment']}"]

    artifacts = release['artifacts']
    if artifacts:
        artifact_items = [f"{artifact['name']}:{artifact['status']}" for artifact in artifacts]
        lines.append('artifacts=' + ','.join(artifact_items))
    else:
        lines.append('artifacts=none')

    warning_items = []
    for check in release['checks']:
        if check['status'] != 'ok':
            warning_items.append(f"{check['name']}:{check['status']}")
    lines.append('warnings=' + (','.join(warning_items) if warning_items else 'none'))

    notes = release.get('notes') or []
    if notes:
        lines.append('notes=' + '; '.join(notes))
    return '\n'.join(lines)
