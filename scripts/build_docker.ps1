# build_docker.ps1
docker build -t wuyou:latest .
docker tag wuyou:latest ghcr.io/your-username/wuyou:latest
docker push ghcr.io/your-username/wuyou:latest
