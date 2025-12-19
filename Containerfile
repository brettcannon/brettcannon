FROM python:3.13-slim

# Create buildbot user with a proper home directory
RUN useradd -m -d /home/buildbot -s /bin/bash buildbot

# Create buildarea directory with world readable and writable permissions
RUN mkdir -p /buildarea && \
    chmod 0777 /buildarea

# Install buildbot-worker
RUN pip install --no-cache-dir buildbot-worker

# Copy entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Set working directory
WORKDIR /buildarea

# Switch to buildbot user
USER buildbot

# Set home directory environment variable
ENV HOME=/home/buildbot

# Use entrypoint script
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Default command - can be overridden
CMD []
