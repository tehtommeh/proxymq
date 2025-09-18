#!/bin/bash

# ProxyMQ Enqueuer End-to-End Test Script
# Tests both synchronous and asynchronous modes

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SYNC_ENQUEUER="http://localhost:8000"
ASYNC_ENQUEUER="http://localhost:8100"
NGINX_SERVICE="nginx"
HTTPBIN_SERVICE="httpbin"
TEST_SERVICE="test"
TEST_PROCESSING_TIME=3  # Test service processing time in seconds

echo -e "${BLUE}🚀 ProxyMQ Enqueuer End-to-End Test${NC}"
echo "============================================"

# Function to print test results
print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✅ $2${NC}"
    else
        echo -e "${RED}❌ $2${NC}"
        exit 1
    fi
}

echo -e "\n${YELLOW}📋 Testing Synchronous Mode (port 8000)${NC}"
echo "----------------------------------------"

# Test 1: Sync GET request to nginx
echo "1. Testing sync GET request to nginx..."
echo "   → GET $SYNC_ENQUEUER/$NGINX_SERVICE"
SYNC_GET_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/sync_get_body.txt "$SYNC_ENQUEUER/$NGINX_SERVICE")
echo "   ← HTTP $SYNC_GET_RESPONSE ($(head -c 50 /tmp/sync_get_body.txt | tr '\n' ' ')...)"
if [ "$SYNC_GET_RESPONSE" = "200" ] && grep -q "Welcome to nginx" /tmp/sync_get_body.txt; then
    print_result 0 "Sync GET request to nginx successful (200 OK)"
else
    print_result 1 "Sync GET request to nginx failed"
fi

# Test 2: Sync POST request to nginx (should get 405 from nginx)
echo "2. Testing sync POST request to nginx..."
echo "   → POST $SYNC_ENQUEUER/$NGINX_SERVICE"
echo "   → Headers: Content-Type: application/json"
echo "   → Body: {\"test\": \"sync data\"}"
SYNC_POST_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/sync_post_body.txt \
    -X POST -H "Content-Type: application/json" -d '{"test": "sync data"}' \
    "$SYNC_ENQUEUER/$NGINX_SERVICE")
echo "   ← HTTP $SYNC_POST_RESPONSE ($(head -c 30 /tmp/sync_post_body.txt | tr '\n' ' ')...)"
if [ "$SYNC_POST_RESPONSE" = "405" ] && grep -q "405 Not Allowed" /tmp/sync_post_body.txt; then
    print_result 0 "Sync POST request to nginx successful (405 Method Not Allowed as expected)"
else
    print_result 1 "Sync POST request to nginx failed"
fi

# Test 3: Sync POST request to httpbin (should succeed)
echo "3. Testing sync POST request to httpbin..."
echo "   → POST $SYNC_ENQUEUER/$HTTPBIN_SERVICE"
echo "   → Headers: Content-Type: application/json"
echo "   → Body: {\"test\": \"sync httpbin data\"}"
SYNC_HTTPBIN_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/sync_httpbin_body.txt \
    -X POST -H "Content-Type: application/json" -d '{"test": "sync httpbin data"}' \
    "$SYNC_ENQUEUER/$HTTPBIN_SERVICE")
echo "   ← HTTP $SYNC_HTTPBIN_RESPONSE (Response: JSON with echoed data)"
if [ "$SYNC_HTTPBIN_RESPONSE" = "200" ] && grep -q '"test": "sync httpbin data"' /tmp/sync_httpbin_body.txt; then
    print_result 0 "Sync POST request to httpbin successful (200 OK)"
else
    print_result 1 "Sync POST request to httpbin failed"
fi

# Test 4: Sync metrics endpoint
echo "4. Testing sync metrics endpoint..."
echo "   → GET $SYNC_ENQUEUER/metrics"
SYNC_METRICS=$(curl -s "$SYNC_ENQUEUER/metrics")
echo "   ← HTTP 200 (Prometheus metrics format)"
if echo "$SYNC_METRICS" | grep -q "enqueuer_requests_total" && \
   echo "$SYNC_METRICS" | grep -q "enqueuer_response_codes_total"; then
    print_result 0 "Sync metrics endpoint working"
else
    print_result 1 "Sync metrics endpoint failed"
