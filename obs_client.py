# obs_client.py
import obsws_python as obs
import time

class OBSClient:
    def __init__(self, host='localhost', port=4455, password='', timeout=3):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.client = None
        
    def connect(self):
        """Establish connection to OBS WebSocket"""
        try:
            self.client = obs.ReqClient(
                host=self.host, 
                port=self.port, 
                password=self.password, 
                timeout=self.timeout
            )
            print("[OBS] Connected successfully")
            return self.client
        except Exception as e:
            print(f"[OBS] Failed to connect: {e}")
            raise
    
    def disconnect(self):
        """Disconnect from OBS WebSocket"""
        if self.client:
            try:
                self.client.disconnect()
                print("[OBS] Disconnected")
            except Exception as e:
                print(f"[OBS] Error during disconnect: {e}")
    
    def start_recording(self):
        """Tell OBS to start a recording"""
        if not self.client:
            raise ConnectionError("Not connected to OBS")
        
        try:
            # Check if recording is already active
            status = self.get_recording_status()
            if status and status.get('output_active', False):
                print("[OBS] Recording already active")
                return
            
            # Start recording
            self.client.start_record()
            print("[OBS] Recording started")
            
            # Wait a moment for recording to initialize
            time.sleep(0.5)
            
        except Exception as e:
            print(f"[OBS] *** FAILED to start recording: {e}")
            raise
    
    def stop_recording(self):
        """Tell OBS to stop the current recording"""
        if not self.client:
            raise ConnectionError("Not connected to OBS")
        
        try:
            # Check if recording is active
            status = self.get_recording_status()
            if not status or not status.get('output_active', False):
                print("[OBS] No active recording to stop")
                return
            
            # Stop recording
            self.client.stop_record()
            print("[OBS] Recording stopped")
            
            # Wait for recording to finalize
            time.sleep(1)
            
        except Exception as e:
            print(f"[OBS] *** FAILED to stop recording: {e}")
            raise
    
    def get_recording_status(self):
        """Get current recording status and file path"""
        if not self.client:
            raise ConnectionError("Not connected to OBS")
        
        try:
            # Get record status
            response = self.client.get_record_status()
            
            # The response is a dataclass with specific attributes
            # Let's inspect what's available
            status_dict = {}
            
            # Check for common attributes (these may vary by OBS version)
            if hasattr(response, 'output_active'):
                status_dict['output_active'] = response.output_active
            if hasattr(response, 'output_paused'):
                status_dict['output_paused'] = response.output_paused
            if hasattr(response, 'output_timecode'):
                status_dict['output_timecode'] = response.output_timecode
            if hasattr(response, 'output_duration'):
                status_dict['output_duration'] = response.output_duration
            if hasattr(response, 'output_bytes'):
                status_dict['output_bytes'] = response.output_bytes
            
            # For output path, we might need to get it separately
            try:
                # Try to get output path from GetRecordDirectory
                dir_response = self.client.get_record_directory()
                if hasattr(dir_response, 'record_directory'):
                    status_dict['record_directory'] = dir_response.record_directory
            except:
                pass
                
            return status_dict
            
        except Exception as e:
            print(f"[OBS] Failed to get recording status: {e}")
            return None
    
    def get_recording_settings(self):
        """Get current recording settings"""
        if not self.client:
            raise ConnectionError("Not connected to OBS")
        
        try:
            response = self.client.get_record_directory()
            settings_dict = {}
            
            if hasattr(response, 'record_directory'):
                settings_dict['record_directory'] = response.record_directory
            
            # Also get output settings
            try:
                output_response = self.client.get_record_directory()
                if hasattr(output_response, 'record_directory'):
                    settings_dict['record_directory'] = output_response.record_directory
            except:
                pass
                
            return settings_dict
            
        except Exception as e:
            print(f"[OBS] Failed to get recording settings: {e}")
            return None
        
    def set_recording_path(self, path: str):
        """Set OBS recording path (if you want to control it from config)"""
        if not self.client:
            raise ConnectionError("Not connected to OBS")
        
        try:
            # Note: Setting recording path might require specific OBS version
            # and might not be available in all WebSocket APIs
            self.client.set_record_directory(record_directory=path)
            print(f"[OBS] Recording path set to: {path}")
            return True
        except Exception as e:
            print(f"[OBS] Failed to set recording path (may not be supported): {e}")
            return False
    
    def get_last_recording_file(self):
        """Get the last recording file path (workaround method)"""
        if not self.client:
            raise ConnectionError("Not connected to OBS")
        
        try:
            # Try to get the last recording filename
            # Note: This might not be available in all OBS versions
            # We'll use a simpler approach - check the recording directory
            
            settings = self.get_recording_settings()
            if not settings or 'record_directory' not in settings:
                return None
            
            record_dir = settings['record_directory']
            if not record_dir:
                return None
            
            # List files in recording directory and find the most recent one
            import os
            from pathlib import Path
            
            record_path = Path(record_dir)
            if not record_path.exists():
                return None
            
            # Look for video files (common extensions)
            video_extensions = {'.mp4', '.mkv', '.flv', '.mov', '.ts', '.m3u8'}
            video_files = []
            
            for file in record_path.iterdir():
                if file.suffix.lower() in video_extensions and file.is_file():
                    video_files.append(file)
            
            if not video_files:
                return None
            
            # Return the most recently modified file
            latest_file = max(video_files, key=lambda f: f.stat().st_mtime)
            return str(latest_file)
            
        except Exception as e:
            print(f"[OBS] Failed to get last recording file: {e}")
            return None
        
    