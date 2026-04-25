class Compiler:
    def __init__(self): self.compile_count = 0
    def compile(self, name, source, dep_outputs):
        self.compile_count += 1
        rendered = "\n".join(line for line in source.splitlines() if not line.startswith("use "))
        for dep_name, dep_value in dep_outputs.items(): rendered = rendered.replace(f"{{{{{dep_name}}}}}", dep_value)
        return rendered