fi

echo -e "\n${YELLOW}⚡ Testing Asynchronous Mode (port 8100)${NC}"
echo "----------------------------------------"

# Test 5: Async job creation with httpbin
echo "5. Testing async job creation with httpbin..."
echo "   → POST $ASYNC_ENQUEUER/$HTTPBIN_SERVICE"
echo "   → Headers: Content-Type: application/json"
echo "   → Body: {\"test\": \"async httpbin data\"}"
ASYNC_CREATE_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/async_create_body.txt \
    -X POST -H "Content-Type: application/json" -d '{"test": "async httpbin data"}' \
    "$ASYNC_ENQUEUER/$HTTPBIN_SERVICE")

if [ "$ASYNC_CREATE_RESPONSE" = "202" ]; then
    JOB_ID=$(cat /tmp/async_create_body.txt | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)
    echo "   ← HTTP $ASYNC_CREATE_RESPONSE ($(cat /tmp/async_create_body.txt))"
    if [ -n "$JOB_ID" ]; then
        print_result 0 "Async job creation successful (Job ID: $JOB_ID)"
    else
        print_result 1 "Async job creation failed - no job ID returned"
    fi
else
    echo "   ← HTTP $ASYNC_CREATE_RESPONSE"
    print_result 1 "Async job creation failed (HTTP $ASYNC_CREATE_RESPONSE)"
fi

# Test 6: Check that job status endpoint works and validate result
echo "6. Testing async job status retrieval..."
echo "   → GET $ASYNC_ENQUEUER/jobs/$JOB_ID"
ASYNC_STATUS_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/async_status_body.txt \
    "$ASYNC_ENQUEUER/jobs/$JOB_ID")

if [ "$ASYNC_STATUS_RESPONSE" = "200" ]; then
    STATUS=$(cat /tmp/async_status_body.txt | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
    JOB_SERVICE=$(cat /tmp/async_status_body.txt | grep -o '"service":"[^"]*"' | cut -d'"' -f4)
    echo "   ← HTTP $ASYNC_STATUS_RESPONSE (Status: $STATUS, Service: $JOB_SERVICE)"
    
    if [ "$JOB_SERVICE" = "httpbin" ]; then
        if [ "$STATUS" = "completed" ]; then
            # Check if the result contains our test data
            if grep -q "async httpbin data" /tmp/async_status_body.txt; then
                print_result 0 "Async job completed successfully with correct result (Service: $JOB_SERVICE)"
            else
                print_result 1 "Async job completed but result is incorrect"
            fi
        elif [ "$STATUS" = "pending" ]; then
            print_result 0 "Async job status retrieval working (Status: $STATUS, Service: $JOB_SERVICE)"
        else
            print_result 1 "Async job status unexpected (Status: $STATUS, Service: $JOB_SERVICE)"
        fi
    else
        print_result 1 "Async job service incorrect (Expected: httpbin, Got: $JOB_SERVICE)"
    fi
else
    echo "   ← HTTP $ASYNC_STATUS_RESPONSE"
    print_result 1 "Async job status retrieval failed (HTTP $ASYNC_STATUS_RESPONSE)"
fi

# Test 7: Test non-existent job
echo "7. Testing non-existent job retrieval..."
FAKE_JOB_ID="00000000-0000-0000-0000-000000000000"
FAKE_JOB_RESPONSE=$(curl -s -w "%{http_code}" -o /dev/null "$ASYNC_ENQUEUER/jobs/$FAKE_JOB_ID")
if [ "$FAKE_JOB_RESPONSE" = "404" ]; then
    print_result 0 "Non-existent job correctly returns 404"
else
    print_result 1 "Non-existent job handling failed"
fi

# Test 8: Async metrics endpoint
echo "8. Testing async metrics endpoint..."
ASYNC_METRICS=$(curl -s "$ASYNC_ENQUEUER/metrics")
if echo "$ASYNC_METRICS" | grep -q "enqueuer_async_jobs_created_total" && \
   echo "$ASYNC_METRICS" | grep -q "enqueuer_async_jobs_completed_total"; then
    print_result 0 "Async metrics endpoint working"
else
    print_result 1 "Async metrics endpoint failed"
fi

# Test 9: Async GET should return 405 (only POST allowed)
echo "9. Testing async GET request (should be rejected)..."
ASYNC_GET_RESPONSE=$(curl -s -w "%{http_code}" -o /dev/null "$ASYNC_ENQUEUER/$HTTPBIN_SERVICE")
if [ "$ASYNC_GET_RESPONSE" = "405" ]; then
    print_result 0 "Async GET correctly rejected (405 Method Not Allowed)"
else
    print_result 1 "Async GET rejection failed"
fi

echo -e "\n${YELLOW}🔄 Testing Async Job Lifecycle with Test Service${NC}"
echo "----------------------------------------"

# Test 10: Create async job with test service (has processing delay)
echo "10. Creating async job with test service..."
LIFECYCLE_JOB_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/lifecycle_job_body.txt \
    -X POST -H "Content-Type: application/json" -d '{"test": "lifecycle test", "timestamp": "'$(date -Iseconds)'"}' \
    "$ASYNC_ENQUEUER/$TEST_SERVICE")

