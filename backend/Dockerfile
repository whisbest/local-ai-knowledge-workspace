FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce
WORKDIR /app
COPY requirements-lock.txt .
RUN pip install --no-cache-dir --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-lock.txt
COPY app ./app
COPY migrations ./migrations
ENV DATA_ROOT=/data PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:application", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
