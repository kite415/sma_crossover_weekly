FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ bot/
COPY watchlist.txt* ./

# State lives in /data (mount a volume so the DB survives container rebuilds)
ENV DB_PATH=/data/bot.db
RUN mkdir -p /data
VOLUME /data

CMD ["python", "-m", "bot.main"]
