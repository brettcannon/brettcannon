# Containerfile for Podman buildbot-worker
# This replaces Docker support and focuses on Podman

FROM python:3.13-slim

# Install buildbot-worker and dependencies
RUN pip install --no-cache-dir buildbot-worker

# Create a dedicated buildbot user with a proper home directory
RUN useradd -m -d /home/buildbot -s /bin/bash buildbot

# Create buildarea directory with world readable and writable permissions
RUN mkdir -p /buildarea && chmod 0777 /buildarea

# Set the working directory
WORKDIR /buildarea

# Switch to the buildbot user
USER buildbot

# Set the home directory environment variable
ENV HOME=/home/buildbot

# Default command to run buildbot-worker
CMD ["buildbot-worker", "start", "--nodaemon", "/buildarea"]
