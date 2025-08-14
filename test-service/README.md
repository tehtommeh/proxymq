# Test Service

A simple FastAPI service for testing HTTP interactions with configurable delays and various response types.

## Configuration

Environment variables:
- `STARTUP_DELAY_SECONDS` - Delay before service becomes ready (default: 0)
- `REQUEST_PROCESSING_SECONDS` - Base processing time for requests (default: 0)

## Endpoints

### GET /health
Returns service health status. Always responds immediately.

### GET /
Root endpoint with configurable delay.

### GET /success
Returns 200 OK with configurable delay + random 0-1s additional delay.

### POST /success
Echoes back the request body with configurable delay + random 0-1s additional delay.

### GET/POST /error422
Returns 422 error immediately (no delay).

### GET/POST /error500
Returns 500 error with configurable delay + random 0-1s additional delay.

## Usage

```bash
# Build and run
docker build -t test-service .
docker run -p 8080:8000 -e REQUEST_PROCESSING_SECONDS=2 test-service

# Test endpoints
curl http://localhost:8080/health
curl -X POST -d '{"test": "data"}' http://localhost:8080/success
```