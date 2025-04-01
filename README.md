# ComfyUI-CloudSync

ComfyUI-CloudSyncは、ComfyUIの出力ディレクトリを監視し、生成されたファイルを自動的にAmazon S3またはS3互換ストレージと同期するプラグインです。

## 機能

- ComfyUIの出力ディレクトリを監視し、新しいファイルを検出（サブディレクトリも含む）
- 検出されたファイルを自動的にS3またはS3互換ストレージにアップロード
- ファイルが完全に書き込まれるまでインテリジェントに待機（ファイルサイズの安定化を検出）
- 出力ディレクトリの内部構造を保持してアップロード
- 起動時にユニークなセッションIDを生成し、S3内でフォルダ分け
- RESTful APIエンドポイントによる状態確認と制御
- 環境変数による簡単な設定

## インストール

1. このリポジトリをComfyUIのカスタムノードディレクトリにクローンします：

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/yourusername/ComfyUI-CloudSync.git
```

2. 必要なPythonパッケージをインストールします：

```bash
pip install boto3 watchdog
```

## 設定

以下の環境変数を設定してください：

- `S3_ACCESS_KEY_ID` (必須): ストレージサービスのアクセスキーID
- `S3_SECRET_ACCESS_KEY` (必須): ストレージサービスのシークレットアクセスキー
- `S3_REGION` (オプション): ストレージサービスのリージョン（デフォルト: us-east-1）
- `S3_BUCKET` (必須): アップロード先のバケット名
- `S3_PREFIX` (オプション): バケット内のプレフィックス（デフォルト: comfyui-outputs）
- `S3_ENDPOINT_URL` (オプション): S3互換エンドポイントのURL（MinIO、Wasabi、DigitalOcean Spacesなど）

環境変数の設定例（Linux/Mac）：

```bash
export S3_ACCESS_KEY_ID=your_access_key
export S3_SECRET_ACCESS_KEY=your_secret_key
export S3_REGION=ap-northeast-1
export S3_BUCKET=your-bucket-name
export S3_PREFIX=comfyui/outputs
export S3_ENDPOINT_URL=https://minio.example.com
```

Windows:

```cmd
set S3_ACCESS_KEY_ID=your_access_key
set S3_SECRET_ACCESS_KEY=your_secret_key
set S3_REGION=ap-northeast-1
set S3_BUCKET=your-bucket-name
set S3_PREFIX=comfyui/outputs
set S3_ENDPOINT_URL=https://minio.example.com
```

### S3互換エンドポイントの使用

このプラグインは、Amazon S3だけでなく、S3互換のストレージサービスにも対応しています。以下のようなサービスで使用できます：

- [MinIO](https://min.io/)
- [Wasabi](https://wasabi.com/)
- [DigitalOcean Spaces](https://www.digitalocean.com/products/spaces)
- [Backblaze B2](https://www.backblaze.com/b2/cloud-storage.html)
- [Scaleway Object Storage](https://www.scaleway.com/en/object-storage/)
- その他のS3互換サービス

S3互換サービスを使用するには、`S3_ENDPOINT_URL`環境変数にエンドポイントのURLを設定してください。

#### MinIOの例：

```bash
export S3_ACCESS_KEY_ID=your_minio_access_key
export S3_SECRET_ACCESS_KEY=your_minio_secret_key
export S3_BUCKET=your-minio-bucket
export S3_ENDPOINT_URL=http://minio.example.com:9000
```

#### Wasabiの例：

```bash
export S3_ACCESS_KEY_ID=your_wasabi_access_key
export S3_SECRET_ACCESS_KEY=your_wasabi_secret_key
export S3_REGION=us-east-1
export S3_BUCKET=your-wasabi-bucket
export S3_ENDPOINT_URL=https://s3.wasabisys.com
```

#### DigitalOcean Spacesの例：

```bash
export S3_ACCESS_KEY_ID=your_spaces_key
export S3_SECRET_ACCESS_KEY=your_spaces_secret
export S3_REGION=nyc3
export S3_BUCKET=your-space-name
export S3_ENDPOINT_URL=https://nyc3.digitaloceanspaces.com
```

## 使い方

### 自動起動

プラグインはComfyUIの起動時に自動的に開始され、デフォルトの出力ディレクトリ（`ComfyUI/output`）を監視します。

### APIエンドポイント

以下のAPIエンドポイントが利用可能です：

#### 状態確認

```
GET /cloud-sync/status
```

レスポンス例：

```json
{
  "running": true,
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "total_files": 10,
  "uploaded_files": 8,
  "failed_files": 0,
  "last_upload_time": "2023-04-01T12:34:56",
  "errors": [],
  "recent_uploads": [
    {
      "file_name": "image_01.png",
      "s3_key": "comfyui-outputs/550e8400-e29b-41d4-a716-446655440000/image_01.png",
      "upload_time": "2023-04-01T12:34:56",
      "size_bytes": 1234567
    }
  ]
}
```

#### 監視開始

```
POST /cloud-sync/start
```

リクエスト本文（オプション）：

```json
{
  "output_dir": "/path/to/custom/output/directory"
}
```

#### 監視停止

```
POST /cloud-sync/stop
```

#### 手動アップロード

```
POST /cloud-sync/upload
```

リクエスト本文：

```json
{
  "file_path": "/path/to/file.png"
}
```

## S3のディレクトリ構造

アップロードされたファイルは以下の構造でS3に保存されます：

```
{S3_PREFIX}/{SESSION_ID}/{RELATIVE_PATH}
```

例：

```
comfyui-outputs/550e8400-e29b-41d4-a716-446655440000/image_01.png
comfyui-outputs/550e8400-e29b-41d4-a716-446655440000/subfolder/image_02.png
```

出力ディレクトリの内部構造（サブディレクトリなど）は保持されます。

## トラブルシューティング

### 環境変数が設定されていない

必要な環境変数が設定されていない場合、エラーメッセージがログに記録され、`/cloud-sync/status`エンドポイントの`errors`フィールドに表示されます。

### アップロードに失敗する

アップロードに失敗した場合、エラーメッセージがログに記録され、`/cloud-sync/status`エンドポイントの`errors`フィールドに表示されます。また、`failed_files`カウンターが増加します。

## ライセンス

MIT