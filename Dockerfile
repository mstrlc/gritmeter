# my.gritmeter.app: server.py and its worker. Standard library only, so no pip step.
# data/ (database, OSM tiles) is mounted at run time; secrets come from the environment, never the image.
FROM python:3.12-slim

WORKDIR /app
COPY *.py ./
COPY site/strava/ site/strava/

ENV PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8722
EXPOSE 8722
HEALTHCHECK --interval=60s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8722/health', timeout=4)"

CMD ["python", "server.py"]
