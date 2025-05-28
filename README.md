# proxymq

proxymq is a set of lightweight proxies for bridging HTTP APIs and RabbitMQ message queues. It enables asynchronous, decoupled communication between services using a request/reply pattern, and is designed to be used as sidecars for microservices.

## Components

### Enqueuer
- **Role:** HTTP-to-AMQP proxy
- **Description:** Accepts HTTP POST requests at `/proxy/{service}` and forwards the request body to a RabbitMQ queue named `{service}_requests`. Waits for a response on a reply queue and returns it to the HTTP client.
- **Intended use:** As a sidecar to the main API service, allowing HTTP clients to interact with backend services via RabbitMQ.

### Dequeuer
- **Role:** AMQP-to-HTTP proxy
- **Description:** Listens to a RabbitMQ queue named `{SERVICE_NAME}_requests`, forwards each message to a downstream HTTP API, and publishes the response to the reply queue for the original requester.
- **Intended use:** As a sidecar to a downstream service (e.g., addition or subtraction), enabling it to process requests from RabbitMQ and respond via HTTP.

## Environment Variables

### Enqueuer
- `RABBITMQ_HOST`: Hostname of the RabbitMQ server (default: `rabbitmq`)
- `RABBITMQ_PORT`: Port of the RabbitMQ server (default: `5672`)

### Dequeuer
- `RABBITMQ_HOST`: Hostname of the RabbitMQ server (default: `rabbitmq`)
- `RABBITMQ_PORT`: Port of the RabbitMQ server (default: `5672`)
- `SERVICE_NAME`: Name of the service (e.g., `addition`, `subtraction`)
- `DOWNSTREAM_URL`: URL of the downstream HTTP API to forward requests to

## Docker

Both services are intended to be run as containers. See the respective subdirectory READMEs for more details on Docker usage.
