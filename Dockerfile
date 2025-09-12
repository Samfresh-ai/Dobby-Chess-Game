FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt && chmod +x stockfish-ubuntu-x86-64
CMD ["gunicorn", "app:app", "-w", "1", "-k", "eventlet", "-b", "0.0.0.0:10000"]
