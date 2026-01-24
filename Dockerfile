FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY converter.py .

# Run the bot
CMD ["python", "-u", "converter.py"]
```

### .dockerignore:
```
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
*.so
*.egg
*.egg-info/
dist/
build/
.env.local
.env.*.local
.git/
.gitignore
README.md
.DS_Store
