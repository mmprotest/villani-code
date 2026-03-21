SAMPLE_RELEASE = {
    'name': 'backend',
    'environment': 'prod',
    'artifacts': [
        {'name': 'api', 'status': 'ok'},
        {'name': 'worker', 'status': 'warn'},
    ],
    'checks': [
        {'name': 'unit', 'status': 'ok'},
        {'name': 'smoke', 'status': 'warn'},
    ],
    'notes': ['deploy after 18:00 UTC'],
}
