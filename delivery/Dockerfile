FROM python:3.12-slim
WORKDIR /app
COPY delivery/requirements.txt /app/delivery/requirements.txt
RUN pip install --no-cache-dir -r delivery/requirements.txt && useradd --uid 10001 --create-home delivery
COPY delivery/server.py /app/delivery/server.py
COPY delivery/templates /app/delivery/templates
COPY delivery/assets /app/delivery/assets
USER delivery
ENV DELIVERY_CATALOG=/data/catalog.json DELIVERY_DB=/data/purchases.sqlite PAYMENT_MODE=test
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "--timeout", "120", "delivery.server:create_app()"]