if [ "$LIFECYCLE_JOB_RESPONSE" = "202" ]; then
    LIFECYCLE_JOB_ID=$(cat /tmp/lifecycle_job_body.txt | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)
    if [ -n "$LIFECYCLE_JOB_ID" ]; then
        print_result 0 "Async job with test service created (Job ID: $LIFECYCLE_JOB_ID)"
    else
        print_result 1 "Async job creation failed - no job ID returned"
    fi
else
    print_result 1 "Async job creation failed (HTTP $LIFECYCLE_JOB_RESPONSE)"
fi

# Test 11: Check job status immediately (should be pending or completed)
echo "11. Checking job status immediately..."
IMMEDIATE_STATUS_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/immediate_status_body.txt \
    "$ASYNC_ENQUEUER/jobs/$LIFECYCLE_JOB_ID")

if [ "$IMMEDIATE_STATUS_RESPONSE" = "200" ]; then
    IMMEDIATE_STATUS=$(cat /tmp/immediate_status_body.txt | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
    print_result 0 "Job status check successful (Status: $IMMEDIATE_STATUS)"
else
    print_result 1 "Job status check failed (HTTP $IMMEDIATE_STATUS_RESPONSE)"
fi

# Test 12: Wait for processing time and check if job completed
echo "12. Waiting ${TEST_PROCESSING_TIME} seconds for job processing..."
sleep $((TEST_PROCESSING_TIME + 1))  # Wait processing time + 1 second buffer

FINAL_STATUS_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/final_status_body.txt \
    "$ASYNC_ENQUEUER/jobs/$LIFECYCLE_JOB_ID")

if [ "$FINAL_STATUS_RESPONSE" = "200" ]; then
    FINAL_STATUS=$(cat /tmp/final_status_body.txt | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
    
    if [ "$FINAL_STATUS" = "completed" ]; then
        # Check if the result contains our test data
        if grep -q "lifecycle test" /tmp/final_status_body.txt; then
            print_result 0 "Async job completed successfully with correct result (Status: $FINAL_STATUS)"
        else
            print_result 1 "Async job completed but result is incorrect"
        fi
    elif [ "$FINAL_STATUS" = "pending" ]; then
        print_result 1 "Job still pending after processing time - may indicate processing issue"
    else
        print_result 1 "Unexpected job status: $FINAL_STATUS"
    fi
else
    print_result 1 "Final job status check failed (HTTP $FINAL_STATUS_RESPONSE)"
fi

echo -e "\n${YELLOW}🔐 Testing Authentication for Async Jobs${NC}"
echo "----------------------------------------"

# Test 13: Create authenticated job with auth token
echo "13. Creating authenticated async job..."
AUTH_TOKEN="Bearer test-user-123"
echo "   → POST $ASYNC_ENQUEUER/$HTTPBIN_SERVICE"
echo "   → Headers: Content-Type: application/json, Authorization: $AUTH_TOKEN"
echo "   → Body: {\"test\": \"authenticated request\", \"user\": \"test-user-123\"}"
AUTH_JOB_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/auth_job_body.txt \
    -X POST -H "Content-Type: application/json" -H "Authorization: $AUTH_TOKEN" \
    -d '{"test": "authenticated request", "user": "test-user-123"}' \
    "$ASYNC_ENQUEUER/$HTTPBIN_SERVICE")

