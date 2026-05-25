docker run --rm memfaultos:latest > out.log
docker build -t memfaultos:latest .

docker run --rm -v $(pwd)/output:/workspace/output memfaultos-builder
docker run --rm -v "$(pwd):/workspace" -v memfaultos-cache:/opt/memfaultos memfaultos-builder:latest