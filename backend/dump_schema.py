import json
from app.main import app
with open("../frontend/openapi.json", "w") as f:
    json.dump(app.openapi(), f, indent=2)
