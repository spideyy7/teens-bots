FROM python:3.11-slim

# Install ffmpeg for fast audio conversion & voice recognition
RUN apt-get update && apt-get install -y --no-install-recommends     ffmpeg     curl     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
EXPOSE 10000

CMD ["python", "-u", "run_render.py"]
