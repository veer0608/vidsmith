# For Hugging Face Spaces, whose free CPU tier is 2 vCPU and 16 GB - enough to
# encode 1080p, which a 512 MB instance is not.
#
# This image is built by the Space, not on your machine. Nothing here asks you
# to run Docker locally.
FROM python:3.11-slim

# ffmpeg and the fonts come from apt here, so scripts/fetch-runtime-deps.sh is
# not needed: apt is the cheaper, more reliable source when the image has it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Spaces run as uid 1000; a root-owned checkout cannot write its own job folder
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    VIDSMITH_JOBS=/tmp/vidsmith-jobs \
    VIDSMITH_MAX_MINUTES=4

WORKDIR $HOME/app

COPY --chown=user requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir --user -r requirements-web.txt

COPY --chown=user vidsmith ./vidsmith
COPY --chown=user web ./web

# the themes name Windows families; point PIL and libass at what apt installed
RUN mkdir -p assets/fonts && \
    cp /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf assets/fonts/ && \
    cp /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf assets/fonts/

EXPOSE 7860
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "7860", \
     "--timeout-keep-alive", "120"]
