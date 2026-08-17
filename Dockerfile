FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY .env ./
EXPOSE 8080
ENV FLASK_APP=app/main.py
CMD ["python", "app/main.py"]