if [ "$AUTH_JOB_RESPONSE" = "202" ]; then
    AUTH_JOB_ID=$(cat /tmp/auth_job_body.txt | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)
    echo "   ← HTTP $AUTH_JOB_RESPONSE ($(cat /tmp/auth_job_body.txt))"
    if [ -n "$AUTH_JOB_ID" ]; then
        print_result 0 "Authenticated async job created (Job ID: $AUTH_JOB_ID)"
    else
        print_result 1 "Authenticated job creation failed - no job ID returned"
    fi
else
    echo "   ← HTTP $AUTH_JOB_RESPONSE"
    print_result 1 "Authenticated job creation failed (HTTP $AUTH_JOB_RESPONSE)"
fi

# Test 14: Access job with correct auth token
echo "14. Accessing job with correct auth token..."
sleep 1  # Brief wait for processing
echo "   → GET $ASYNC_ENQUEUER/jobs/$AUTH_JOB_ID"
echo "   → Headers: Authorization: $AUTH_TOKEN"
CORRECT_AUTH_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/correct_auth_body.txt \
    -H "Authorization: $AUTH_TOKEN" \
    "$ASYNC_ENQUEUER/jobs/$AUTH_JOB_ID")

if [ "$CORRECT_AUTH_RESPONSE" = "200" ]; then
    AUTH_STATUS=$(cat /tmp/correct_auth_body.txt | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
    echo "   ← HTTP $CORRECT_AUTH_RESPONSE (Status: $AUTH_STATUS, auth token matched)"
    print_result 0 "Job access successful with correct auth token (Status: $AUTH_STATUS)"
else
    echo "   ← HTTP $CORRECT_AUTH_RESPONSE"
    print_result 1 "Job access failed with correct auth token (HTTP $CORRECT_AUTH_RESPONSE)"
fi

# Test 15: Access job with wrong auth token (should fail)
echo "15. Accessing job with wrong auth token..."
WRONG_AUTH_TOKEN="Bearer wrong-user-456"
echo "   → GET $ASYNC_ENQUEUER/jobs/$AUTH_JOB_ID"
echo "   → Headers: Authorization: $WRONG_AUTH_TOKEN (incorrect token)"
WRONG_AUTH_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/wrong_auth_body.txt \
    -H "Authorization: $WRONG_AUTH_TOKEN" \
    "$ASYNC_ENQUEUER/jobs/$AUTH_JOB_ID")

if [ "$WRONG_AUTH_RESPONSE" = "403" ]; then
    echo "   ← HTTP $WRONG_AUTH_RESPONSE (Forbidden: auth token mismatch)"
    print_result 0 "Job access correctly denied with wrong auth token (403 Forbidden)"
else
    echo "   ← HTTP $WRONG_AUTH_RESPONSE (Expected 403 Forbidden)"
    print_result 1 "Job access should have been denied with wrong auth token (Got HTTP $WRONG_AUTH_RESPONSE)"
fi

# Test 16: Access job without auth token (should fail)
echo "16. Accessing job without auth token..."
echo "   → GET $ASYNC_ENQUEUER/jobs/$AUTH_JOB_ID"
echo "   → Headers: (no Authorization header)"
NO_AUTH_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/no_auth_body.txt \
    "$ASYNC_ENQUEUER/jobs/$AUTH_JOB_ID")

if [ "$NO_AUTH_RESPONSE" = "403" ]; then
    echo "   ← HTTP $NO_AUTH_RESPONSE (Forbidden: missing auth token)"
    print_result 0 "Job access correctly denied without auth token (403 Forbidden)"
else
    echo "   ← HTTP $NO_AUTH_RESPONSE (Expected 403 Forbidden)"
    print_result 1 "Job access should have been denied without auth token (Got HTTP $NO_AUTH_RESPONSE)"
fi

# Test 17: Create unauthenticated job and access it (should work)
echo "17. Creating and accessing unauthenticated job..."
echo "   → POST $ASYNC_ENQUEUER/$HTTPBIN_SERVICE"
echo "   → Headers: Content-Type: application/json (no Authorization header)"
echo "   → Body: {\"test\": \"unauthenticated request\"}"
UNAUTH_JOB_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/unauth_job_body.txt \
    -X POST -H "Content-Type: application/json" \
    -d '{"test": "unauthenticated request"}' \
    "$ASYNC_ENQUEUER/$HTTPBIN_SERVICE")

