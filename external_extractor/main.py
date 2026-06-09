from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="Location Extractor")

class ExtractorRequest(BaseModel):
    query: str

class ContextEntry(BaseModel):
    term: str
    context: str

class ExtractorResponse(BaseModel):
    enrichments: list[ContextEntry] = Field(default_factory=list)

@app.post("/", response_model=ExtractorResponse)
def extract_location(request: ExtractorRequest):
    query_lower = request.query.lower()
    enrichments = []
    
    locations = {
        "new york": "Location: 40.7128° N, 74.0060° W",
        "london": "Location: 51.5074° N, 0.1278° W",
        "tokyo": "Location: 35.6762° N, 139.6503° E",
        "paris": "Location: 48.8566° N, 2.3522° E"
    }
    
    for loc, coords in locations.items():
        if loc in query_lower:
            enrichments.append(ContextEntry(term=loc, context=coords))
            
    return ExtractorResponse(enrichments=enrichments)
