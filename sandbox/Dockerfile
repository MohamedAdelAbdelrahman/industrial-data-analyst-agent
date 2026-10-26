# Image for the run_python tool. Built by: python -m ida.mcp_server.sandbox --build
FROM python:3.12-slim

RUN pip install --no-cache-dir pandas==2.2.3 numpy==2.2.6 matplotlib==3.10.3 scipy==1.15.3 \
 && useradd --uid 1000 --create-home sandbox

ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mpl \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 1000:1000
WORKDIR /out
CMD ["python", "/code/main.py"]
