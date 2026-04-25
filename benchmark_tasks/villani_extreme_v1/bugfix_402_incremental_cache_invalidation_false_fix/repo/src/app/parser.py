def parse_deps(source):
    out=[]
    for line in source.splitlines():
        line=line.strip()
        if line.startswith("use "): out.append(line.split()[1])
    return out
