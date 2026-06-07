# backend/Dockerfile

# Use Python 3.12 image
FROM python:3.12.6-slim

WORKDIR /app

# System dependencies (adjust if you don't need PDF processing, etc.)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpoppler-cpp-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# Make sure backend/requirements.txt exists in your repo
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source code
COPY . .

# Default port FastAPI will listen on inside the container
ENV PORT=8000
EXPOSE 8000

# Start FastAPI app
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
