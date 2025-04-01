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

# ロギングの設定
logger = logging.getLogger('CloudSync')
logger.setLevel(logging.WARNING)  # INFOからWARNINGに変更して出力を減らす
logger.propagate = False

# 既存のハンドラをクリア
if logger.handlers:
    logger.handlers.clear()

# 標準出力へのハンドラを追加
handler = logging.StreamHandler()
formatter = logging.Formatter('[Cloud Sync] - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# グローバル変数
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
        # 環境変数からS3の設定を取得
        self.aws_access_key_id = os.environ.get('S3_ACCESS_KEY_ID')
        self.aws_secret_access_key = os.environ.get('S3_SECRET_ACCESS_KEY')
        self.aws_region = os.environ.get('S3_REGION', 'us-east-1')
        self.s3_bucket = os.environ.get('S3_BUCKET')
        self.s3_prefix = os.environ.get('S3_PREFIX', 'comfyui-outputs')
        self.s3_endpoint_url = os.environ.get('S3_ENDPOINT_URL')
        
        # セッションIDの生成（起動時にユニークなIDでフォルダ分け）
        self.session_id = str(uuid.uuid4())
        upload_status["session_id"] = self.session_id
        
        # S3クライアントの初期化
        self.s3_client = None
        if self.aws_access_key_id and self.aws_secret_access_key and self.s3_bucket:
            try:
                # S3クライアントの設定
                client_kwargs = {
                    'aws_access_key_id': self.aws_access_key_id,
                    'aws_secret_access_key': self.aws_secret_access_key,
                    'region_name': self.aws_region
                }
                
                # S3互換エンドポイントが指定されている場合は追加
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
        """ファイルをS3にアップロードする"""
        if not self.s3_client:
            logger.error("S3 client not initialized. Cannot upload file.")
            upload_status["errors"].append("S3 client not initialized. Cannot upload file.")
            return False
        
        try:
            # アップロード開始を示すフラグを設定
            upload_status["uploading"] = True
            
            # ファイル名を取得
            file_name = os.path.basename(file_path)
            
            # ディレクトリ構造を保持するためのパス計算
            if base_dir:
                # base_dirからの相対パスを計算
                try:
                    rel_path = os.path.relpath(file_path, base_dir)
                    # Windowsパスを/に変換
                    rel_path = rel_path.replace('\\', '/')
                except ValueError:
                    # file_pathがbase_dirの外にある場合
                    rel_path = file_name
            else:
                # base_dirが指定されていない場合はファイル名のみ
                rel_path = file_name
            
            # S3のキーを生成（セッションIDでフォルダ分け、ディレクトリ構造を保持）
            s3_key = f"{self.s3_prefix}/{self.session_id}/{rel_path}"
            
            # ファイルをアップロード
            self.s3_client.upload_file(file_path, self.s3_bucket, s3_key)
            
            # アップロード成功の情報を記録
            upload_status["uploaded_files"] += 1
            upload_status["last_upload_time"] = datetime.now().isoformat()
            
            # 最近のアップロード履歴に追加（最大10件）
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
            
            # アップロード完了を示すフラグを設定
            upload_status["uploading"] = False
            return True
            
        except Exception as e:
            error_msg = f"Failed to upload {file_path}: {str(e)}"
            logger.error(error_msg)
            upload_status["errors"].append(error_msg)
            upload_status["failed_files"] += 1
            
            # エラー時もアップロード完了を示すフラグを設定
            upload_status["uploading"] = False
            return False

class CloudSyncHandler(FileSystemEventHandler):
    def __init__(self, uploader: S3Uploader, output_dir: str):
        self.uploader = uploader
        self.output_dir = output_dir
        # ファイルサイズ安定化待機の設定
        self.max_wait_time = 30  # 最大待機時間（秒）
        self.check_interval = 0.5  # チェック間隔（秒）
        self.size_stable_count = 3  # サイズが安定したと判断するためのチェック回数
    
    def wait_for_file_completion(self, file_path: str) -> bool:
        """ファイルが完全に書き込まれるまで待機する"""
        start_time = time.time()
        last_size = -1
        stable_count = 0
        
        logger.debug(f"Waiting for file to stabilize: {file_path}")
        
        while time.time() - start_time < self.max_wait_time:
            try:
                # ファイルが存在するか確認
                if not os.path.exists(file_path):
                    logger.warning(f"File disappeared while waiting: {file_path}")
                    return False
                
                # 現在のファイルサイズを取得
                current_size = os.path.getsize(file_path)
                
                # ファイルサイズが前回と同じかチェック
                if current_size == last_size:
                    stable_count += 1
                    if stable_count >= self.size_stable_count:
                        logger.debug(f"File size stabilized at {current_size} bytes after {time.time() - start_time:.2f} seconds")
                        return True
                else:
                    stable_count = 0
                    last_size = current_size
                
                # 少し待機
                time.sleep(self.check_interval)
                
            except (IOError, OSError) as e:
                # ファイルがロックされているか、アクセスできない場合
                logger.warning(f"Error accessing file {file_path}: {str(e)}")
                time.sleep(self.check_interval)
        
        logger.warning(f"Timed out waiting for file to stabilize: {file_path}")
        # タイムアウトしても最善を尽くしてアップロードを試みる
        return True
    
    def on_created(self, event):
        if not event.is_directory:
            file_path = event.src_path
            
            # 全てのファイルをアップロード対象とする
            logger.debug(f"New file detected: {file_path}")
            upload_status["total_files"] += 1
            
            # ファイルが完全に書き込まれるまで待機
            if self.wait_for_file_completion(file_path):
                # アップロード（output_dirからの相対パスを保持）
                self.uploader.upload_file(file_path, self.output_dir)
            else:
                error_msg = f"Failed to upload {file_path}: File was not stable"
                logger.error(error_msg)
                upload_status["errors"].append(error_msg)
                upload_status["failed_files"] += 1

def start_watcher(output_dir: str) -> Optional[Observer]:
    """出力ディレクトリの監視を開始する"""
    # S3アップローダーの初期化
    uploader = S3Uploader()
    
    # 出力ディレクトリが存在するか確認
    if not os.path.exists(output_dir):
        error_msg = f"Output directory does not exist: {output_dir}"
        logger.error(error_msg)
        upload_status["errors"].append(error_msg)
        return None
    
    try:
        # ファイルシステムイベントハンドラの設定
        event_handler = CloudSyncHandler(uploader, output_dir)
        observer = Observer()
        # 再帰的に監視してサブディレクトリも対象にする
        observer.schedule(event_handler, output_dir, recursive=True)
        observer.start()
        
        upload_status["running"] = True
        logger.info(f"Cloud Sync: Started watching directory: {output_dir}")
        return observer
    except Exception as e:
        error_msg = f"Failed to start directory watcher: {str(e)}"
        logger.error(error_msg)
        upload_status["errors"].append(error_msg)
        return None

def stop_watcher(observer: Observer):
    """ディレクトリの監視を停止する"""
    if observer:
        observer.stop()
        observer.join()
        upload_status["running"] = False
        logger.info("Cloud Sync: Stopped directory watcher")

# グローバル変数
observer = None
output_dir = None

def setup_routes():
    """APIエンドポイントのセットアップ"""
    @PromptServer.instance.routes.get("/cloud-sync/status")
    async def get_status(request):
        """クラウド同期の状態を取得するエンドポイント"""
        tmp = upload_status

        # errorsとrecent_uploadsを削除
        tmp.pop("errors", None)
        tmp.pop("recent_uploads", None)
        return web.json_response(tmp)
    
    @PromptServer.instance.routes.post("/cloud-sync/start")
    async def start_uploader(request):
        """クラウド同期を開始するエンドポイント"""
        global observer, output_dir
        
        try:
            # 外部からの出力ディレクトリ指定は危ないのでなし
            # data = await request.json()
            # new_output_dir = data.get("output_dir")
            new_output_dir = None
            
            # 出力ディレクトリが指定されていない場合はデフォルトを使用
            if not new_output_dir:
                # ComfyUIのデフォルト出力ディレクトリを使用
                new_output_dir = folder_paths.get_output_directory()
            
            # 既に実行中の場合は停止
            if observer:
                stop_watcher(observer)
            
            # 状態をリセット
            upload_status["running"] = False
            upload_status["uploading"] = False
            upload_status["total_files"] = 0
            upload_status["uploaded_files"] = 0
            upload_status["failed_files"] = 0
            upload_status["last_upload_time"] = None
            upload_status["errors"] = []
            upload_status["recent_uploads"] = []
            
            # 新しい監視を開始
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
    
    @PromptServer.instance.routes.post("/cloud-sync/stop")
    async def stop_uploader(request):
        """クラウド同期を停止するエンドポイント"""
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
    
    @PromptServer.instance.routes.post("/cloud-sync/upload")
    async def manual_upload(request):
        """特定のファイルを手動でアップロードするエンドポイント"""
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
            
            # S3アップローダーの初期化
            uploader = S3Uploader()
            upload_status["total_files"] += 1
            
            # アップロード（output_dirが指定されている場合はそれを基準にする）
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

    # 起動時に自動的に監視を開始
    def start_default_watcher():
        global observer, output_dir
        
        # ComfyUIのデフォルト出力ディレクトリを使用
        output_dir = folder_paths.get_output_directory()
        
        # 監視を開始
        observer = start_watcher(output_dir)
        if observer:
            logger.info(f"Cloud Sync: Automatically started watching directory: {output_dir}")
        else:
            logger.error("Failed to automatically start watcher")
    
    # 別スレッドで監視を開始（ComfyUIの起動を妨げないため）
    threading.Thread(target=start_default_watcher).start()

    logger.debug("CloudSync routes have been set up")