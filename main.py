import os
import gpxpy
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString
import osmnx as ox
import folium
import numpy as np
from datetime import datetime
import json

class StravaStreetCoverageTracker:
    def __init__(self, city_name, buffer_distance=15):
        """
        Initialize the tracker
        
        Args:
            city_name: Name of the city (e.g., "Boston, Massachusetts, USA")
            buffer_distance: Buffer around GPS tracks in meters (default 15m)
        """
        self.city_name = city_name
        self.buffer_distance = buffer_distance
        self.streets = None
        self.street_segments = None
        self.covered_segments = set()
        self.activities = []
        self.city_boundary = None
        
    def load_city_streets(self, network_type='drive'):
        """
        Load street network from OpenStreetMap
        
        Args:
            network_type: Type of streets to include ('drive', 'bike', 'walk', 'all')
        """
        print(f"Loading street network for {self.city_name}...")
        
        # Download street network
        G = ox.graph_from_place(self.city_name, network_type=network_type)
        
        # Convert to GeoDataFrame
        self.streets = ox.graph_to_gdfs(G, nodes=False)
        
        # Add unique ID to each street segment
        self.streets['street_id'] = range(len(self.streets))
        
        # Split streets into smaller segments at intersections
        self._split_streets_into_segments()
        
        # Get city boundary
        self._get_city_boundary()
        
        print(f"Loaded {len(self.streets)} street segments")
        print(f"Created {len(self.street_segments)} smaller segments")
        
    def _split_streets_into_segments(self):
        """Split streets into smaller segments for more granular coverage tracking"""
        segments = []
        segment_id = 0
        
        for idx, street in self.streets.iterrows():
            # Get the street geometry
            geom = street.geometry
            
            # Split the line into smaller segments (every ~100 meters)
            if geom.length > 0.001:  # Roughly 100 meters in degrees
                # Create points along the line
                distances = np.linspace(0, geom.length, max(2, int(geom.length / 0.001)))
                points = [geom.interpolate(d) for d in distances]
                
                # Create segments between consecutive points
                for i in range(len(points) - 1):
                    segment_geom = LineString([points[i], points[i + 1]])
                    segments.append({
                        'segment_id': segment_id,
                        'street_id': street['street_id'],
                        'geometry': segment_geom,
                        'length': segment_geom.length,
                        'original_street': street
                    })
                    segment_id += 1
            else:
                # Keep original street if it's too short
                segments.append({
                    'segment_id': segment_id,
                    'street_id': street['street_id'],
                    'geometry': geom,
                    'length': geom.length,
                    'original_street': street
                })
                segment_id += 1
        
        self.street_segments = gpd.GeoDataFrame(segments, crs=self.streets.crs)
        
    def _get_city_boundary(self):
        """Get the city boundary for visualization"""
        try:
            from geopy.geocoders import Nominatim
            
            geolocator = Nominatim(user_agent="street_coverage_tracker")
            location = geolocator.geocode(self.city_name)
            
            if location:
                # Get the boundary using OSMnx
                boundary = ox.geocoder.geocode_to_gdf(self.city_name)
                if not boundary.empty:
                    self.city_boundary = boundary.geometry.iloc[0]
                    print(f"Loaded city boundary for {self.city_name}")
        except Exception as e:
            print(f"Could not load city boundary: {e}")
            self.city_boundary = None
        
    def load_gpx_file(self, filepath):
        """Load a single GPX file and extract coordinates"""
        with open(filepath, 'r') as gpx_file:
            gpx = gpxpy.parse(gpx_file)
            
        points = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    points.append((point.longitude, point.latitude))
                    
        return points
        
    def load_gpx_directory(self, gpx_dir, activity_type="Run"):
        """
        Load activities from a directory of GPX files
        
        Args:
            gpx_dir: Directory containing GPX files (searches recursively)
            activity_type: Type of activities to load ("Run", "Bike", "Walk", "Hike", "All")
        """
        if not os.path.exists(gpx_dir):
            print(f"Directory {gpx_dir} not found!")
            return
            
        # Walk through directory and subdirectories
        for root, dirs, files in os.walk(gpx_dir):
            for filename in files:
                if filename.endswith('.gpx'):
                    # Filter by activity type if not "All"
                    if activity_type != "All":
                        # Check if the activity type is in the filename or directory name
                        if activity_type.lower() not in filename.lower() and activity_type.lower() not in root.lower():
                            continue
                    
                    filepath = os.path.join(root, filename)
                    try:
                        points = self.load_gpx_file(filepath)
                        if points:
                            self.activities.append({
                                'filename': filename,
                                'points': points,
                                'date': filename.split('_')[0]  # Assuming date is in filename
                            })
                    except Exception as e:
                        print(f"Error loading {filename}: {e}")
                    
        print(f"Loaded {len(self.activities)} {activity_type} activities from {gpx_dir}")
        
    def process_activities(self):
        """Process all activities and determine which street segments have been covered"""
        if self.street_segments is None:
            raise ValueError("Load city streets first using load_city_streets()")
            
        total_segments = len(self.street_segments)
        
        for activity in self.activities:
            # Create LineString from GPS points
            if len(activity['points']) < 2:
                continue
                
            line = LineString(activity['points'])
            
            # Buffer the line to account for GPS inaccuracy
            buffered_line = line.buffer(self.buffer_distance / 111000)  # Convert meters to degrees (rough)
            
            # Find intersecting street segments
            intersecting = self.street_segments[self.street_segments.intersects(buffered_line)]
            
            # Add to covered segments
            self.covered_segments.update(intersecting['segment_id'].values)
            
        coverage_pct = (len(self.covered_segments) / total_segments) * 100
        print(f"\nCoverage: {len(self.covered_segments)}/{total_segments} street segments ({coverage_pct:.1f}%)")
        
    def create_map(self, save_path='coverage_map.html'):
        """Create an interactive map showing street coverage"""
        if self.street_segments is None:
            raise ValueError("Process activities first")
            
        # Get map center
        bounds = self.street_segments.total_bounds
        center = [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2]
        
        # Create base map
        m = folium.Map(location=center, zoom_start=13)
        
        # Create feature groups for layer control
        gps_layer = folium.FeatureGroup(name="GPS Tracks (Orange Dashed)", show=True)
        covered_streets_layer = folium.FeatureGroup(name="Covered Streets (Green)", show=True)
        uncovered_streets_layer = folium.FeatureGroup(name="Uncovered Streets (Red)", show=True)
        boundary_layer = folium.FeatureGroup(name="City Boundary", show=True)
        
        # Add city boundary if available
        if self.city_boundary and hasattr(self.city_boundary, 'exterior'):
            # Convert boundary to coordinates
            coords = [[lat, lon] for lon, lat in self.city_boundary.exterior.coords]
            folium.Polygon(
                locations=coords,
                color='#34495e',
                weight=2,
                fill=False,
                opacity=0.8,
                popup=f"City Boundary: {self.city_name}"
            ).add_to(boundary_layer)
        
        # Add GPS tracks
        for activity in self.activities:
            if len(activity['points']) >= 2:
                # Convert coordinates for folium (lat, lon)
                track_coords = [[lat, lon] for lon, lat in activity['points']]
                folium.PolyLine(
                    locations=track_coords,
                    color='#FF6B35',  # Orange for GPS tracks
                    weight=3,
                    opacity=0.6,
                    popup=f"Activity: {activity['filename']}",
                    dash_array='5, 10'  # Dashed line to distinguish from streets
                ).add_to(gps_layer)
        
        # Add street segments
        for idx, segment in self.street_segments.iterrows():
            if segment['segment_id'] in self.covered_segments:
                color = '#2ecc71'  # Green for covered
                weight = 3
                opacity = 0.8
                layer = covered_streets_layer
            else:
                color = '#e74c3c'  # Red for uncovered
                weight = 1
                opacity = 0.4
                layer = uncovered_streets_layer
                
            folium.PolyLine(
                locations=[[lat, lon] for lon, lat in segment.geometry.coords],
                color=color,
                weight=weight,
                opacity=opacity
            ).add_to(layer)
        
        # Add all layers to map
        boundary_layer.add_to(m)
        gps_layer.add_to(m)
        covered_streets_layer.add_to(m)
        uncovered_streets_layer.add_to(m)
        
        # Add layer control
        folium.LayerControl().add_to(m)
            
        # Add coverage statistics
        total = len(self.street_segments)
        covered = len(self.covered_segments)
        pct = (covered / total) * 100
        
        stats_html = f'''
        <div style="position: fixed; top: 10px; right: 10px; z-index: 1000; 
                    background-color: white; padding: 15px; border-radius: 8px; 
                    box-shadow: 0 4px 12px rgba(0,0,0,0.3); min-width: 250px;">
            <h4 style="margin: 0 0 10px 0; color: #333;">Coverage Statistics</h4>
            <p style="margin: 5px 0;"><strong>Covered:</strong> {covered}/{total} street segments</p>
            <p style="margin: 5px 0;"><strong>Percentage:</strong> {pct:.1f}%</p>
            <p style="margin: 5px 0;"><strong>Activities:</strong> {len(self.activities)}</p>
        </div>
        '''
        m.get_root().html.add_child(folium.Element(stats_html))
        
        # Save map
        m.save(save_path)
        print(f"Map saved to {save_path}")
        
    def export_statistics(self, save_path='coverage_stats.json'):
        """Export detailed statistics"""
        stats = {
            'city': self.city_name,
            'total_streets': len(self.streets),
            'total_segments': len(self.street_segments),
            'covered_segments': len(self.covered_segments),
            'coverage_percentage': (len(self.covered_segments) / len(self.street_segments)) * 100,
            'total_activities': len(self.activities),
            'buffer_distance_meters': self.buffer_distance,
            'timestamp': datetime.now().isoformat()
        }
        
        with open(save_path, 'w') as f:
            json.dump(stats, f, indent=2)
            
        print(f"Statistics saved to {save_path}")


# Example usage
if __name__ == "__main__":
    import sys
    
    # Check if GPX directory is provided
    if len(sys.argv) > 1:
        gpx_dir = sys.argv[1]
    else:
        gpx_dir = 'strava_runs'  # Default directory
    
    city_name = "Somerville, Massachusetts, USA"
    print(f"Analyzing street coverage for {city_name}")
    print(f"Loading activities from: {gpx_dir}")
    
    # Initialize tracker
    tracker = StravaStreetCoverageTracker(city_name, buffer_distance=20)
    
    # Load city streets
    tracker.load_city_streets(network_type='drive')
    
    # Load GPX files from the Strava client download
    tracker.load_gpx_directory(gpx_dir)
    
    if len(tracker.activities) == 0:
        print("No activities found! Make sure to run the Strava client first:")
        print("python strava_client.py")
        sys.exit(1)
    
    # Process activities
    tracker.process_activities()
    
    # Create visualization
    tracker.create_map('coverage_map.html')
    
    # Export statistics
    tracker.export_statistics()