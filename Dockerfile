FROM python:3.13-slim

WORKDIR /app

# Install system dependencies (ffmpeg, curl, unzip, git, nodejs) and deno.
# yt-dlp needs a JS runtime (node OR deno) to solve YouTube's signature
# challenges - installing BOTH means if one breaks or gets blocked, the
# other still works instead of every download failing.
RUN apt-get update -y \
    && apt-get install -y --no-install-recommends ffmpeg curl unzip git nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://deno.land/install.sh | sh

ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

# Install python dependencies from requirements.txt
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Default command uses the repo `start` script
CMD ["bash", "start"]
