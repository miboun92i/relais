FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PORT=8787
WORKDIR /app
COPY index.html app.js commercial.js style.css server.py ./
EXPOSE 8787
CMD ["python", "server.py"]
