#!/bin/bash

# Simple BabilCORS Deployment
# Run this on your VPS via Termius

echo "🚀 Simple BabilCORS Deployment"
echo "==============================="

# Pull and run container
docker pull ghcr.io/srknsntrk1991/babilcors-caster:latest

# Stop existing container if running
docker stop babilcors 2>/dev/null || true
docker rm babilcors 2>/dev/null || true

# Create config directory
mkdir -p /opt/babilcors/config

# Download config if not exists
if [ ! -f /opt/babilcors/config/caster_config.json ]; then
    echo "Downloading config..."
    curl -s -o /opt/babilcors/config/caster_config.json \
        https://raw.githubusercontent.com/srknsntrk1991/babilcors/master/config/caster_config.docker.json
fi

# Run container
docker run -d \
  --name babilcors \
  --restart unless-stopped \
  -p 2101:2101 \
  -p 8001:8001 \
  -v /opt/babilcors/config:/app/config \
  ghcr.io/srknsntrk1991/babilcors-caster:latest

echo "✅ Container started!"
echo ""
echo "Check status: docker ps"
echo "View logs: docker logs -f babilcors"
echo "Test health: curl http://localhost:2101/healthz"
echo ""
echo "📡 Services available at:"
echo "NTRIP: http://$(hostname -I | awk '{print $1}'):2101"
echo "API:   http://$(hostname -I | awk '{print $1}'):8001"