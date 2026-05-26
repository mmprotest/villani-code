from .astish import Assignment, FunctionDef, ImportAlias, Module

def parse_program(lines):
    statements=[]; current=None
    for raw in lines:
        line=raw.rstrip()
        if not line: continue
        if line.startswith("def "):
            current=FunctionDef(name=line.split()[1].rstrip(":"), body=[]); statements.append(current); continue
        target_list = statements if current is None else current.body
        if line.startswith("import "):
            target, alias = [p.strip() for p in line.split("import ",1)[1].split(" as ")]
            target_list.append(ImportAlias(alias=alias, target=target))
        else:
            left, right = [p.strip() for p in line.split("=",1)]
            target_list.append(Assignment(target=left, source=right))
    return Module(statements=statements)
