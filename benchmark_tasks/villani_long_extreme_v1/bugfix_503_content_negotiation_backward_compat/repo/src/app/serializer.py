def serialize_v1(item): return {"id":item["id"],"name":item["name"]}
def serialize_v2(item): return {"id":item["id"],"name":item["name"],"meta":{"source":item["source"],"score":item["score"]}}
