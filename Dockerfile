FROM python:3.12-slim-bookworm
RUN mkdir /app && chmod 777 /app
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
RUN apt -qq update && apt -qq install -y git python3 python3-pip ffmpeg
COPY . .
RUN pip3 install -r requirements.txt
CMD gunicorn app:app & python -m main
