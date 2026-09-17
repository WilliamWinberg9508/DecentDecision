FROM python:3.12-slim

WORKDIR /srv
RUN pip install --no-cache-dir \
        fastapi==0.115.6 \
        "uvicorn[standard]==0.34.0" \
        "psycopg[binary,pool]==3.2.3" \
        "pydantic[email]==2.10.4" \
        jinja2==3.1.5 \
        python-multipart==0.0.20

COPY schema.sql ./schema.sql
COPY app ./app

EXPOSE 8000
# One worker handled ~300 req/s against a million-vote database in testing;
# four gives headroom and survives a worker dying. Each opens its own pool
# (max 10), so this is 40 Postgres connections at full stretch.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
