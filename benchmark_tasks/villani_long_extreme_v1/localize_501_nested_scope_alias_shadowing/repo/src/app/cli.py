from .resolver import Resolver
from .astish import Ref
from .formatter import render
def run_demo():
    r=Resolver(); r.bind_alias("db","pkg.database"); return render(r.resolve(Ref("db")))
