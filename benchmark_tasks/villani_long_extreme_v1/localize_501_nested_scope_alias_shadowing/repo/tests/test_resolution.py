from app.resolver import Resolver
from app.astish import Ref
from app.formatter import render
def test_nested_lambda_uses_outer_alias_not_local_value():
    r=Resolver(); r.bind_alias("db","pkg.database"); r.push(); r.bind_value("db","local_string"); assert render(r.resolve(Ref("db")))=="alias::pkg.database"
def test_local_alias_still_wins_over_outer_alias():
    r=Resolver(); r.bind_alias("db","pkg.database"); r.push(); r.bind_alias("db","pkg.alt"); assert render(r.resolve(Ref("db")))=="alias::pkg.alt"
def test_plain_value_resolution_works_when_no_alias_exists():
    r=Resolver(); r.bind_value("x","number"); assert render(r.resolve(Ref("x")))=="value::number"
