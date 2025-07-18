import os
import gpxpy
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, Point
import osmnx as ox
import folium
import numpy as np
from datetime import datetime
import json
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import multiprocessing as mp
from functools import partial

class CoverageConfig:
    """Centralized configuration for street coverage tracking - SIMPLIFIED"""
    
    # GPS Quality Settings - NO filtering
    MIN_GPS_ACCURACY = 1000.0  # meters - essentially no filtering
    
    # Street Segmentation Settings - SIMPLE fixed segments
    MIN_SEGMENT_LENGTH = 100.0   # meters - fixed ~100m segments like original
    MAX_SEGMENT_LENGTH = 100.0   # meters - fixed size
    
    # Coverage Completion Settings - VERY simple
    MIN_COVERAGE_RATIO = 0.1     # 10% coverage required (very lenient)
    MIN_COVERAGE_THRESHOLD = 0.01  # minimum ratio to even consider tracking
    
    # Road Filtering Settings - MINIMAL
    EXCLUDED_HIGHWAY_TYPES = {
        'motorway', 'motorway_link'  # Only exclude major highways
    }
    
    # Map Visualization Settings
    BUFFER_DISTANCE = 20  # meters - original 20m buffer
    MAP_ZOOM_START = 13
    COMPLETED_COLOR = '#2ecc71'  # Green
    INCOMPLETED_COLOR = '#e74c3c'  # Red
    GPS_TRACK_COLOR = '#FF6B35'  # Orange

@dataclass
class GPSPoint:
    """GPS point with quality metrics"""
    lat: float
    lon: float
    timestamp: Optional[datetime] = None
    accuracy: Optional[float] = None  # GPS accuracy in meters
    
    @property
    def coords(self) -> Tuple[float, float]:
        return (self.lon, self.lat)

