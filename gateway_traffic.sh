#!/bin/bash
# Usage:  ./traffic.sh                         -> overview of the last 24 h, then the last 60 minutes
#         ./traffic.sh START END               -> then that window, minute by minute
#         e.g. ./traffic.sh 2026-10-28T11:10:00Z 2026-10-28T11:40:00Z      (times are UTC)
API=${API:-a1y7vmqtab}; STAGE=${STAGE:-staging}; FN=${FN:-broker-firms-api}; REGION=${REGION:-ap-southeast-1}
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
START=${1:-$(date -u -d '60 minutes ago' +%Y-%m-%dT%H:%M:%SZ)}; END=${2:-$NOW}

show() {   # namespace metric statistic period start end dimensions...
  local ns=$1 metric=$2 stat=$3 period=$4 start=$5 end=$6; shift 6
  local out
  out=$(aws cloudwatch get-metric-statistics --region "$REGION" --namespace "$ns" --metric-name "$metric" \
        --dimensions "$@" --start-time "$start" --end-time "$end" --period "$period" --statistics "$stat" \
        --query "sort_by(Datapoints,&Timestamp)[].[Timestamp,${stat}]" --output text 2>&1)
  echo "== $metric ($stat, every ${period}s)"
  if [ -z "$out" ]; then echo "   (no data points: no requests in this window, or wrong account/Region/names)"; else echo "$out"; fi
}

echo "Now (UTC): $NOW    Account: $(aws sts get-caller-identity --query Account --output text 2>&1)    Region: $REGION"
echo; echo "-- Is the API in this account and Region?"
aws apigatewayv2 get-api --api-id "$API" --region "$REGION" --query "[Name,ProtocolType]" --output text 2>&1
echo; echo "-- Which stages does it have? (the script uses: $STAGE)"
aws apigatewayv2 get-stages --api-id "$API" --region "$REGION" --query "Items[].StageName" --output text 2>&1
echo; echo "-- Which metrics does CloudWatch hold for it?"
aws cloudwatch list-metrics --region "$REGION" --namespace AWS/ApiGateway --dimensions Name=ApiId,Value="$API" \
  --query "Metrics[].MetricName" --output text 2>&1 | tr '\t' '\n' | sort -u
echo; echo "-- Requests per hour, last 24 hours (UTC): shows WHEN there was traffic"
show AWS/ApiGateway Count Sum 3600 "$(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ)" "$NOW" Name=ApiId,Value=$API Name=Stage,Value=$STAGE

echo; echo "-- Minute by minute, $START to $END"
for m in Count 4xx 5xx; do show AWS/ApiGateway $m Sum 60 "$START" "$END" Name=ApiId,Value=$API Name=Stage,Value=$STAGE; done
for m in Latency IntegrationLatency; do show AWS/ApiGateway $m Maximum 60 "$START" "$END" Name=ApiId,Value=$API Name=Stage,Value=$STAGE; done
for m in Invocations Errors Throttles; do show AWS/Lambda $m Sum 60 "$START" "$END" Name=FunctionName,Value=$FN; done
