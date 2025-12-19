# Containerfile for Podman buildbot-worker
# This replaces Docker support and focuses on Podman

FROM python:3.13-slim

# Install buildbot-worker and dependencies
RUN pip install --no-cache-dir buildbot-worker==4.3.0

# Create a dedicated buildbot user with a proper home directory
RUN useradd -m -d /home/buildbot -s /bin/bash buildbot

# Create buildarea directory with world readable and writable permissions
# Note: 0777 permissions are used to handle permission issues with volume mounts
# and different user contexts, as specified in the requirements
RUN mkdir -p /buildarea && chmod 0777 /buildarea

# Set the working directory
WORKDIR /buildarea

# Switch to the buildbot user
USER buildbot

# Set the home directory environment variable
ENV HOME=/home/buildbot

# Default command to run buildbot-worker
CMD ["buildbot-worker", "start", "--nodaemon", "/buildarea"]
