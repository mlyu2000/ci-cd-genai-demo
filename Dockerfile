FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /repo
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tests ./tests
ENV FLASK_APP=app/main.py
ENV GIT_PYTHON_REFRESH=quiet
EXPOSE 8080
CMD ["python", "app/main.py"]
