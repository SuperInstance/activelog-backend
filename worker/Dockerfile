# Base Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy worker code
COPY . .

# Optional: expose a port if worker has metrics/monitoring endpoint
# EXPOSE 9000

# Default command to run worker script
# Replace worker.py with your actual worker entrypoint file
CMD ["python", "worker.py"]
