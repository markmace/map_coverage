import os
from fastapi import FastAPI, HTTPException, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
import asyncio
import json
from typing import Dict, List
from main import StravaStreetCoverageTracker
from shapely.geometry import LineString

app = FastAPI(title="Street Coverage Tracker", description="Track your running coverage across city streets")

# Create static and templates directories
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_progress(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except:
                # Remove dead connections
                self.active_connections.remove(connection)

manager = ConnectionManager()

class CoverageResponse(BaseModel):
    city_name: str
    total_segments: int
    completed_segments: int
    coverage_percentage: float
    total_activities: int
    map_url: str
    stats_url: str
    quality_metrics: dict

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Main page with city selection"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

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
    
    # Add progress callback to tracker with weighted progress calculation
    async def progress_callback(stage: str, current: int, total: int, message: str = ""):
        # Calculate overall progress based on weighted stages
        if stage == "loading_streets":
            overall_progress = (current / total) * 5  # 0-5%
            detailed_message = "Loading city street network..."
        elif stage == "loading_activities":
            overall_progress = 5 + (current / total) * 5  # 5-10%
            detailed_message = f"Loading GPX activities... ({current}/{total})"
        elif stage == "processing_activities":
            overall_progress = 10 + (current / total * 85)  # 10-95%
            detailed_message = f"Processing activity {current} of {total}"
        elif stage == "generating_outputs":
            overall_progress = 95 + (current / total) * 5  # 95-100%
            detailed_message = message or "Generating outputs..."
        else:
            overall_progress = 0
            detailed_message = message
        
        progress_data = {
            "stage": stage,
            "current": current,
            "total": total,
            "overall_progress": overall_progress,
            "message": detailed_message
        }
        await manager.send_progress(progress_data)
    
    try:
        # Load city streets
        await progress_callback("loading_streets", 0, 1, "Loading city street network...")
        tracker.load_city_streets(network_type="drive")
        await progress_callback("loading_streets", 1, 1, "City streets loaded successfully")
        
        # Load GPX files from the specified directory
        await progress_callback("loading_activities", 0, 1, "Loading GPX activities...")
        tracker.load_gpx_directory(gpx_dir, activity_type=activity_type)
        if len(tracker.activities) == 0:
            raise HTTPException(status_code=400, detail="No valid GPX files found in directory")
        await progress_callback("loading_activities", 1, 1, f"Loaded {len(tracker.activities)} activities")
        
        # Process activities with detailed progress updates
        total_activities = len(tracker.activities)
        for i, activity in enumerate(tracker.activities):
            # Process each activity
            if len(activity['gps_points']) < 2:
                continue
                
            # Create activity line once
            activity_line = LineString([p.coords for p in activity['gps_points']])
            
            # Simple intersection check with buffer
            buffered_activity = activity_line.buffer(20 / 111000)  # 20m buffer
            
            # Check each street segment
            for segment_id, street_segment in tracker.street_segment_objects.items():
                if buffered_activity.intersects(street_segment.geometry):
                    # Mark as covered (simple binary approach)
                    if activity['filename'] not in street_segment.activities_covering:
                        street_segment.activities_covering.append(activity['filename'])
                        
                        # Mark as completed if we have any coverage
                        if street_segment.is_completed:
                            tracker.covered_segments.add(segment_id)
            
            # Send progress update every 5 activities or on last activity
            if (i + 1) % 5 == 0 or (i + 1) == total_activities:
                await progress_callback("processing_activities", i + 1, total_activities, 
                                     f"Processed {i + 1}/{total_activities} activities")
        
        await progress_callback("processing_activities", total_activities, total_activities, 
                             "Activity processing complete")
        
        # Generate outputs
        await progress_callback("generating_outputs", 0, 2, "Generating coverage map...")
        safe_city = city_name.replace(' ', '_').replace(',', '_')
        map_filename = f"coverage_map_{safe_city}_{activity_type}.html"
        stats_filename = f"coverage_stats_{safe_city}_{activity_type}.json"
        tracker.create_map(f"static/{map_filename}")
        await progress_callback("generating_outputs", 1, 2, "Generating statistics...")
        tracker.export_statistics(f"static/{stats_filename}")
        await progress_callback("generating_outputs", 2, 2, "Analysis complete!")
        
        # Send explicit completion message
        await manager.send_progress({
            "stage": "complete",
            "overall_progress": 100,
            "message": "Analysis complete!",
            "current": 1,
            "total": 1
        })
        
        # Calculate coverage
        total_segments = len(tracker.street_segments)
        completed_segments = len([s for s in tracker.street_segment_objects.values() if s.is_completed])
        coverage_percentage = (completed_segments / total_segments) * 100 if total_segments > 0 else 0
        
        return CoverageResponse(
            city_name=city_name,
            total_segments=total_segments,
            completed_segments=completed_segments,
            coverage_percentage=coverage_percentage,
            total_activities=len(tracker.activities),
            map_url=f"/static/{map_filename}",
            stats_url=f"/static/{stats_filename}",
            quality_metrics={
                'min_gps_accuracy': tracker.min_gps_accuracy,
                'min_coverage_ratio': tracker.min_coverage_ratio,
                'min_segment_length': tracker.min_segment_length,
                'max_segment_length': tracker.max_segment_length
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        await manager.send_progress({
            "stage": "error",
            "overall_progress": 0,
            "message": f"Error: {str(e)}",
            "current": 0,
            "total": 1
        })
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000) 