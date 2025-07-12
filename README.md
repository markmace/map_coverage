# Map Coverage Tracker

A Python application to track street coverage from Strava activities using GPS data.

## Setup

This project uses `uv` for dependency management. To get started:

1. Install `uv` (if not already installed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Install dependencies:
   ```bash
   uv sync
   ```

3. Activate the virtual environment:
   ```bash
   source .venv/bin/activate
   ```

## Usage

### Web Application (Recommended)

The easiest way to use this tool is through the web interface:

1. **Start the web server:**
   ```bash
   uv run python webapp.py
   ```

2. **Open your browser** and go to `http://localhost:8000`

3. **Upload your GPX files** and select your city to see your street coverage!

### Command Line Workflow

1. **Download your Strava activities:**
   ```bash
   uv run python strava_client.py
   ```
   This will download your activities as GPX files to the `strava_activities/` directory.

2. **Analyze street coverage:**
   ```bash
   uv run python main.py
   ```
   This will:
   - Load the city street network
   - Process your downloaded activities
   - Calculate coverage statistics
   - Generate an interactive map (`coverage_map.html`)
   - Export detailed statistics (`coverage_stats.json`)

### Components

The main application provides a `StravaStreetCoverageTracker` class that can:

- Load street networks from OpenStreetMap
- Process GPX files from Strava activities
- Calculate street coverage statistics
- Generate interactive maps showing covered vs uncovered streets
- Export detailed statistics

## Strava API Setup

You need to set up Strava API access:

1. Create a Strava app at https://www.strava.com/settings/api
2. Get your Client ID and Client Secret
3. Create `strava_credentials.json` with:
   ```json
   {
       "strava_client_id": "YOUR_CLIENT_ID",
       "strava_client_secret": "YOUR_CLIENT_SECRET"
   }
   ```

The app will automatically create `strava_access_tokens.json` when you first authenticate.

## Dependencies

- `gpxpy` - GPX file parsing
- `geopandas` - Geospatial data handling
- `osmnx` - OpenStreetMap data access
- `folium` - Interactive map creation
- `shapely` - Geometric operations
- `pandas` - Data manipulation
- `requests` - HTTP requests (for Strava API)
- `numpy` - Numerical operations
- `fastapi` - Web framework
- `uvicorn` - ASGI server
- `jinja2` - HTML templates

## Files

- `main.py` - Main application with street coverage tracking logic
- `webapp.py` - FastAPI web application
- `strava_client.py` - Strava API client for downloading activities
- `templates/index.html` - Web interface template
- `API_KEYS.json` - API credentials (not tracked in git)
