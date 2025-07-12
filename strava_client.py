import requests
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
import json
import time
from datetime import datetime
import os
from typing import List, Optional, Set

class StravaAPIClient:
    def __init__(self, client_id, client_secret, token_file='strava_access_tokens.json'):
        """
        Initialize Strava API client
        
        Args:
            client_id: Your Strava app's Client ID
            client_secret: Your Strava app's Client Secret
            token_file: File to store access tokens for reuse
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_file = token_file
        self.access_token = None
        self.refresh_token = None
        self.token_expiry = None
        
        # Rate limiting tracking
        self.request_count = 0
        self.request_reset_time = time.time() + 900  # 15 minutes
        
        # Try to load existing tokens
        self.load_tokens()
        
    def save_tokens(self):
        """Save tokens to file for reuse"""
        with open(self.token_file, 'w') as f:
            json.dump({
                'access_token': self.access_token,
                'refresh_token': self.refresh_token,
                'expires_at': self.token_expiry
            }, f)
            
    def load_tokens(self):
        """Load tokens from file if they exist"""
        if os.path.exists(self.token_file):
            with open(self.token_file, 'r') as f:
                tokens = json.load(f)
                self.access_token = tokens.get('access_token')
                self.refresh_token = tokens.get('refresh_token')
                self.token_expiry = tokens.get('expires_at')
                
    def needs_auth(self):
        """Check if we need to authenticate"""
        if not self.access_token:
            return True
        if self.token_expiry and time.time() > self.token_expiry:
            return True
        return False
        
    def refresh_access_token(self):
        """Refresh the access token using the refresh token"""
        if not self.refresh_token:
            raise ValueError("No refresh token available")
            
        response = requests.post(
            'https://www.strava.com/oauth/token',
            data={
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token,
                'grant_type': 'refresh_token'
            }
        )
        
        if response.status_code == 200:
            token_data = response.json()
            self.access_token = token_data['access_token']
            self.refresh_token = token_data['refresh_token']
            self.token_expiry = token_data['expires_at']
            self.save_tokens()
            print("Access token refreshed successfully")
        else:
            raise Exception(f"Failed to refresh token: {response.text}")
            
    def authenticate(self):
        """Run the OAuth flow to get access token"""
        # OAuth handler
        class OAuthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                # Parse the authorization code from the callback
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                
                if 'code' in params:
                    self.server.auth_code = params['code'][0]
                    self.send_response(200)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()
                    self.wfile.write(b'<html><body><h1>Authorization successful!</h1>')
                    self.wfile.write(b'<p>You can close this window and return to your Python script.</p></body></html>')
                else:
                    self.send_response(400)
                    self.end_headers()
                    
            def log_message(self, format, *args):
                # Suppress log messages
                return
        
        # Step 1: Direct user to Strava authorization page
        auth_url = f"https://www.strava.com/oauth/authorize?" \
                   f"client_id={self.client_id}" \
                   f"&response_type=code" \
                   f"&redirect_uri=http://localhost:8000/callback" \
                   f"&approval_prompt=force" \
                   f"&scope=activity:read_all"
        
        print(f"Opening browser for authorization...")
        print(f"If browser doesn't open, go to: {auth_url}")
        webbrowser.open(auth_url)
        
        # Step 2: Start local server to receive callback
        server = HTTPServer(('localhost', 8000), OAuthHandler)
        server.auth_code = None
        
        # Wait for callback
        while server.auth_code is None:
            server.handle_request()
            
        # Step 3: Exchange authorization code for access token
        token_response = requests.post(
            'https://www.strava.com/oauth/token',
            data={
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'code': server.auth_code,
                'grant_type': 'authorization_code'
            }
        )
        
        if token_response.status_code == 200:
            token_data = token_response.json()
            self.access_token = token_data['access_token']
            self.refresh_token = token_data['refresh_token']
            self.token_expiry = token_data['expires_at']
            self.save_tokens()
            print("Authentication successful!")
            return True
        else:
            print(f"Authentication failed: {token_response.text}")
            return False
            
    def ensure_authenticated(self):
        """Make sure we have a valid access token"""
        if self.needs_auth():
            if self.refresh_token:
                # Try to refresh first
                try:
                    self.refresh_access_token()
                except:
                    # If refresh fails, do full auth
                    self.authenticate()
            else:
                self.authenticate()
                
    def check_rate_limit(self, response):
        """Check rate limit headers and handle if needed"""
        if 'X-Ratelimit-Limit' in response.headers and 'X-Ratelimit-Usage' in response.headers:
            limit = response.headers['X-Ratelimit-Limit'].split(',')
            usage = response.headers['X-Ratelimit-Usage'].split(',')
            
            fifteen_min_limit = int(limit[0])
            fifteen_min_usage = int(usage[0])
            daily_limit = int(limit[1])
            daily_usage = int(usage[1])
            
            print(f"Rate limit: {fifteen_min_usage}/{fifteen_min_limit} (15min), {daily_usage}/{daily_limit} (daily)")
            
            # If we're close to the 15-minute limit, wait
            if fifteen_min_usage >= fifteen_min_limit - 5:
                wait_time = max(0, self.request_reset_time - time.time())
                print(f"Approaching rate limit, waiting {wait_time:.0f} seconds...")
                time.sleep(wait_time + 1)
                self.request_reset_time = time.time() + 900
                
    def get_athlete(self):
        """Get authenticated athlete information"""
        self.ensure_authenticated()
        
        response = requests.get(
            'https://www.strava.com/api/v3/athlete',
            headers={'Authorization': f'Bearer {self.access_token}'}
        )
        
        self.check_rate_limit(response)
        
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to get athlete info: {response.text}")
            
    def get_all_activities(self, activity_types: Optional[Set[str]] = None, 
                          sport_types: Optional[Set[str]] = None,
                          after_date: Optional[int] = None) -> List[dict]:
        """
        Get ALL athlete's activities, optionally filtered by type
        
        Args:
            activity_types: Set of activity types to include (e.g. {'Run', 'Ride'})
            sport_types: Set of sport types to include (e.g. {'Run', 'MountainBikeRide', 'Ride'})
            after_date: Only get activities after this date (unix timestamp)
            
        Returns:
            List of filtered activities
        """
        self.ensure_authenticated()
        
        all_activities = []
        page = 1
        per_page = 200  # Maximum allowed by Strava
        
        print("Fetching all activities...")
        
        while True:
            params = {
                'page': page,
                'per_page': per_page
            }
            
            if after_date:
                params['after'] = after_date
                
            response = requests.get(
                'https://www.strava.com/api/v3/athlete/activities',
                headers={'Authorization': f'Bearer {self.access_token}'},
                params=params
            )
            
            self.check_rate_limit(response)
            
            if response.status_code == 200:
                page_activities = response.json()
                
                if not page_activities:
                    # No more activities
                    break
                    
                # Filter activities if types specified
                if activity_types or sport_types:
                    filtered_activities = []
                    for activity in page_activities:
                        if activity_types and activity.get('type') in activity_types:
                            filtered_activities.append(activity)
                        elif sport_types and activity.get('sport_type') in sport_types:
                            filtered_activities.append(activity)
                        elif not activity_types and not sport_types:
                            filtered_activities.append(activity)
                    
                    all_activities.extend(filtered_activities)
                    print(f"Page {page}: Found {len(page_activities)} activities, {len(filtered_activities)} match filter")
                else:
                    all_activities.extend(page_activities)
                    print(f"Page {page}: Found {len(page_activities)} activities")
                
                # If we got less than per_page activities, we've reached the end
                if len(page_activities) < per_page:
                    break
                    
                page += 1
                
                # Small delay to be nice to the API
                time.sleep(0.5)
            else:
                raise Exception(f"Failed to get activities: {response.text}")
                
        return all_activities
        
    def get_activity_streams(self, activity_id, stream_types=['latlng', 'time', 'distance']):
        """
        Get detailed GPS data for an activity
        
        Args:
            activity_id: Strava activity ID
            stream_types: Types of data to fetch (latlng for GPS coordinates)
        """
        self.ensure_authenticated()
        
        response = requests.get(
            f'https://www.strava.com/api/v3/activities/{activity_id}/streams',
            headers={'Authorization': f'Bearer {self.access_token}'},
            params={
                'keys': ','.join(stream_types),
                'key_by_type': True
            }
        )
        
        self.check_rate_limit(response)
        
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to get activity streams: {response.text}")
            
    def download_gps_data_by_type(self, output_dir='strava_activities', 
                                   activity_types: Optional[Set[str]] = None,
                                   sport_types: Optional[Set[str]] = None):
        """
        Download GPS data for activities of specific types
        
        Args:
            output_dir: Directory to save GPS data
            activity_types: Set of activity types to download (e.g. {'Run', 'Ride'})
            sport_types: Set of sport types to download (e.g. {'Run', 'MountainBikeRide'})
        """
        os.makedirs(output_dir, exist_ok=True)
        
        # Create subdirectories for each type if filtering
        if activity_types or sport_types:
            types_to_create = set()
            if activity_types:
                types_to_create.update(activity_types)
            if sport_types:
                types_to_create.update(sport_types)
            
            for activity_type in types_to_create:
                os.makedirs(os.path.join(output_dir, activity_type), exist_ok=True)
        
        # Get all activities
        activities = self.get_all_activities(activity_types=activity_types, sport_types=sport_types)
        
        print(f"\nFound {len(activities)} activities matching criteria")
        
        # Track progress
        downloaded = 0
        skipped = 0
        errors = 0
        
        # Download GPS data for each activity
        for i, activity in enumerate(activities):
            activity = activities[i]
            activity_id = activity['id']
            activity_name = activity['name']
            activity_date = activity['start_date']
            activity_type = activity.get('sport_type', activity.get('type', 'Unknown'))
            
            print(f"\nProcessing {i+1}/{len(activities)}: {activity_name} ({activity_date}) - {activity_type}")
            
            # Determine subdirectory
            if activity_types or sport_types:
                subdir = activity_type
            else:
                subdir = ''
            
            # Check if already downloaded
            # Better filename sanitization to handle special characters
            import re
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', activity_name)  # Remove invalid filename chars
            safe_name = re.sub(r'[^\w\s-]', '_', safe_name)  # Replace other special chars with underscore
            safe_name = safe_name.replace(' ', '_').replace('__', '_').strip('_')  # Clean up underscores
            
            filename = f"{activity_date}_{activity_id}_{safe_name}.gpx"
            filepath = os.path.join(output_dir, subdir, filename)
            
            print(f"  Checking for existing file: {filename}")
            if os.path.exists(filepath):
                print(f"  ✓ Already downloaded, skipping...")
                skipped += 1
                continue
            else:
                print(f"  → File not found, will download...")
            
            try:
                # Get GPS stream
                streams = self.get_activity_streams(activity_id)
                
                if 'latlng' in streams and streams['latlng']['data']:
                    # Convert to GPX format
                    gpx_content = self._create_gpx(activity, streams['latlng']['data'])
                    
                    # Save as GPX file
                    with open(filepath, 'w') as f:
                        f.write(gpx_content)
                    
                    downloaded += 1
                    print(f"  ✓ Saved as {filename}")
                else:
                    print(f"  ⚠ No GPS data available for this activity")
                    skipped += 1
                    
            except Exception as e:
                print(f"  ✗ Error downloading activity: {e}")
                errors += 1
                
                # If rate limit error, wait longer
                if "429" in str(e) or "Rate Limit" in str(e):
                    print("  Rate limit hit, waiting 15 minutes...")
                    time.sleep(900)
                
            # Be nice to Strava's API
            time.sleep(1)
            
        print(f"\n{'='*50}")
        print(f"Download complete!")
        print(f"Downloaded: {downloaded}")
        print(f"Skipped: {skipped}")
        print(f"Errors: {errors}")
        print(f"Total processed: {len(activities)}")
        
    def _create_gpx(self, activity, coordinates):
        """Convert activity data to GPX format"""
        # Escape XML special characters
        import xml.sax.saxutils as saxutils
        name = saxutils.escape(activity['name'])
        
        gpx_template = f'''<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Strava API Client" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata>
    <name>{name}</name>
    <time>{activity['start_date']}</time>
  </metadata>
  <trk>
    <name>{name}</name>
    <type>{activity.get('sport_type', activity.get('type', 'Unknown'))}</type>
    <trkseg>
'''
        
        # Add track points
        for coord in coordinates:
            if coord and len(coord) >= 2:  # Ensure valid coordinate
                lat, lon = coord
                gpx_template += f'      <trkpt lat="{lat}" lon="{lon}"></trkpt>\n'
        
        gpx_template += '''    </trkseg>
  </trk>
</gpx>'''
        
        return gpx_template




# Example usage
if __name__ == "__main__":
    # Load API keys
    try:
        with open('strava_credentials.json', 'r') as f:
            api_keys = json.load(f)
        CLIENT_ID = api_keys['strava_client_id']
        CLIENT_SECRET = api_keys['strava_client_secret']
    except FileNotFoundError:
        print("Error: strava_credentials.json not found!")
        exit(1)

    # Initialize client
    client = StravaAPIClient(CLIENT_ID, CLIENT_SECRET)
    
    # Get athlete info
    athlete = client.get_athlete()
    print(f"Authenticated as: {athlete['firstname']} {athlete['lastname']}")
    
    # Example 1: Download only runs (simple approach)
    print("\n" + "="*50)
    print("Downloading all running activities...")
    client.download_gps_data_by_type(
        output_dir='strava_runs',
        activity_types={'Run'}  # Will get all running activities
    )
    
    # Example 2: Download only cycling activities (all types of rides)
    # print("\n" + "="*50)
    # print("Downloading all cycling activities...")
    # client.download_gps_data_by_type(
    #     output_dir='strava_rides',
    #     activity_types={'Ride'},  # Gets all ride types
    #     resume_from_id=None
    # )
    
    # Example 3: Download specific sport types
    # print("\n" + "="*50)
    # print("Downloading specific sport types...")
    # client.download_gps_data_by_type(
    #     output_dir='strava_specific',
    #     sport_types={'Run', 'TrailRun', 'VirtualRun'},  # Specific sport types
    #     resume_from_id=None
    # )
    
    # Example 4: Download ALL activities (no filtering)
    # print("\n" + "="*50)
    # print("Downloading ALL activities...")
    # client.download_gps_data_by_type(
    #     output_dir='strava_all_activities',
    #     activity_types=None,  # No filtering - gets everything
    #     sport_types=None,
    #     resume_from_id=None
    # )