if [ "$UNAUTH_JOB_RESPONSE" = "202" ]; then
    UNAUTH_JOB_ID=$(cat /tmp/unauth_job_body.txt | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)
    echo "   ← HTTP $UNAUTH_JOB_RESPONSE (Job ID: $UNAUTH_JOB_ID)"
    
    # Try to access the unauthenticated job
    sleep 1  # Brief wait for processing
    echo "   → GET $ASYNC_ENQUEUER/jobs/$UNAUTH_JOB_ID"
    echo "   → Headers: (no Authorization header)"
    UNAUTH_ACCESS_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/unauth_access_body.txt \
        "$ASYNC_ENQUEUER/jobs/$UNAUTH_JOB_ID")
    
    if [ "$UNAUTH_ACCESS_RESPONSE" = "200" ]; then
        echo "   ← HTTP $UNAUTH_ACCESS_RESPONSE (job accessible without auth)"
        print_result 0 "Unauthenticated job access successful (no auth required)"
    else
        echo "   ← HTTP $UNAUTH_ACCESS_RESPONSE"
        print_result 1 "Unauthenticated job access failed (HTTP $UNAUTH_ACCESS_RESPONSE)"
    fi
else
    echo "   ← HTTP $UNAUTH_JOB_RESPONSE"
    print_result 1 "Unauthenticated job creation failed (HTTP $UNAUTH_JOB_RESPONSE)"
fi

echo -e "\n${YELLOW}🔒 Testing Enforced Authentication Mode${NC}"
echo "----------------------------------------"

# Test 18: Create unauthenticated job with auth enforcement (should fail)
echo "18. Testing enforced auth mode - unauthenticated job creation should fail..."
echo "   → Restarting async enqueuer with ASYNC_REQUIRE_AUTH=true"
docker compose stop enqueuer-async > /dev/null 2>&1
ASYNC_REQUIRE_AUTH=true docker compose up -d enqueuer-async > /dev/null 2>&1
sleep 3  # Wait for service to be ready

echo "   → POST $ASYNC_ENQUEUER/$HTTPBIN_SERVICE"
echo "   → Headers: Content-Type: application/json (no Authorization header)"
echo "   → Body: {\"test\": \"should be rejected\"}"
ENFORCED_UNAUTH_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/enforced_unauth_body.txt \
    -X POST -H "Content-Type: application/json" \
    -d '{"test": "should be rejected"}' \
    "$ASYNC_ENQUEUER/$HTTPBIN_SERVICE")

if [ "$ENFORCED_UNAUTH_RESPONSE" = "401" ]; then
    echo "   ← HTTP $ENFORCED_UNAUTH_RESPONSE (Unauthorized: auth required)"
    print_result 0 "Enforced auth mode correctly rejects unauthenticated requests (401 Unauthorized)"
else
    echo "   ← HTTP $ENFORCED_UNAUTH_RESPONSE (Expected 401 Unauthorized)"
    print_result 1 "Enforced auth mode should reject unauthenticated requests (Got HTTP $ENFORCED_UNAUTH_RESPONSE)"
fi

# Test 19: Create authenticated job with auth enforcement (should succeed)
echo "19. Testing enforced auth mode - authenticated job creation should succeed..."
ENFORCED_AUTH_TOKEN="Bearer enforced-user-789"
echo "   → POST $ASYNC_ENQUEUER/$HTTPBIN_SERVICE"
echo "   → Headers: Content-Type: application/json, Authorization: $ENFORCED_AUTH_TOKEN"
echo "   → Body: {\"test\": \"should be accepted\"}"
ENFORCED_AUTH_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/enforced_auth_body.txt \
    -X POST -H "Content-Type: application/json" -H "Authorization: $ENFORCED_AUTH_TOKEN" \
    -d '{"test": "should be accepted"}' \
    "$ASYNC_ENQUEUER/$HTTPBIN_SERVICE")

