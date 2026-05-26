from .projection import BalanceProjector
def resume_projection(event_log, projection_store, checkpoint_store):
    projector=BalanceProjector(projection_store, checkpoint_store); projector.catch_up(event_log); return projection_store
