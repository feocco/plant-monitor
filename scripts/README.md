# Scripts

## Install locally

```bash
scripts/install_local.sh
```

The script creates `.venv`, installs the package, and prints the activation
command needed for `plant` to be available on your shell `PATH`.

## Build locally

```bash
scripts/build_image.sh
```

Override the image name:

```bash
IMAGE_NAME=plant-monitor:dev scripts/build_image.sh
```

## Deploy to a remote Docker host

This deploys the working directory over SSH and runs `docker compose up -d --build`
on the remote host. It transfers local `.env` and `plants.yaml` to the remote
server, but those files are ignored by git.

```bash
REMOTE_DOCKER_HOST=user@nas.local \
REMOTE_APP_DIR=/opt/plant-monitor \
scripts/deploy_remote.sh
```

Remote requirements:

- SSH access from this Mac
- Docker and Docker Compose plugin installed on the remote host
- permission for the SSH user to run Docker
