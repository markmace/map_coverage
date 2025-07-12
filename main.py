import os
import gpxpy
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, LineString
import osmnx as ox
import folium
from folium import plugins
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
        self.covered_streets = set()
        self.activities = []
        
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
        
        print(f"Loaded {len(self.streets)} street segments")
        
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
    
    def load_strava_export(self, export_dir):
        """
        Load activities from Strava data export
        
        Args:
            export_dir: Directory containing exported Strava data
        """
        activities_dir = os.path.join(export_dir, 'activities')
        
        for filename in os.listdir(activities_dir):
            if filename.endswith('.gpx'):
                filepath = os.path.join(activities_dir, filename)
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
                    
        print(f"Loaded {len(self.activities)} activities")
        
    def load_gpx_directory(self, gpx_dir):
        """
        Load activities from a directory of GPX files
        
        Args:
            gpx_dir: Directory containing GPX files
        """
        if not os.path.exists(gpx_dir):
            print(f"Directory {gpx_dir} not found!")
            return
            
        for filename in os.listdir(gpx_dir):
            if filename.endswith('.gpx'):
                filepath = os.path.join(gpx_dir, filename)
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
                    
        print(f"Loaded {len(self.activities)} activities from {gpx_dir}")
        
    def process_activities(self):
        """Process all activities and determine which streets have been covered"""
        if self.streets is None:
            raise ValueError("Load city streets first using load_city_streets()")
            
        total_streets = len(self.streets)
        
        for activity in self.activities:
            # Create LineString from GPS points
            if len(activity['points']) < 2:
                continue
                
            line = LineString(activity['points'])
            
            # Buffer the line to account for GPS inaccuracy
            buffered_line = line.buffer(self.buffer_distance / 111000)  # Convert meters to degrees (rough)
            
            # Find intersecting streets
            intersecting = self.streets[self.streets.intersects(buffered_line)]
            
            # Add to covered streets
            self.covered_streets.update(intersecting['street_id'].values)
            
        coverage_pct = (len(self.covered_streets) / total_streets) * 100
        print(f"\nCoverage: {len(self.covered_streets)}/{total_streets} streets ({coverage_pct:.1f}%)")
        
    def create_map(self, save_path='coverage_map.html'):
        """Create an interactive map showing street coverage"""
        if self.streets is None:
            raise ValueError("Process activities first")
            
        # Get map center
        bounds = self.streets.total_bounds
        center = [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2]
        
        # Create base map
        m = folium.Map(location=center, zoom_start=13)
        
        # Add streets
        for idx, street in self.streets.iterrows():
            if street['street_id'] in self.covered_streets:
                color = '#2ecc71'  # Green for covered
                weight = 4
                opacity = 0.8
            else:
                color = '#e74c3c'  # Red for uncovered
                weight = 2
                opacity = 0.5
                
            folium.PolyLine(
                locations=[[lat, lon] for lon, lat in street.geometry.coords],
                color=color,
                weight=weight,
                opacity=opacity
            ).add_to(m)
            
        # Add coverage statistics
        total = len(self.streets)
        covered = len(self.covered_streets)
        pct = (covered / total) * 100
        
        stats_html = f'''
        <div style="position: fixed; top: 10px; right: 10px; z-index: 1000; 
                    background-color: white; padding: 10px; border-radius: 5px; 
                    box-shadow: 0 2px 5px rgba(0,0,0,0.3);">
            <h4>Coverage Statistics</h4>
            <p><strong>Covered:</strong> {covered}/{total} streets</p>
            <p><strong>Percentage:</strong> {pct:.1f}%</p>
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
            'covered_streets': len(self.covered_streets),
            'coverage_percentage': (len(self.covered_streets) / len(self.streets)) * 100,
            'total_activities': len(self.activities),
            'buffer_distance_meters': self.buffer_distance,
            'timestamp': datetime.now().isoformat()
        }
        
        with open(save_path, 'w') as f:
            json.dump(stats, f, indent=2)
            
        print(f"Statistics saved to {save_path}")
        
    def get_uncovered_streets_nearby(self, radius_km=1):
        """Find uncovered streets within a certain radius of covered streets"""
        # This helps identify good areas to explore next
        uncovered = self.streets[~self.streets['street_id'].isin(self.covered_streets)]
        
        # Create buffer around covered streets
        covered_streets_geom = self.streets[self.streets['street_id'].isin(self.covered_streets)]
        covered_buffer = covered_streets_geom.unary_union.buffer(radius_km / 111)  # Convert km to degrees
        
        # Find uncovered streets within buffer
        nearby_uncovered = uncovered[uncovered.intersects(covered_buffer)]
        
        return nearby_uncovered


# Example usage
if __name__ == "__main__":
    import sys
    
    # Check if GPX directory is provided
    if len(sys.argv) > 1:
        gpx_dir = sys.argv[1]
    else:
        gpx_dir = 'strava_activities'  # Default directory
    
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
    
    # Find areas to explore
    nearby = tracker.get_uncovered_streets_nearby(radius_km=0.5)
    print(f"Found {len(nearby)} uncovered streets within 0.5km of covered areas")