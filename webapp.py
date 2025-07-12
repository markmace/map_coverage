import os
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
from main import StravaStreetCoverageTracker
import json
from fastapi import Request

app = FastAPI(title="Street Coverage Tracker", description="Track your running coverage across city streets")

# Create static and templates directories
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")

class CoverageRequest(BaseModel):
    city_name: str
    buffer_distance: int = 20
    network_type: str = "drive"

class CoverageResponse(BaseModel):
    city_name: str
    total_streets: int
    covered_streets: int
    coverage_percentage: float
    total_activities: int
    map_url: str
    stats_url: str

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Main page with city selection and file upload"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/analyze", response_model=CoverageResponse)
async def analyze_coverage(
    city_name: str = Form(...),
    buffer_distance: int = Form(20),
    network_type: str = Form("drive"),
    files: List[UploadFile] = File(...)
):
    """Analyze street coverage from uploaded GPX files"""
    
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    
    # Create temporary directory for uploaded files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Save uploaded files
        for file in files:
            if not file.filename.endswith('.gpx'):
                continue
                
            file_path = temp_path / file.filename
            with open(file_path, "wb") as buffer:
                content = await file.read()
                buffer.write(content)
        
        # Initialize tracker
        tracker = StravaStreetCoverageTracker(city_name, buffer_distance=buffer_distance)
        
        try:
            # Load city streets
            tracker.load_city_streets(network_type=network_type)
            
            # Load GPX files from temp directory
            tracker.load_gpx_directory(str(temp_path))
            
            if len(tracker.activities) == 0:
                raise HTTPException(status_code=400, detail="No valid GPX files found")
            
            # Process activities
            tracker.process_activities()
            
            # Generate outputs
            map_filename = f"coverage_map_{city_name.replace(' ', '_').replace(',', '_')}.html"
            stats_filename = f"coverage_stats_{city_name.replace(' ', '_').replace(',', '_')}.json"
            
            tracker.create_map(f"static/{map_filename}")
            tracker.export_statistics(f"static/{stats_filename}")
            
            # Calculate coverage
            total_streets = len(tracker.streets)
            covered_streets = len(tracker.covered_streets)
            coverage_percentage = (covered_streets / total_streets) * 100 if total_streets > 0 else 0
            
            return CoverageResponse(
                city_name=city_name,
                total_streets=total_streets,
                covered_streets=covered_streets,
                coverage_percentage=coverage_percentage,
                total_activities=len(tracker.activities),
                map_url=f"/static/{map_filename}",
                stats_url=f"/static/{stats_filename}"
            )
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

@app.get("/map/{filename}")
async def get_map(filename: str):
    """Serve generated map files"""
    file_path = f"static/{filename}"
    if os.path.exists(file_path):
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Map file not found")

@app.get("/stats/{filename}")
async def get_stats(filename: str):
    """Serve generated stats files"""
    file_path = f"static/{filename}"
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="application/json")
    raise HTTPException(status_code=404, detail="Stats file not found")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000) 