if [ "$ENFORCED_AUTH_RESPONSE" = "202" ]; then
    ENFORCED_JOB_ID=$(cat /tmp/enforced_auth_body.txt | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)
    echo "   ← HTTP $ENFORCED_AUTH_RESPONSE (Job ID: $ENFORCED_JOB_ID)"
    print_result 0 "Enforced auth mode allows authenticated requests (200 OK)"
else
    echo "   ← HTTP $ENFORCED_AUTH_RESPONSE"
    print_result 1 "Enforced auth mode should allow authenticated requests (Got HTTP $ENFORCED_AUTH_RESPONSE)"
fi

# Test 20: Verify job access still works correctly in enforced mode
echo "20. Testing enforced auth mode - job access with correct token..."
sleep 1  # Wait for processing
echo "   → GET $ASYNC_ENQUEUER/jobs/$ENFORCED_JOB_ID"
echo "   → Headers: Authorization: $ENFORCED_AUTH_TOKEN"
ENFORCED_ACCESS_RESPONSE=$(curl -s -w "%{http_code}" -o /tmp/enforced_access_body.txt \
    -H "Authorization: $ENFORCED_AUTH_TOKEN" \
    "$ASYNC_ENQUEUER/jobs/$ENFORCED_JOB_ID")

if [ "$ENFORCED_ACCESS_RESPONSE" = "200" ]; then
    ENFORCED_STATUS=$(cat /tmp/enforced_access_body.txt | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
    echo "   ← HTTP $ENFORCED_ACCESS_RESPONSE (Status: $ENFORCED_STATUS)"
    print_result 0 "Enforced auth mode job access successful with correct token"
else
    echo "   ← HTTP $ENFORCED_ACCESS_RESPONSE"
    print_result 1 "Enforced auth mode job access failed (HTTP $ENFORCED_ACCESS_RESPONSE)"
fi

# Restore normal mode for cleanup
echo "   → Restoring async enqueuer to normal mode (ASYNC_REQUIRE_AUTH=false)"
docker compose stop enqueuer-async > /dev/null 2>&1
docker compose up -d enqueuer-async > /dev/null 2>&1
sleep 2

echo -e "\n${YELLOW}📊 Final Metrics Summary${NC}"
echo "----------------------------------------"

# Display key metrics
echo "Sync Enqueuer Metrics:"
curl -s "$SYNC_ENQUEUER/metrics" | grep "enqueuer_requests_total\|enqueuer_response_codes_total" | head -6

echo -e "\nAsync Enqueuer Metrics:"
curl -s "$ASYNC_ENQUEUER/metrics" | grep "enqueuer_async_jobs\|enqueuer_response_codes_total.*202" | head -4

# Cleanup temp files
rm -f /tmp/sync_get_body.txt /tmp/sync_post_body.txt /tmp/sync_httpbin_body.txt /tmp/async_create_body.txt /tmp/async_status_body.txt \
      /tmp/lifecycle_job_body.txt /tmp/immediate_status_body.txt /tmp/final_status_body.txt \
      /tmp/auth_job_body.txt /tmp/correct_auth_body.txt /tmp/wrong_auth_body.txt /tmp/no_auth_body.txt \
      /tmp/unauth_job_body.txt /tmp/unauth_access_body.txt \
      /tmp/enforced_unauth_body.txt /tmp/enforced_auth_body.txt /tmp/enforced_access_body.txt

echo -e "\n${GREEN}🎉 All tests passed! Both sync and async enqueuers are working correctly.${NC}"
echo -e "${BLUE}Summary:${NC}"
echo "  - Sync mode: Direct request/response on port 8000"
echo "  - Async mode: Job creation and status retrieval on port 8100"
echo "  - Both modes properly integrate with RabbitMQ, nginx, httpbin, and test services"
echo "  - POST requests working correctly with httpbin echo service"
echo "  - Async job lifecycle tested with test service (with processing delay)"
echo "  - Authentication: Auth headers protect job access (configurable header name)"
echo "  - Security: Jobs can only be accessed by users with matching auth tokens"
echo "  - Enforced Auth Mode: ASYNC_REQUIRE_AUTH setting prevents anonymous job creation"
echo "  - Flexible Auth: Supports both anonymous and authenticated workflows"
echo "  - Metrics collection working for both modes"