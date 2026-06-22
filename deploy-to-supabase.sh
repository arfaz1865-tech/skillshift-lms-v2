#!/bin/bash
set -euo pipefail

# Script to deploy Prisma schema to Supabase

echo "🚀 Deploying Prisma schema to Supabase..."
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found!"
    echo "Please create a .env file with your DATABASE_URL"
    echo "See .env.example or SUPABASE_SETUP.md for more info"
    exit 1
fi

# Check if DATABASE_URL is set
if ! grep -q "DATABASE_URL" .env; then
    echo "❌ Error: DATABASE_URL not found in .env file!"
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    uv venv
fi

echo "📦 Installing Python dependencies..."
source .venv/bin/activate

echo "🔧 Generating Prisma Client..."
npx -y prisma@5.17.0 generate

echo "🚀 Pushing schema to Supabase..."
npx -y prisma@5.17.0 db push --schema prisma/schema.prisma

echo "✅ Schema deployment complete."
