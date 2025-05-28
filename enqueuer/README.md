# Enqueuer

This service acts as an HTTP-to-AMQP proxy for forwarding HTTP POST requests to a RabbitMQ queue and waiting for a response. It is intended to be used as a sidecar to the main API service.

## How it works
- Accepts HTTP POST requests at `/proxy/{service}`.
- Pushes the request body to a RabbitMQ queue named `{service}_requests`.
- Waits for a response on a reply queue and returns it to the HTTP client.

## Environment Variables
- `RABBITMQ_HOST`: Hostname of the RabbitMQ server (default: `rabbitmq`).
- `RABBITMQ_PORT`: Port of the RabbitMQ server (default: `5672`).

## Running Locally
```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Docker
This service is intended to be run as a container. See the main project `README.md` for usage. 