#!/usr/bin/env bash
# Google Cloud Shell で実行する。Web 画面を Cloud Run に置き、音声は Cloud Storage のバケットから読む。
#   git clone https://github.com/jfkr-solo/pdf2audiobook.git && cd pdf2audiobook
#   bash deploy/cloudrun.sh
# 2回目以降（コードを更新したとき）も同じコマンドで上書きされる。
set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-asia-northeast1}"          # 東京
SERVICE="${SERVICE:-pdf2audiobook}"
BUCKET="${BUCKET:-${PROJECT}-pdf2audiobook}"
[ -n "$PROJECT" ] || { echo "gcloud config set project <プロジェクトID> を先に実行してください"; exit 1; }
echo "プロジェクト: $PROJECT  リージョン: $REGION  バケット: gs://$BUCKET"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com storage.googleapis.com

if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
fi

if [ -z "${APP_PASSWORD:-}" ]; then
  read -r -s -p "Web 画面のパスワードを決めて入力してください: " APP_PASSWORD; echo
fi
[ ${#APP_PASSWORD} -ge 8 ] || { echo "パスワードは8文字以上にしてください"; exit 1; }

gcloud run deploy "$SERVICE" --source . --region "$REGION" \
  --allow-unauthenticated \
  --execution-environment gen2 \
  --add-volume name=data,type=cloud-storage,bucket="$BUCKET",readonly=true \
  --add-volume-mount volume=data,mount-path=/data \
  --set-env-vars "APP_PASSWORD=${APP_PASSWORD}" \
  --memory 512Mi --cpu 1 --min-instances 0 --max-instances 1 --timeout 3600

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "APP_BASE_URL=${URL}" >/dev/null
echo
echo "できました: $URL"
echo "音声のアップロード: gcloud storage cp -r BOOK001 gs://$BUCKET/"
echo "（BOOK001/manuscript.json と BOOK001/audio/*.mp3 があれば聞けます）"
