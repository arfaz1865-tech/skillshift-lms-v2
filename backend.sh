#!/bin/bash

# Physiotherapy Portal - Backend Start Script
# This script starts the Supabase local development environment

cd "$(dirname "$0")" || exit 1

echo "🚀 Starting Supabase Local Development..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check if Docker is running
if ! docker ps &> /dev/null; then
    echo "❌ Docker is not running. Please start Docker first."
    exit 1
fi

echo "✅ Docker is running"

# Start Supabase (excluding vector service to avoid timeout issues)
echo "⏳ Starting Supabase services (this may take 30-60 seconds)..."
npx supabase start -x vector

if [ $? -eq 0 ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "✅ Supabase is running!"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "📊 Access Points:"
    echo "   • Studio:       http://127.0.0.1:54323"
    echo "   • API:          http://127.0.0.1:54321"
    echo "   • Database:     postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    echo ""
    echo "🔑 Use these credentials for frontend development:"
    echo "   • SUPABASE_URL:     http://127.0.0.1:54321"
    echo "   • SUPABASE_ANON_KEY: sb_publishable_ACJWlzQHlZjBrEguHvfOxg_3BJgxAaH"
    echo ""
    echo "To stop Supabase, run: npm run supabase:stop (from server directory)"
    echo ""
else
    echo ""
    echo "❌ Failed to start Supabase"
    echo "Troubleshooting:"
    echo "  1. Ensure Docker is running"
    echo "  2. Check if ports 54321-54324 are available"
    echo "  3. Try: npx supabase stop && rm -rf .supabase/docker.json"
    exit 1
fi