FROM ubuntu:22.04

# Set non-interactive frontend to avoid prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Berlin

# Define Binder-required user variables
ARG NB_USER=jovyan
ARG NB_UID=1000
ENV USER ${NB_USER}
ENV NB_UID ${NB_UID}
ENV HOME /home/${NB_USER}

# Install necessary system dependencies
RUN apt-get update && apt-get install -y \
    software-properties-common \
    python3 \
    python3-pip
   
# Install scorep from PPA 
RUN add-apt-repository -y ppa:score-p/releases \
    && apt-get update \
    && apt-get install -y scorep

# Upgrade pip and install base Python dependencies
RUN pip3 install pip setuptools wheel

# Install Score-P Python bindings
RUN pip3 install --no-cache-dir scorep

# Install JUmPER kernel
RUN pip3 install --no-cache-dir jumper-kernel && \
    python3 -m jumper.install

# Create jovyan user with UID 1000 (Binder requirement)
RUN adduser --disabled-password \
    --gecos "Default user" \
    --uid ${NB_UID} \
    ${NB_USER}

# Copy repository contents to the user's home directory
WORKDIR ${HOME}
COPY . ${HOME}

# Change ownership of the directory to the created user
USER root
RUN chown -R ${NB_UID} ${HOME}
USER ${NB_USER}

# Ensure the PATH includes the correct Python location
ENV PATH="${HOME}/.local/bin:${PATH}"

# Install project dependencies from pyproject.toml using pip
RUN pip install --no-cache-dir .

# Install JupyterLab and notebook
RUN pip install --no-cache-dir jupyterlab notebook

# Expose Jupyter Notebook port
EXPOSE 8888

# Set the default command for running Jupyter Notebook
CMD ["jupyter", "notebook", "--NotebookApp.default_url=/lab", "--ip=0.0.0.0", "--port=8888", "--no-browser"]
