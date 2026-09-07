# Imagen de la aplicacion (FastAPI + Uvicorn).
FROM python:3.12-slim

# Sin .pyc y con salida sin buffer, para ver los logs en vivo en docker compose.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Las dependencias van primero: si no cambian, Docker reutiliza la capa.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

# La app no corre como root (seccion 8: seguridad por diseno).
RUN useradd --create-home --shell /usr/sbin/nologin monitor && \
    chown -R monitor:monitor /app
USER monitor

# Los modulos de backend/ se importan planos (from reglas import ...).
WORKDIR /app/backend

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
