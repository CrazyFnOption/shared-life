from uuid import UUID
from .models import Message,Adopt
from . import planning

def register_routes(app,Database,ExpectedUser,db_path):
    @app.get('/api/v2/plans/records/{record_id}/ai')
    def read(record_id:UUID,db:Database,user:ExpectedUser):
        return planning.read(db,user['id'],str(record_id),app.state.planning_provider)

    @app.post('/api/v2/plans/records/{record_id}/ai/messages')
    def send(record_id:UUID,body:Message,db:Database,user:ExpectedUser):
        return planning.send(db,user['id'],str(record_id),body,app.state.planning_provider,db_path)

    @app.post('/api/v2/plans/records/{record_id}/ai/adopt')
    def adopt(record_id:UUID,body:Adopt,db:Database,user:ExpectedUser):
        return planning.adopt(db,user['id'],str(record_id),body)
