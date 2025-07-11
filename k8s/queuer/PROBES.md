# Kubernetes Pod Probes Configuration

This document explains the Pod probe configuration for the nginx and url-fetcher deployments that use the dequeuer for processing RabbitMQ jobs.

## Overview

The probe configuration ensures that during deployments, old pods are only removed once new pods have:
1. Successfully connected to RabbitMQ via the dequeuer
2. Confirmed the downstream service (nginx/url-fetcher) is healthy
3. Started accepting and processing jobs from RabbitMQ

## Probe Strategy

### Init Container (Dequeuer) Probes

**Readiness Probe on Dequeuer Init Container:**
- **Endpoint**: `http://localhost:8001/metrics` (Prometheus metrics)
- **Purpose**: Ensures the dequeuer has completed its health check and is ready to process RabbitMQ jobs
- **Key Metric**: Checks for presence of `dequeuer_ready_time_seconds` metric
- **Timing**: 
  - Initial delay: 5 seconds
  - Check interval: 5 seconds
  - Timeout: 3 seconds
  - Failure threshold: 3 attempts

**How it works:**
1. Dequeuer starts and runs `wait_for_downstream_health()`
2. Health check polls the downstream service (nginx/url-fetcher)
3. Once downstream is healthy, `dequeuer_ready_time_seconds` metric is set
4. Kubernetes readiness probe detects this metric and marks init container as ready
5. Only then does the main container start

### Main Container Probes

**Liveness Probe:**
- **Purpose**: Ensures the main service stays healthy during operation
- **Endpoint**: Service root path (`/` on port 8000 for url-fetcher, port 80 for nginx)
- **Timing**:
  - Initial delay: 30 seconds (allows time for startup)
  - Check interval: 10 seconds
  - Timeout: 5 seconds
  - Failure threshold: 3 attempts

**Readiness Probe:**
- **Purpose**: Determines when the main service is ready to receive traffic
- **Endpoint**: Service root path (`/` on port 8000 for url-fetcher, port 80 for nginx)
- **Timing**:
  - Initial delay: 5 seconds
  - Check interval: 5 seconds
  - Timeout: 3 seconds
  - Failure threshold: 3 attempts

## Deployment Behavior

### During Rolling Updates

1. **New Pod Creation**: 
   - Init container (dequeuer) starts
   - Dequeuer waits for downstream service health check
   - Readiness probe waits for `dequeuer_ready_time_seconds` metric

2. **Pod Readiness**:
   - Once dequeuer confirms downstream is healthy, init container becomes ready
   - Main container starts and its probes begin
   - Pod is marked ready only when both containers pass their readiness checks

3. **Traffic Routing**:
   - Kubernetes only routes traffic to ready pods
   - Old pods continue processing until new pods are fully ready

4. **Old Pod Termination**:
   - Old pods are terminated only after new pods are ready and receiving traffic
   - Ensures continuous job processing from RabbitMQ

### Failure Scenarios

**If Dequeuer Health Check Fails:**
- Init container readiness probe fails
- Pod never becomes ready
- Old pods continue running
- Deployment rollout is blocked until issue is resolved

**If Main Service Fails:**
- Liveness probe detects failure and restarts container
- Readiness probe removes pod from service endpoints
- Traffic is routed to healthy pods only

## Monitoring

The dequeuer exposes several Prometheus metrics on port 8001:

- `dequeuer_ready_time_seconds`: Unix timestamp when dequeuer became ready
- `dequeuer_health_check_attempts_total`: Total health check attempts
- `dequeuer_health_check_success_total`: Total successful health checks
- `dequeuer_health_check_duration_seconds`: Health check latency

These metrics provide visibility into the health check process and can be used for alerting and debugging.

## Configuration Details

### URL-Fetcher Deployment
- **Init Container Port**: 8001 (metrics)
- **Main Container Port**: 8000 (HTTP service)
- **Health Check Target**: `http://localhost:8000/`
- **Batch Mode**: Enabled

### Nginx Deployment
- **Init Container Port**: 8001 (metrics)
- **Main Container Port**: 80 (HTTP service)
- **Health Check Target**: `http://localhost:80/`
- **Batch Mode**: Disabled

## Benefits

1. **Zero Downtime Deployments**: Continuous job processing during rollouts
2. **Proper Health Signaling**: Uses actual readiness state of job processing system
3. **Kubernetes Native**: Leverages standard Kubernetes deployment strategies
4. **Observable**: Prometheus metrics provide visibility into health check process
5. **Fail-Safe**: Blocks deployments if health checks fail, preventing broken rollouts
