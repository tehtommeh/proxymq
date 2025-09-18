from pydantic import BaseModel
from typing import Optional, Dict, Any
from enum import Enum
import json


class JobStatus(str, Enum):
    """Job status enumeration"""
    PENDING = "pending"
    COMPLETED = "completed" 
    FAILED = "failed"


class EnqueueResult(BaseModel):
    """Result of an enqueue operation"""
    correlation_id: str
    success: bool
    error_message: Optional[str] = None
    status_code: Optional[int] = None


class ProxyResult(BaseModel):
    """Result of a proxy operation (sync mode)"""
    body: bytes
    headers: Dict[str, Any]
    status_code: int


class JobRequest(BaseModel):
    """Incoming job request data"""
    service: str
    method: str
    body: bytes
    headers: Dict[str, str]
    auth_token: Optional[str] = None


class JobData(BaseModel):
    """Job data for Redis storage and API responses"""
    job_id: str
    service: str
    status: JobStatus
    created_at: float
    completed_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    auth_token: Optional[str] = None

    def to_redis_dict(self) -> Dict[str, str]:
        """Convert to Redis-compatible string dictionary"""
        data = {
            "job_id": self.job_id,
            "service": self.service,
            "status": self.status.value,
            "created_at": str(self.created_at),
        }
        
        if self.completed_at is not None:
            data["completed_at"] = str(self.completed_at)
        if self.result is not None:
            import json
            data["result"] = json.dumps(self.result)
        if self.error is not None:
            data["error"] = self.error
        if self.auth_token is not None:
            data["auth_token"] = self.auth_token
            
        return data

    @classmethod
    def from_redis_dict(cls, data: Dict[str, str]) -> 'JobData':
        """Create JobData from Redis dictionary"""
        job_data: Dict[str, Any] = {
            "job_id": data["job_id"],
            "service": data["service"],
            "status": JobStatus(data["status"]),
            "created_at": float(data["created_at"]),
        }

        if "completed_at" in data and data["completed_at"]:
            job_data["completed_at"] = float(data["completed_at"])
        if "result" in data:
            try:
                job_data["result"] = json.loads(data["result"])
            except json.JSONDecodeError:
                pass
        if "error" in data:
            job_data["error"] = data["error"]
        if "auth_token" in data:
            job_data["auth_token"] = data["auth_token"]

        return cls(**job_data)

    def to_api_response(self) -> Dict[str, Any]:
        """Convert to API response format"""
        response: Dict[str, Any] = {
            "job_id": self.job_id,
            "status": self.status.value,
            "service": self.service,
            "created_at": self.created_at
        }
        
        if self.status == JobStatus.COMPLETED:
            if self.completed_at is not None:
                response["completed_at"] = self.completed_at
            if self.result is not None:
                response["result"] = self.result
        elif self.status == JobStatus.FAILED:
            if self.completed_at is not None:
                response["completed_at"] = self.completed_at
            if self.error is not None:
                response["error"] = self.error
                
        return response


class JobCreationResponse(BaseModel):
    """Response for job creation"""
    job_id: str
    status: JobStatus = JobStatus.PENDING
    message: str = "Job queued successfully"


class JobStatusResponse(BaseModel):
    """Response for job status queries"""
    job_id: str
    status: JobStatus
    service: str
    created_at: float
    completed_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


