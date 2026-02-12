#!/bin/bash
# Complete test flow for Disaster Reporting API
# Run this after starting the server

set -e  # Exit on any error

BASE_URL="http://localhost:8000"
echo "🧪 Testing Disaster Reporting API"
echo "=================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Step 1: Check server is running
echo -e "${BLUE}Step 1: Checking server health...${NC}"
HEALTH=$(curl -s $BASE_URL/health)
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Server is running!${NC}"
    echo "$HEALTH" | jq '.'
else
    echo -e "${RED}❌ Server not running. Start it first with:${NC}"
    echo "   python -m uvicorn app.main:app --reload"
    exit 1
fi
echo ""

# Step 2: Register a test user
echo -e "${BLUE}Step 2: Registering test user...${NC}"
PHONE="+353871234567"

REGISTER_RESPONSE=$(curl -s -X POST $BASE_URL/api/v1/auth/users/register \
  -H "Content-Type: application/json" \
  -d "{
    \"phone_number\": \"$PHONE\",
    \"full_name\": \"Test User\",
    \"email\": \"testuser@example.com\"
  }")

echo "$REGISTER_RESPONSE" | jq '.'

# Check if OTP was sent
if echo "$REGISTER_RESPONSE" | jq -e '.message' > /dev/null; then
    echo -e "${GREEN}✅ OTP sent!${NC}"
else
    echo -e "${RED}❌ Registration failed${NC}"
    exit 1
fi
echo ""

# Step 3: Get OTP from logs (in real app, user gets SMS)
echo -e "${BLUE}Step 3: Checking logs for OTP...${NC}"
echo -e "${RED}⚠️  In production, user gets OTP via SMS${NC}"
echo -e "${RED}⚠️  For testing, check app logs or use: 123456${NC}"
echo ""
echo "Enter the OTP (or press Enter for '123456' if in dev mode): "
read -r OTP
OTP=${OTP:-123456}
echo ""

# Step 4: Verify OTP and get session token
echo -e "${BLUE}Step 4: Verifying OTP and getting session token...${NC}"
VERIFY_RESPONSE=$(curl -s -X POST $BASE_URL/api/v1/auth/users/register/verify \
  -H "Content-Type: application/json" \
  -d "{
    \"phone_number\": \"$PHONE\",
    \"otp\": \"$OTP\"
  }")

echo "$VERIFY_RESPONSE" | jq '.'

# Extract session token
SESSION_TOKEN=$(echo "$VERIFY_RESPONSE" | jq -r '.session_token // .access_token // empty')

if [ -z "$SESSION_TOKEN" ] || [ "$SESSION_TOKEN" == "null" ]; then
    echo -e "${RED}❌ Failed to get session token${NC}"
    echo "Response was: $VERIFY_RESPONSE"
    exit 1
fi

echo -e "${GREEN}✅ Got session token: ${SESSION_TOKEN:0:20}...${NC}"
echo ""

# Step 5: Report a disaster
echo -e "${BLUE}Step 5: Reporting a disaster...${NC}"
DISASTER_RESPONSE=$(curl -s -X POST $BASE_URL/api/v1/disasters/report \
  -H "Authorization: Bearer $SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "location_lat": 53.3498,
    "location_lng": -6.2603,
    "disaster_type": "fire",
    "severity": "critical",
    "description": "Large building fire in Dublin city center. Multiple floors affected with heavy smoke visible from several blocks away.",
    "image_urls": [
      "https://example.com/fire-image-1.jpg",
      "https://example.com/fire-image-2.jpg"
    ]
  }')

echo "$DISASTER_RESPONSE" | jq '.'

# Check if disaster was created
DISASTER_ID=$(echo "$DISASTER_RESPONSE" | jq -r '.id // empty')

if [ -z "$DISASTER_ID" ] || [ "$DISASTER_ID" == "null" ]; then
    echo -e "${RED}❌ Failed to create disaster${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Disaster reported successfully!${NC}"
echo -e "${GREEN}   Disaster ID: $DISASTER_ID${NC}"
echo -e "${GREEN}   Status: $(echo "$DISASTER_RESPONSE" | jq -r '.status')${NC}"
echo -e "${GREEN}   ERT Notified: $(echo "$DISASTER_RESPONSE" | jq -r '.ert_notified')${NC}"
echo ""

# Step 6: Test different disaster types
echo -e "${BLUE}Step 6: Testing other disaster types...${NC}"

echo "📍 Reporting FLOOD (medium severity)..."
curl -s -X POST $BASE_URL/api/v1/disasters/report \
  -H "Authorization: Bearer $SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "location_lat": 53.3420,
    "location_lng": -6.2500,
    "disaster_type": "flood",
    "severity": "medium",
    "description": "Street flooding on O Connell Street. Water level approximately 30cm deep."
  }' | jq -c '{id, disaster_type, severity, status}'

echo ""
echo "🚑 Reporting MEDICAL emergency (high severity)..."
curl -s -X POST $BASE_URL/api/v1/disasters/report \
  -H "Authorization: Bearer $SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "location_lat": 53.3380,
    "location_lng": -6.2591,
    "disaster_type": "medical",
    "severity": "high",
    "description": "Multiple casualties at Trinity College. Medical assistance urgently needed."
  }' | jq -c '{id, disaster_type, severity, status}'

echo ""
echo "🚗 Reporting ACCIDENT (low severity)..."
curl -s -X POST $BASE_URL/api/v1/disasters/report \
  -H "Authorization: Bearer $SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "location_lat": 53.3450,
    "location_lng": -6.2400,
    "disaster_type": "accident",
    "severity": "low",
    "description": "Minor car accident on Grafton Street. No injuries reported. Traffic disrupted."
  }' | jq -c '{id, disaster_type, severity, status}'

echo ""
echo -e "${GREEN}=================================="
echo -e "✅ All tests completed successfully!"
echo -e "=================================="${NC}
echo ""
echo "📊 Summary:"
echo "  - Registered user: $PHONE"
echo "  - Session token: ${SESSION_TOKEN:0:30}..."
echo "  - Disasters reported: 4"
echo ""
echo "🌐 View interactive docs at: $BASE_URL/docs"
