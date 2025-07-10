# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ProxyMQ is a scalable API-to-microservice queueing system that decouples API request handling from downstream microservice processing using RabbitMQ. The system consists of three main Python services:

- **Enqueuer**: Sidecar that receives HTTP requests and enqueues them to RabbitMQ
- **Dequeuer**: Sidecar that pulls requests from RabbitMQ and forwards them to microservices
- **URL-Fetcher**: Example microservice that demonstrates batch processing capabilities

## Development Commands

### Local Development
```bash
# Start all services with docker-compose
docker-compose up

# Build and run individual services
docker build -t enqueuer ./enqueuer
docker build -t dequeuer ./dequeuer
docker build -t url-fetcher ./url-fetcher
```

### Build and Deployment
```bash
# Build Docker images
make build-all           # Build both enqueuer and dequeuer
make build-enqueuer      # Build only enqueuer
make build-dequeuer      # Build only dequeuer

# Push to registry (requires REGISTRY env var)
make push-all            # Push both images
make deploy-all          # Build and push both images
```

### Kubernetes Deployment
```bash
# Deploy using Helm chart
helm install proxymq ./k8s/queuer

# Update deployment
helm upgrade proxymq ./k8s/queuer
```

## Architecture

### Core Components
- **FastAPI**: Web framework for all services
- **aio_pika**: Async RabbitMQ client with connection pooling
- **httpx**: HTTP client for downstream requests
- **prometheus_client**: Metrics collection and exposition
- **pydantic-settings**: Environment-based configuration

### Message Flow
1. Client → API → Enqueuer (HTTP)
2. Enqueuer → RabbitMQ (publish to `{service}_requests` queue)
3. Dequeuer → RabbitMQ (consume from queue)
4. Dequeuer → Microservice (HTTP, single or batch)
5. Microservice → Dequeuer → RabbitMQ (response to reply queue)
6. Enqueuer → RabbitMQ (consume response) → API → Client

### Queue Structure
- **Request queues**: `{service}_requests` (durable, per-service)
- **Reply queues**: `reply_{correlation_id}` (exclusive, auto-delete, per-request)
- **Correlation IDs**: UUID4 for request/response matching

## Configuration

### Environment Variables
All services use pydantic-settings for configuration:

**Enqueuer**:
- `RABBITMQ_HOST`: RabbitMQ hostname (default: "rabbitmq")
- `RABBITMQ_PORT`: RabbitMQ port (default: 5672)
- `ENQUEUER_REPLY_TIMEOUT`: Reply timeout in seconds (default: 120)
- `ENQUEUER_POOL_SIZE`: Connection pool size (default: 10)

**Dequeuer**:
- `SERVICE_NAME`: Service identifier for queue naming
- `DOWNSTREAM_URL`: Target microservice URL
- `BATCH_MODE`: Enable batch processing (default: false)
- `BATCH_DOWNSTREAM_URL`: Batch processing endpoint
- `DEQUEUER_POOL_SIZE`: Connection pool size (default: 10)

**URL-Fetcher**:
- `BATCH_SIZE`: Maximum batch size for processing (default: 10)

## Monitoring

### Prometheus Metrics
- Enqueuer exposes metrics on `/metrics` endpoint
- Dequeuer runs metrics server on port 8001
- Key metrics: request counts, latencies, response codes, processing times

### Grafana Dashboard
- Pre-configured dashboard in `grafana/provisioning/dashboards/`
- Accessible at `http://localhost:3000` (admin/admin)

## Development Notes

### Service Structure
Each service follows the same pattern:
- `main.py`: FastAPI application with async RabbitMQ handling
- `settings.py`: Pydantic settings for environment configuration
- `requirements.txt`: Python dependencies
- `Dockerfile`: Multi-stage build for production deployment

### Adding New Services
1. Create new dequeuer instance with appropriate `SERVICE_NAME`
2. Configure `DOWNSTREAM_URL` to point to your microservice
3. Add queue configuration in deployment manifests
4. For batch processing, implement `/batch` endpoint and set `BATCH_MODE=true`

### Batch Processing
- **HTTP Methods**: Batch mode now supports GET, POST, and PUT methods
- **Request Structure**: Each batch request preserves the original HTTP method, headers, and body
- **Downstream Format**: Batch requests are sent as JSON with method, headers, body, and correlation_id
- **Response Handling**: Individual responses are routed back to their respective reply queues
- **Error Handling**: Failed batch requests send error responses to all affected messages

### Error Handling
- Services use correlation IDs for request tracking
- Failed messages should be handled with proper nack/requeue logic
- Consider implementing dead letter queues for production use

### Connection Management
- All services use connection pooling for RabbitMQ
- Pool sizes are configurable via environment variables
- Connections are managed at the application lifecycle level