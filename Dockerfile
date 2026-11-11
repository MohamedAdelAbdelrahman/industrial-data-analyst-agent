# Image for the agent: Streamlit UI + LangGraph agent + the MCP server (a subprocess of the agent).
# Used by docker-compose.yml; run_python's sandbox has its own image in sandbox/Dockerfile.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # bge-small (manual search) is downloaded once into the data volume, not into the image
    HF_HOME=/ida-data/hf

WORKDIR /app

# 1) CPU-only torch first: sentence-transformers would otherwise pull the CUDA build (~3 GB more).
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

# 2) Dependencies before the source code, so editing src/ does not reinstall them (layer cache).
#    The stub package lets the editable install resolve; the real code is copied in step 3.
COPY pyproject.toml ./
RUN mkdir -p src/ida && touch src/ida/__init__.py \
 && pip install -e ".[rag,sandbox,obs,ui]"

# 3) Code and the files the app reads from the repo (schema, manuals, data generator).
COPY src ./src
COPY data/schema.sql data/generate_db.py ./data/
COPY data/manuals ./data/manuals

EXPOSE 8501
CMD ["streamlit", "run", "src/ida/ui/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
