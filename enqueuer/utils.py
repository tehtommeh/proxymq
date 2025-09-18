import time
from typing import Dict, Any, Optional
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram
from models import JobData, JobStatus


def create_error_response(error_message: str, detail: str, status_code: int) -> JSONResponse:
    """Create a standardized JSON error response"""
    return JSONResponse(
        content={"error": error_message, "detail": detail},
        status_code=status_code
    )


def create_json_response(data: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    """Create a standardized JSON response"""
    return JSONResponse(
        content=data,
        status_code=status_code
    )


def extract_auth_token(headers: Dict[str, str], auth_header: str) -> Optional[str]:
    """Extract auth token from request headers (case-insensitive)"""
    return headers.get(auth_header.lower()) or headers.get(auth_header)


def record_response_metrics(response_codes_counter: Counter, service: str, status_code: int):
    """Record response metrics consistently"""
    response_codes_counter.labels(service=service, status_code=str(status_code)).inc()


def get_error_message_for_status(status_code: int) -> str:
    """Get appropriate error message for status code"""
    if status_code == 503:
        return "Service unavailable"
    elif status_code == 404:
        return "Service not found"
    elif status_code == 504:
        return "Gateway Timeout"
    elif status_code == 401:
        return "Unauthorized"
    elif status_code == 403:
        return "Forbidden"
    else:
        return "Internal Server Error"


def create_job_data(job_id: str, service: str, status: JobStatus, 
                   result: Optional[Dict[str, Any]] = None, 
                   error: Optional[str] = None, 
                   auth_token: Optional[str] = None) -> JobData:
    """Create a new JobData instance with current timestamp"""
    current_time = time.time()
    return JobData(
        job_id=job_id,
        service=service,
        status=status,
        created_at=current_time,
        completed_at=current_time if status != JobStatus.PENDING else None,
        result=result,
        error=error,
        auth_token=auth_token
    )


class MetricsRecorder:
    """Helper class for recording metrics consistently"""
    
    def __init__(self, response_codes_counter: Counter, request_latency: Histogram, 
                 requests_failed: Counter):
        self.response_codes = response_codes_counter
        self.request_latency = request_latency  
        self.requests_failed = requests_failed
    
    def record_response(self, service: str, status_code: int):
        """Record response metrics"""
        self.response_codes.labels(service=service, status_code=str(status_code)).inc()
    
    def record_failure(self, service: str):
        """Record request failure"""
        self.requests_failed.labels(service=service).inc()
    
    def record_latency(self, service: str, duration: float):
        """Record request latency"""
        self.request_latency.labels(service=service).observe(duration)