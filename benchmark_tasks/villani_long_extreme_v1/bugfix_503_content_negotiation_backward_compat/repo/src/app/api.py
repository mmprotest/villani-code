from .negotiation import choose_serializer
def respond(item, accept_header): return choose_serializer(accept_header)(item)
