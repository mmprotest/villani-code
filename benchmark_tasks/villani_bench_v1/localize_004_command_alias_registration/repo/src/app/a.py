ALIASES = {
    'rn': 'remove',
    'mvv': 'move',
    'ls': 'list',
    'cp': 'copy',
}

def resolve(cmd):
    return ALIASES.get(cmd, cmd)
