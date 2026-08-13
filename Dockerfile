FROM python:3-alpine

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir .

VOLUME [ "/pictures" ]

ENV FAMLY_PICTURES_FOLDER=/pictures

ENTRYPOINT [ "famly-fetch" ]
