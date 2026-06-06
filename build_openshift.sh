#!/bin/bash
# build_openshift.sh
# Builds the images for linux/amd64 so they can be run on OpenShift

set -e

TAG="latest"

echo "🔨 Building Frontend (linux/amd64)..."
docker buildx build --platform linux/amd64 --load -t my-frontend:${TAG} ./frontend

echo "🔨 Building Backend (linux/amd64)..."
docker buildx build --platform linux/amd64 --load --secret id=deploy_key,src=./deploy_key -f ./backend/Dockerfile -t my-backend:${TAG} .

echo "🔨 Building Agent (linux/amd64)..."
docker buildx build --platform linux/amd64 --load --secret id=deploy_key,src=./deploy_key -f ./agent/Dockerfile -t my-agent:${TAG} .

echo "📦 Creating openshift-images directory..."
mkdir -p openshift-images

echo "📦 Saving Frontend image to openshift-images/frontend.tar..."
docker save -o openshift-images/frontend.tar my-frontend:${TAG}

echo "📦 Saving Backend image to openshift-images/backend.tar..."
docker save -o openshift-images/backend.tar my-backend:${TAG}

echo "📦 Saving Agent image to openshift-images/agent.tar..."
docker save -o openshift-images/agent.tar my-agent:${TAG}

echo "✅ Build and save complete!"
echo "The linux/amd64 images have been saved as separate .tar files in the 'openshift-images' folder."
echo ""
echo "You can transfer this folder to your OpenShift environment and load them using:"
echo "docker load -i openshift-images/frontend.tar"
echo "docker load -i openshift-images/backend.tar"
echo "docker load -i openshift-images/agent.tar"