class StreetSegment:
    """Street segment with completion tracking"""
    def __init__(self, segment_id: int, geometry: LineString, street_id: int, 
                 original_street: dict, length: float):
        self.segment_id = segment_id
        self.geometry = geometry
        self.street_id = street_id
        self.original_street = original_street
        self.length = length
        
        # Completion tracking
        self.activities_covering: List[str] = []
        self.coverage_ratios: List[float] = []  # How much of segment was covered
        
    @property
    def is_completed(self) -> bool:
        """Simple binary completion logic"""
        return len(self.activities_covering) > 0
    
    @property
    def completion_metadata(self) -> dict:
        return {
            'segment_id': self.segment_id,
            'is_completed': self.is_completed,
            'activity_count': len(self.activities_covering)
        }

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
        
        # Enhanced tracking
        self.street_segment_objects: Dict[int, StreetSegment] = {}
        
        # Configuration
        self.min_gps_accuracy = CoverageConfig.MIN_GPS_ACCURACY  # meters - filter out poor GPS data
        self.min_segment_length = CoverageConfig.MIN_SEGMENT_LENGTH  # meters - minimum segment size
        self.max_segment_length = CoverageConfig.MAX_SEGMENT_LENGTH  # meters - maximum segment size
        self.min_coverage_ratio = CoverageConfig.MIN_COVERAGE_RATIO  # minimum coverage to consider segment completed
        
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
        
        # Filter out roads unsuitable for running
        self._filter_runnable_streets()
        
        # Add unique ID to each street segment
        self.streets['street_id'] = range(len(self.streets))
        
        # Split streets into smaller segments at intersections
        self._split_streets_into_segments()
        
        # Get city boundary
        self._get_city_boundary()
        
        print(f"Loaded {len(self.streets)} runnable street segments")
        print(f"Created {len(self.street_segments)} smaller segments")
        
    def _filter_runnable_streets(self):
        """Filter out roads that are unsuitable for running"""
        if self.streets.empty:
            return
            
        # Define road types to exclude (highways, motorways, etc.)
        excluded_highway_types = CoverageConfig.EXCLUDED_HIGHWAY_TYPES
        
        # Filter based on highway type
        initial_count = len(self.streets)
        
        # Remove highways and major roads
        self.streets = self.streets[
            ~self.streets['highway'].isin(excluded_highway_types)
        ]
        
        filtered_count = len(self.streets)
        removed_count = initial_count - filtered_count
        
        print(f"Filtered out {removed_count} major roads/highways")
        print(f"Remaining {filtered_count} runnable roads")
        
        # Print breakdown of road types
        if 'highway' in self.streets.columns:
            road_types = self.streets['highway'].value_counts()
            print("\nRoad type breakdown:")
            for road_type, count in road_types.head(5).items():
                print(f"  {road_type}: {count}")
        
    def _split_streets_into_segments(self):
        """Split streets into smaller segments for more granular coverage tracking"""
        segments = []
        segment_id = 0
        
        for idx, street in self.streets.iterrows():
            # Get the street geometry
            geom = street.geometry
            
            # Determine optimal segment length based on street characteristics
            street_length = geom.length * 111000  # Convert to meters (rough)
            
            if street_length > self.max_segment_length:
                # Split into smaller segments
                num_segments = max(2, int(street_length / self.max_segment_length))
                distances = np.linspace(0, geom.length, num_segments + 1)
                points = [geom.interpolate(d) for d in distances]
                
                # Create segments between consecutive points
                for i in range(len(points) - 1):
                    segment_geom = LineString([points[i], points[i + 1]])
                    segment_length = segment_geom.length * 111000
                    
                    if segment_length >= self.min_segment_length:
                        segments.append({
                            'segment_id': segment_id,
                            'street_id': street['street_id'],
                            'geometry': segment_geom,
                            'length': segment_geom.length,
                            'original_street': street
                        })
                        segment_id += 1
            else:
                # Keep original street if it's within reasonable length
                segments.append({
                    'segment_id': segment_id,
                    'street_id': street['street_id'],
                    'geometry': geom,
                    'length': geom.length,
                    'original_street': street
                })
                segment_id += 1
        
        self.street_segments = gpd.GeoDataFrame(segments, crs=self.streets.crs)
        
        # Create StreetSegment objects for enhanced tracking
        for idx, segment in self.street_segments.iterrows():
            self.street_segment_objects[segment['segment_id']] = StreetSegment(
                segment_id=segment['segment_id'],
                geometry=segment.geometry,
                street_id=segment['street_id'],
                original_street=segment['original_street'],
                length=segment['length']
            )
        
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
        
    def _parse_gpx_with_quality(self, filepath: str) -> List[GPSPoint]:
        """Parse GPX file with enhanced quality assessment"""
        with open(filepath, 'r') as gpx_file:
            gpx = gpxpy.parse(gpx_file)
            
        points = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    # Extract quality metrics if available
                    accuracy = None
                    if hasattr(point, 'horizontal_dilution') and point.horizontal_dilution:
                        # Convert HDOP to approximate accuracy
                        accuracy = point.horizontal_dilution * 5.0  # Rough conversion
                    
                    gps_point = GPSPoint(
                        lat=point.latitude,
                        lon=point.longitude,
                        timestamp=point.time,
                        accuracy=accuracy
                    )
                    points.append(gps_point)
                    
        return points
        
    def _assess_gps_quality(self, points: List[GPSPoint]) -> List[GPSPoint]:
        """Assess and filter GPS points based on quality metrics - much more lenient"""
        if len(points) < 2:
            return points
            
        quality_points = []
        
        for point in points:
            # Only skip points with obviously bad accuracy (very high threshold)
            if point.accuracy and point.accuracy > self.min_gps_accuracy:
                continue
            quality_points.append(point)
            
        # If we filtered out too many points, return original
        if len(quality_points) < len(points) * 0.5:  # If we filtered out more than 50%
            print(f"Warning: GPS filtering too aggressive, keeping all {len(points)} points")
            return points
            
        return quality_points
        
    def load_gpx_file(self, filepath):
        """Load a single GPX file and extract coordinates with quality assessment"""
        points = self._parse_gpx_with_quality(filepath)
        quality_points = self._assess_gps_quality(points)
        
        return quality_points
        
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
            
        # Get city bounds from loaded street network
        if self.streets is None:
            print("Warning: No street network loaded. Loading all activities without geographic filtering.")
            city_bounds = None
        else:
            # Get bounds from the street network
            bounds = self.streets.total_bounds  # (minx, miny, maxx, maxy)
            # Add some buffer around the city (about 2km)
            buffer_degrees = 0.02  # roughly 2km at this latitude
            city_bounds = {
                'min_lat': bounds[1] - buffer_degrees,
                'max_lat': bounds[3] + buffer_degrees,
                'min_lon': bounds[0] - buffer_degrees,
                'max_lon': bounds[2] + buffer_degrees
            }
            print(f"City bounds: {city_bounds}")
        
        local_activities = 0
        total_activities = 0
        
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
                        if len(points) >= 2:
                            total_activities += 1
                            
                            # Check if activity is in local area (if we have city bounds)
                            is_local = True  # Default to True if no bounds
                            if city_bounds:
                                is_local = False
                                for point in points:
                                    if (city_bounds['min_lat'] <= point.lat <= city_bounds['max_lat'] and
                                        city_bounds['min_lon'] <= point.lon <= city_bounds['max_lon']):
                                        is_local = True
                                        break
                            
                            if is_local:
                                self.activities.append({
                                    'filename': filename,
                                    'points': [(p.lon, p.lat) for p in points],
                                    'gps_points': points
                                })
                                local_activities += 1
                    except Exception as e:
                        print(f"Error loading {filename}: {e}")
                    
        print(f"Found {total_activities} total {activity_type} activities")
        print(f"Loaded {local_activities} local activities from {gpx_dir}")
        
    def _calculate_coverage_ratio(self, activity_points: List[GPSPoint], 
                                street_segment: StreetSegment) -> float:
        """Calculate what percentage of the street segment was covered"""
        if len(activity_points) < 2:
            return 0.0
            
        # Create activity line
        activity_line = LineString([p.coords for p in activity_points])
        
        # Early exit if no intersection
        if not activity_line.intersects(street_segment.geometry):
            return 0.0
            
        intersection = activity_line.intersection(street_segment.geometry)
        
        if intersection.is_empty:
            return 0.0
            
        # Calculate coverage ratio
        intersection_length = intersection.length
        street_length = street_segment.geometry.length
        
        return intersection_length / street_length
        
    def process_activities(self):
        """Process all activities and determine which street segments have been completed - SIMPLIFIED"""
        if self.street_segments is None:
            raise ValueError("Load city streets first using load_city_streets()")
            
        total_segments = len(self.street_segments)
        total_activities = len(self.activities)
        
        print(f"Processing {total_activities} activities against {total_segments} street segments...")
        
        # SIMPLE processing - no complex filtering
        coverage_found = 0
        for i, activity in enumerate(self.activities):
            if len(activity['gps_points']) < 2:
                continue
                
            # Progress update every 100 activities
            if i % 100 == 0:
                print(f"Processing activity {i+1}/{total_activities}...")
                
            # Create activity line once
            activity_line = LineString([p.coords for p in activity['gps_points']])
            
            # Simple intersection check with buffer
            buffered_activity = activity_line.buffer(CoverageConfig.BUFFER_DISTANCE / 111000)
            
            # Check each street segment
            for segment_id, street_segment in self.street_segment_objects.items():
                if buffered_activity.intersects(street_segment.geometry):
                    # Mark as covered (simple binary approach)
                    if activity['filename'] not in street_segment.activities_covering:
                        street_segment.activities_covering.append(activity['filename'])
                        coverage_found += 1
                        
                        # Mark as completed if we have any coverage
                        if street_segment.is_completed:
                            self.covered_segments.add(segment_id)
        
        print(f"Found {coverage_found} coverage matches")
        print("Processing complete!")
        
        # Calculate final statistics
        completed_count = len([s for s in self.street_segment_objects.values() if s.is_completed])
        coverage_pct = (completed_count / total_segments) * 100
        print(f"\nCoverage: {completed_count}/{total_segments} street segments completed ({coverage_pct:.1f}%)")
        
        # Print quality metrics
        if completed_count > 0:
            print(f"Success! Found {completed_count} completed segments out of {total_segments}")
        else:
            print("No segments completed.")
        
    def _calculate_coverage_ratio_wrapper_with_line(self, activity_line: LineString, street_segment: StreetSegment, filename: str) -> Tuple[int, float, str]:
        """Wrapper function for multiprocessing coverage ratio calculation with pre-computed line"""
        try:
            coverage_ratio = self._calculate_coverage_ratio_with_line(activity_line, street_segment)
            return street_segment.segment_id, coverage_ratio, filename
        except Exception as e:
            print(f"Error in coverage calculation: {e}")
            return street_segment.segment_id, 0.0, filename
        
    def _calculate_coverage_ratio_with_line(self, activity_line: LineString, street_segment: StreetSegment) -> float:
        """Calculate what percentage of the street segment was covered using pre-computed line"""
        # Add buffer to activity line to account for GPS inaccuracy
        buffered_activity = activity_line.buffer(CoverageConfig.BUFFER_DISTANCE / 111000)  # Convert meters to degrees
        
        # Early exit if no intersection
        if not buffered_activity.intersects(street_segment.geometry):
            return 0.0
            
        intersection = buffered_activity.intersection(street_segment.geometry)
        
        if intersection.is_empty:
            return 0.0
            
        # Calculate coverage ratio
        intersection_length = intersection.length
        street_length = street_segment.geometry.length
        
        ratio = intersection_length / street_length
        
        return ratio
        
    def create_map(self, save_path='coverage_map.html'):
        """Create an interactive map showing street coverage"""
        if self.street_segments is None:
            raise ValueError("Process activities first")
            
        # Get map center
        bounds = self.street_segments.total_bounds
        center = [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2]
        
        # Create base map with zoom controls in bottom-left
        m = folium.Map(location=center, zoom_start=CoverageConfig.MAP_ZOOM_START, zoom_control=True)
        
        # Move zoom controls to bottom-left
        m.get_root().html.add_child(folium.Element("""
        <style>
        .leaflet-control-zoom {
            position: absolute !important;
            bottom: 20px !important;
            left: 10px !important;
            top: auto !important;
            right: auto !important;
        }
        </style>
        """))
        
        # Create feature groups for layer control
        gps_layer = folium.FeatureGroup(name="GPS Tracks (Orange Dashed)", show=True)
        completed_streets_layer = folium.FeatureGroup(name="Completed Streets (Green)", show=True)
        incomplete_streets_layer = folium.FeatureGroup(name="Incomplete Streets (Red)", show=True)
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
                # Convert coordinates for folium (lat, lon) - pre-compute once
                track_coords = [[lat, lon] for lon, lat in activity['points']]
                folium.PolyLine(
                    locations=track_coords,
                    color=CoverageConfig.GPS_TRACK_COLOR,  # Orange for GPS tracks
                    weight=3,
                    opacity=0.6,
                    popup=f"Activity: {activity['filename']}",
                    dash_array='5, 10'  # Dashed line to distinguish from streets
                ).add_to(gps_layer)
        
        # Add street segments - pre-compute coordinates
        for segment_id, street_segment in self.street_segment_objects.items():
            if street_segment.is_completed:
                color = CoverageConfig.COMPLETED_COLOR  # Green for completed
                weight = 3
                opacity = 0.8
                layer = completed_streets_layer
            else:
                color = CoverageConfig.INCOMPLETED_COLOR  # Red for incomplete
                weight = 1
                opacity = 0.4
                layer = incomplete_streets_layer
                
            # Create popup with completion details
            metadata = street_segment.completion_metadata
            popup_text = f"""
            <b>Segment {segment_id}</b><br>
            Completed: {'Yes' if metadata['is_completed'] else 'No'}<br>
            Activities: {metadata['activity_count']}<br>
            """
            
            # Pre-compute coordinates once
            coords = [[lat, lon] for lon, lat in street_segment.geometry.coords]
                
            folium.PolyLine(
                locations=coords,
                color=color,
                weight=weight,
                opacity=opacity,
                popup=folium.Popup(popup_text, max_width=300)
            ).add_to(layer)
        
        # Add all layers to map
        boundary_layer.add_to(m)
        gps_layer.add_to(m)
        completed_streets_layer.add_to(m)
        incomplete_streets_layer.add_to(m)
        
        # Add layer control
        folium.LayerControl().add_to(m)
            
        # Add coverage statistics
        total = len(self.street_segments)
        completed = len(self.covered_segments)
        pct = (completed / total) * 100
        
        stats_html = f'''
        <div style="position: fixed; top: 10px; left: 10px; z-index: 1000; 
                    background-color: white; padding: 15px; border-radius: 8px; 
                    box-shadow: 0 4px 12px rgba(0,0,0,0.3); min-width: 250px;">
            <h4 style="margin: 0 0 10px 0; color: #333;">Coverage Statistics</h4>
            <p style="margin: 5px 0;"><strong>Completed:</strong> {completed}/{total} street segments</p>
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
        # Calculate enhanced statistics
        total_segments = len(self.street_segments)
        completed_segments = [s for s in self.street_segment_objects.values() if s.is_completed]
        
        # Coverage ratio distribution for completed segments
        coverage_ratios = [max(s.coverage_ratios) for s in completed_segments if s.coverage_ratios]
        
        stats = {
            'city': self.city_name,
            'total_segments': total_segments,
            'completed_segments': len(completed_segments),
            'coverage_percentage': (len(completed_segments) / total_segments) * 100,
            'total_activities': len(self.activities),
            'timestamp': datetime.now().isoformat(),
            
            # Quality metrics
            'min_gps_accuracy': CoverageConfig.MIN_GPS_ACCURACY,
            'min_coverage_ratio': CoverageConfig.MIN_COVERAGE_RATIO,
            'min_segment_length': CoverageConfig.MIN_SEGMENT_LENGTH,
            'max_segment_length': CoverageConfig.MAX_SEGMENT_LENGTH
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
    tracker = StravaStreetCoverageTracker(city_name, buffer_distance=CoverageConfig.BUFFER_DISTANCE)
    
    # Load city streets FIRST (so we can get geographic bounds)
    tracker.load_city_streets(network_type='drive')
    
    # Load GPX files from the Strava client download (now with geographic filtering)
    tracker.load_gpx_directory(gpx_dir)
    
    if len(tracker.activities) == 0:
        print("No local activities found! Make sure to run the Strava client first:")
        print("python strava_client.py")
        sys.exit(1)
    
    # Process activities
    tracker.process_activities()
    
    # Create visualization
    tracker.create_map('coverage_map.html')
    
    # Export statistics
    tracker.export_statistics()