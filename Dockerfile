FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY converter.py .

# Expose health check port
EXPOSE 8000

# Run the bot
CMD ["python", "-u", "converter.py"]
