import os
from fastapi import FastAPI, HTTPException, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
from main import StravaStreetCoverageTracker

app = FastAPI(title="Street Coverage Tracker", description="Track your running coverage across city streets")

# Create static and templates directories
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")

class CoverageResponse(BaseModel):
    city_name: str
    total_segments: int
    covered_segments: int
    coverage_percentage: float
    total_activities: int
    map_url: str
    stats_url: str

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Main page with city selection"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/analyze", response_model=CoverageResponse)
async def analyze_coverage(
    city_name: str = Form(...),
    activity_type: str = Form("Run"),
    gpx_dir: str = Form("strava_runs")
):
    """Analyze street coverage from a directory of GPX files"""
    
    if not os.path.exists(gpx_dir) or not os.path.isdir(gpx_dir):
        raise HTTPException(status_code=400, detail=f"Directory not found: {gpx_dir}")
    
    tracker = StravaStreetCoverageTracker(city_name, buffer_distance=20)
    try:
        # Load city streets
        tracker.load_city_streets(network_type="drive")
        # Load GPX files from the specified directory
        tracker.load_gpx_directory(gpx_dir, activity_type=activity_type)
        if len(tracker.activities) == 0:
            raise HTTPException(status_code=400, detail="No valid GPX files found in directory")
        # Process activities
        tracker.process_activities()
        # Generate outputs
        safe_city = city_name.replace(' ', '_').replace(',', '_')
        map_filename = f"coverage_map_{safe_city}_{activity_type}.html"
        stats_filename = f"coverage_stats_{safe_city}_{activity_type}.json"
        tracker.create_map(f"static/{map_filename}")
        tracker.export_statistics(f"static/{stats_filename}")
        # Calculate coverage
        total_segments = len(tracker.street_segments)
        covered_segments = len(tracker.covered_segments)
        coverage_percentage = (covered_segments / total_segments) * 100 if total_segments > 0 else 0
        return CoverageResponse(
            city_name=city_name,
            total_segments=total_segments,
            covered_segments=covered_segments,
            coverage_percentage=coverage_percentage,
            total_activities=len(tracker.activities),
            map_url=f"/static/{map_filename}",
            stats_url=f"/static/{stats_filename}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000) 