FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/criteria
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home criteria
COPY app ./app
USER criteria
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import json,os,urllib.request; r=urllib.request.Request('http://127.0.0.1:8000/health/ready', headers={'Authorization':'Bearer '+os.environ['READER_TOKEN']}); assert json.load(urllib.request.urlopen(r,timeout=3))['status']=='ready'"
CMD ["uvicorn", "app.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test
COPY tests ./tests
HEALTHCHECK NONE
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp=/tmp/criteria-tests"]
