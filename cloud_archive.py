import os
import time
import uuid
import json
import logging
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

import boto3
from botocore.exceptions import ClientError
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from aiohttp import web
from server import PromptServer

import folder_paths

# Logging configuration
logger = logging.getLogger('CloudArchive')
logger.setLevel(logging.INFO)
logger.propagate = False

# Clear existing handlers
if logger.handlers:
    logger.handlers.clear()

# Add handler for standard output
handler = logging.StreamHandler()
formatter = logging.Formatter('[Cloud Archive] - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Global variables
upload_status = {
    "running": False,
    "uploading": False,
    "session_id": "",
    "total_files": 0,
    "uploaded_files": 0,
    "failed_files": 0,
    "last_upload_time": None,
    "errors": [],
    "recent_uploads": []
}

class S3Uploader:
    def __init__(self):
        # Get S3 configuration from environment variables
        self.aws_access_key_id = os.environ.get('S3_ACCESS_KEY_ID')
        self.aws_secret_access_key = os.environ.get('S3_SECRET_ACCESS_KEY')
        self.aws_region = os.environ.get('S3_REGION', 'us-east-1')
        self.s3_bucket = os.environ.get('S3_BUCKET')
        self.s3_prefix = os.environ.get('S3_PREFIX', 'comfyui-outputs')
        self.s3_endpoint_url = os.environ.get('S3_ENDPOINT_URL')
        
        # Generate session ID (create unique folder at startup)
        self.session_id = str(uuid.uuid4())[:13]
        upload_status["session_id"] = self.session_id
        
        # Initialize S3 client
        self.s3_client = None
        if self.aws_access_key_id and self.aws_secret_access_key and self.s3_bucket:
            try:
                # Configure S3 client
                client_kwargs = {
                    'aws_access_key_id': self.aws_access_key_id,
                    'aws_secret_access_key': self.aws_secret_access_key,
                    'region_name': self.aws_region
                }
                
                # Add S3-compatible endpoint if specified
                if self.s3_endpoint_url:
                    client_kwargs['endpoint_url'] = self.s3_endpoint_url
                
                self.s3_client = boto3.client('s3', **client_kwargs)
                endpoint_info = f", Endpoint: {self.s3_endpoint_url}" if self.s3_endpoint_url else ""
                logger.debug(f"S3 client initialized. Bucket: {self.s3_bucket}, Prefix: {self.s3_prefix}{endpoint_info}, Session ID: {self.session_id}")
            except Exception as e:
                logger.error(f"Failed to initialize S3 client: {str(e)}")
                upload_status["errors"].append(f"S3 client initialization error: {str(e)}")
        else:
            missing_vars = []
            if not self.aws_access_key_id:
                missing_vars.append("S3_ACCESS_KEY_ID")
            if not self.aws_secret_access_key:
                missing_vars.append("S3_SECRET_ACCESS_KEY")
            if not self.s3_bucket:
                missing_vars.append("S3_BUCKET")
            
            error_msg = f"Missing required environment variables: {', '.join(missing_vars)}"
            logger.error(error_msg)
            upload_status["errors"].append(error_msg)
    
    def upload_file(self, file_path: str, base_dir: str = None) -> bool:
        """Upload a file to S3"""
        if not self.s3_client:
            logger.error("S3 client not initialized. Cannot upload file.")
            upload_status["errors"].append("S3 client not initialized. Cannot upload file.")
            return False
        
        try:
            # Set flag indicating upload has started
            upload_status["uploading"] = True
            
            # Get the filename
            file_name = os.path.basename(file_path)
            
            # Calculate path to preserve directory structure
            if base_dir:
                # Calculate relative path from base_dir
                try:
                    rel_path = os.path.relpath(file_path, base_dir)
                    # Convert Windows paths to /
                    rel_path = rel_path.replace('\\', '/')
                except ValueError:
                    # If file_path is outside base_dir
                    rel_path = file_name
            else:
                # If base_dir is not specified, use only the filename
                rel_path = file_name
            
            # Generate S3 key (organize by session ID, preserve directory structure)
            s3_key = f"{self.s3_prefix}/{self.session_id}/{rel_path}"
            
            # Upload the file
            self.s3_client.upload_file(file_path, self.s3_bucket, s3_key)
            
            # Record successful upload information
            upload_status["uploaded_files"] += 1
            upload_status["last_upload_time"] = datetime.now().isoformat()
            
            # Add to recent upload history (maximum 10 entries)
            upload_info = {
                "file_name": file_name,
                "s3_key": s3_key,
                "upload_time": upload_status["last_upload_time"],
                "size_bytes": os.path.getsize(file_path)
            }
            upload_status["recent_uploads"].append(upload_info)
            if len(upload_status["recent_uploads"]) > 10:
                upload_status["recent_uploads"].pop(0)
            logger.debug(f"Successfully uploaded {file_path} to s3://{self.s3_bucket}/{s3_key}")
            
            # Set flag indicating upload is complete
            upload_status["uploading"] = False
            return True
            
        except Exception as e:
            error_msg = f"Failed to upload {file_path}: {str(e)}"
            logger.error(error_msg)
            upload_status["errors"].append(error_msg)
            upload_status["failed_files"] += 1
            
            # Set flag indicating upload is complete even on error
            upload_status["uploading"] = False
            return False

class CloudArchiveHandler(FileSystemEventHandler):
    def __init__(self, uploader: S3Uploader, output_dir: str):
        self.uploader = uploader
        self.output_dir = output_dir
        # Configuration for waiting for file size stabilization
        self.max_wait_time = 60*5  # Maximum wait time (seconds)
        self.check_interval = 0.5  # Check interval (seconds)
        self.size_stable_count = 3  # Number of checks to determine if size has stabilized
    
    def wait_for_file_completion(self, file_path: str) -> bool:
        """Wait until the file is completely written"""
        start_time = time.time()
        last_size = -1
        stable_count = 0
        
        logger.debug(f"Waiting for file to stabilize: {file_path}")
        
        while time.time() - start_time < self.max_wait_time:
            try:
                # Check if the file exists
                if not os.path.exists(file_path):
                    logger.warning(f"File disappeared while waiting: {file_path}")
                    return False
                
                # Get current file size
                current_size = os.path.getsize(file_path)
                
                # For non-zero files, check stabilization
                if current_size > 0:
                    if current_size == last_size:
                        stable_count += 1
                        if stable_count >= self.size_stable_count:
                            logger.debug(f"File size stabilized at {current_size} bytes after {time.time() - start_time:.2f} seconds")
                            return True
                    else:
                        stable_count = 0
                        last_size = current_size
                
                # Wait for next check
                time.sleep(self.check_interval)
                
            except (IOError, OSError) as e:
                # If the file is locked or inaccessible
                logger.warning(f"Error accessing file {file_path}: {str(e)}")
                time.sleep(self.check_interval)
        
        logger.warning(f"Timed out waiting for file to stabilize: {file_path}")
        # Try to upload even if timed out, doing our best
        return True
    
    def on_created(self, event):
        if not event.is_directory:
            file_path = event.src_path
            
            # Consider all files as upload targets
            logger.debug(f"New file detected: {file_path}")
            upload_status["total_files"] += 1
            
            # Wait until the file is completely written
            if self.wait_for_file_completion(file_path):
                # Upload (preserving relative path from output_dir)
                self.uploader.upload_file(file_path, self.output_dir)
            else:
                error_msg = f"Failed to upload {file_path}: File was not stable"
                logger.error(error_msg)
                upload_status["errors"].append(error_msg)
                upload_status["failed_files"] += 1

def start_watcher(output_dir: str) -> Optional[Observer]:
    """Start monitoring the output directory"""
    # Initialize S3 uploader
    uploader = S3Uploader()
    
    # Check if output directory exists
    if not os.path.exists(output_dir):
        error_msg = f"Output directory does not exist: {output_dir}"
        logger.error(error_msg)
        upload_status["errors"].append(error_msg)
        return None
    
    try:
        # Set up filesystem event handler
        event_handler = CloudArchiveHandler(uploader, output_dir)
        observer = Observer()
        # Monitor recursively to include subdirectories
        observer.schedule(event_handler, output_dir, recursive=True)
        observer.start()
        
        upload_status["running"] = True
        logger.info(f"Cloud Archive: Started watching directory: {output_dir}")
        return observer
    except Exception as e:
        error_msg = f"Failed to start directory watcher: {str(e)}"
        logger.error(error_msg)
        upload_status["errors"].append(error_msg)
        return None

def stop_watcher(observer: Observer):
    """Stop monitoring the directory"""
    if observer:
        observer.stop()
        observer.join()
        upload_status["running"] = False
        logger.info("Cloud Archive: Stopped directory watcher")

# Global variables
observer = None
output_dir = None

def setup_routes():
    """Set up API endpoints"""
    @PromptServer.instance.routes.get("/cloud-archive/status")
    async def get_status(request):
        """Endpoint to get cloud sync status"""
        return web.json_response(upload_status)
    
    @PromptServer.instance.routes.post("/cloud-archive/start")
    async def start_uploader(request):
        """Endpoint to start cloud sync"""
        global observer, output_dir
        
        try:
            # No output directory specification from outside as it's dangerous
            # data = await request.json()
            # new_output_dir = data.get("output_dir")
            new_output_dir = None
            
            # Use default if output directory is not specified
            if not new_output_dir:
                # Use ComfyUI's default output directory
                new_output_dir = folder_paths.get_output_directory()
            
            # Stop if already running
            if observer:
                stop_watcher(observer)
            
            # Reset status
            upload_status["running"] = False
            upload_status["uploading"] = False
            upload_status["total_files"] = 0
            upload_status["uploaded_files"] = 0
            upload_status["failed_files"] = 0
            upload_status["last_upload_time"] = None
            upload_status["errors"] = []
            upload_status["recent_uploads"] = []
            
            # Start new monitoring
            output_dir = new_output_dir
            observer = start_watcher(output_dir)
            
            if observer:
                return web.json_response({
                    "success": True,
                    "message": f"Started watching directory: {output_dir}",
                    "status": upload_status
                })
            else:
                return web.json_response({
                    "success": False,
                    "message": "Failed to start watcher. Check logs for details.",
                    "status": upload_status
                }, status=500)
                
        except Exception as e:
            error_msg = f"Error starting uploader: {str(e)}"
            logger.error(error_msg)
            return web.json_response({
                "success": False,
                "message": error_msg,
                "status": upload_status
            }, status=500)
    
    @PromptServer.instance.routes.post("/cloud-archive/stop")
    async def stop_uploader(request):
        """Endpoint to stop cloud sync"""
        global observer
        
        if observer:
            stop_watcher(observer)
            observer = None
            return web.json_response({
                "success": True,
                "message": "Stopped watching directory",
                "status": upload_status
            })
        else:
            return web.json_response({
                "success": False,
                "message": "Watcher is not running",
                "status": upload_status
            })
    
    @PromptServer.instance.routes.post("/cloud-archive/upload")
    async def manual_upload(request):
        """Endpoint to manually upload a specific file"""
        try:
            data = await request.json()
            file_path = data.get("file_path")
            
            if not file_path:
                return web.json_response({
                    "success": False,
                    "message": "No file path provided",
                    "status": upload_status
                }, status=400)
            
            if not os.path.exists(file_path):
                return web.json_response({
                    "success": False,
                    "message": f"File does not exist: {file_path}",
                    "status": upload_status
                }, status=404)
            
            # Initialize S3 uploader
            uploader = S3Uploader()
            upload_status["total_files"] += 1
            
            # Upload (using output_dir as reference if specified)
            success = uploader.upload_file(file_path, output_dir)
            
            return web.json_response({
                "success": success,
                "message": f"{'Successfully uploaded' if success else 'Failed to upload'} {file_path}",
                "status": upload_status
            })
            
        except Exception as e:
            error_msg = f"Error uploading file: {str(e)}"
            logger.error(error_msg)
            return web.json_response({
                "success": False,
                "message": error_msg,
                "status": upload_status
            }, status=500)

    # Automatically start monitoring at startup
    def start_default_watcher():
        global observer, output_dir
        
        # Use ComfyUI's default output directory
        output_dir = folder_paths.get_output_directory()
        
        # Start monitoring
        observer = start_watcher(output_dir)
        if observer:
            logger.info(f"Cloud Archive: Automatically started watching directory: {output_dir}")
        else:
            logger.error("Failed to automatically start watcher")
    
    # Start monitoring in a separate thread (to avoid blocking ComfyUI startup)
    threading.Thread(target=start_default_watcher).start()

    logger.debug("CloudArchive routes have been set up")