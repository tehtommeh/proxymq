# Docker registry configuration
ENQUEUER_IMAGE := $(REGISTRY)/enqueuer:latest
DEQUEUER_IMAGE := $(REGISTRY)/dequeuer:latest

# Default target
.PHONY: help
help:
	@echo "Available targets:"
	@echo "  build-enqueuer    - Build enqueuer Docker image"
	@echo "  build-dequeuer    - Build dequeuer Docker image"
	@echo "  build-all         - Build both Docker images"
	@echo "  push-enqueuer     - Push enqueuer Docker image to registry"
	@echo "  push-dequeuer     - Push dequeuer Docker image to registry"
	@echo "  push-all          - Push both Docker images to registry"
	@echo "  deploy-enqueuer   - Build and push enqueuer image"
	@echo "  deploy-dequeuer   - Build and push dequeuer image"
	@echo "  deploy-all        - Build and push both images"

# Build targets
.PHONY: build-enqueuer
build-enqueuer:
	@echo "Building enqueuer Docker image..."
	docker build -t $(ENQUEUER_IMAGE) ./enqueuer
	@echo "Enqueuer image built successfully: $(ENQUEUER_IMAGE)"

.PHONY: build-dequeuer
build-dequeuer:
	@echo "Building dequeuer Docker image..."
	docker build -t $(DEQUEUER_IMAGE) ./dequeuer
	@echo "Dequeuer image built successfully: $(DEQUEUER_IMAGE)"

.PHONY: build-all
build-all: build-enqueuer build-dequeuer

# Push targets
.PHONY: push-enqueuer
push-enqueuer:
	@echo "Pushing enqueuer Docker image to registry..."
	docker push $(ENQUEUER_IMAGE)
	@echo "Enqueuer image pushed successfully"

.PHONY: push-dequeuer
push-dequeuer:
	@echo "Pushing dequeuer Docker image to registry..."
	docker push $(DEQUEUER_IMAGE)
	@echo "Dequeuer image pushed successfully"

.PHONY: push-all
push-all: push-enqueuer push-dequeuer

# Deploy targets (build + push)
.PHONY: deploy-enqueuer
deploy-enqueuer: build-enqueuer push-enqueuer

.PHONY: deploy-dequeuer
deploy-dequeuer: build-dequeuer push-dequeuer

.PHONY: deploy-all
deploy-all: build-all push-all
