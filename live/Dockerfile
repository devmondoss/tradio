# Servicio Railway: paper-trading de la estrategia de liquidez (mide fill ratio maker 24/7).
# Es un servicio SEPARADO del monitor Rust (que usa el Dockerfile raíz). En Railway:
#   New Service → Deploy from repo → Root Directory = live → (usa este Dockerfile)
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY levels.py paper_liquidity.py ./
# -u = stdout sin buffer (logs en vivo en Railway)
CMD ["python", "-u", "paper_liquidity.py"]
