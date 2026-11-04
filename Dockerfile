FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV HOST=0.0.0.0 PORT=8080 DATA_DIR=/data PYTHONUNBUFFERED=1
RUN mkdir /data && useradd --uid 10001 --no-create-home forms && chown forms /data
USER forms
VOLUME ["/data"]
EXPOSE 8080
CMD ["python", "-m", "app.server"]
